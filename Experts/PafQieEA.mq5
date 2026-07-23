//+------------------------------------------------------------------+
//|                                                     PafQieEA.mq5 |
//|  PAF-QIE — Prof AF Quantum Institutional Engine                  |
//|  Continuous structure/bias tracking detects MSS (close breaks    |
//|  the most recent opposing swing) -- no external liquidity sweep  |
//|  precondition. Pools (PDH/PDL/Asia/swing H1) are TP-target       |
//|  candidates only. A local Python bridge (brain/server.py) runs   |
//|  the LLM decision brain (Minimax/Anthropic/...) for BUY/SELL/SKIP|
//|  + discretionary SL/TP. This EA never talks to the LLM directly. |
//+------------------------------------------------------------------+
#property copyright "PAF-QIE Team"
#property version   "0.30"

#include <PafQie/Types.mqh>
#include <PafQie/Structure.mqh>
#include <PafQie/Liquidity.mqh>
#include <PafQie/Brain.mqh>
#include <PafQie/Dashboard.mqh>
#include <PafQie/TradeExec.mqh>

//--- inputs
input group "=== General ==="
input long             InpMagic          = 902501;
input ENUM_TIMEFRAMES  InpEntryTF        = PERIOD_M15;   // entry timeframe
input bool             InpAutoTrade      = true;         // false = signal/alert only
input double           InpRiskPct        = 1.0;          // risk % per trade
input double           InpMaxLotRiskMult = 1.5;          // cancel if minLot risk > this x target
input double           InpMinRR          = 2.0;          // minimum reward:risk
input int              InpCooldownBars   = 12;           // bars between LLM decisions

input group "=== Structure ==="
input int              InpSwingK         = 3;            // fractal strength (bars each side)
input int              InpLookback       = 200;          // swing scan depth (entry TF)
input int              InpFvgLookback    = 20;            // bars before MSS to search for FVG (context only)
input double           InpMaxSlAtr       = 5.0;          // reject SL wider than this x ATR

input group "=== Liquidity ==="
input bool             InpUseAsia        = true;         // Asia range as pool
input int              InpAsiaStart      = 0;            // Asia start hour (server)
input int              InpAsiaEnd        = 7;            // Asia end hour (server)
input int              InpMaxSwingPools  = 6;            // H1 swing pools per side

input group "=== LLM Brain (bridge Python) ==="
input string           InpBrainUrl       = "http://127.0.0.1:8787/decide"; // brain/server.py endpoint
input int              InpMinConfidence  = 70;           // execute only at/above this (EA-side re-check)
input int              InpTimeoutMs      = 60000;        // WebRequest timeout (model reasoning perlu waktu lebih lama)
input int              InpCtxBars        = 40;           // candles sent as context
input bool             InpLogJournal     = true;         // log decisions to CSV
input bool             InpPushNotif      = false;        // push notification on decision

input group "=== Management ==="
input double           InpPartialR       = 1.5;          // partial close at this R
input double           InpPartialPct     = 50.0;         // % closed at partial

//--- globals
CPafDashboard  g_dash;
CPafExec       g_exec;
datetime       g_lastBar        = 0;
datetime       g_lastAskedMss   = 0;
datetime       g_lastDecisionAt = 0;
int            g_lastConf       = 0;
string         g_lastReason     = "";
string         g_llmStatus      = "IDLE";
string         g_symbol;

//+------------------------------------------------------------------+
int OnInit()
  {
   g_symbol = _Symbol;
   g_dash.Init(ChartID());
   g_exec.Init(InpMagic, InpPartialR, InpPartialPct);
   Print("PAF-QIE: brain bridge = ", InpBrainUrl,
        " — pastikan brain/server.py sedang jalan (python3 -m brain.server) "
        "dan URL ini di-whitelist di Allow WebRequest.");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   g_dash.Destroy();
   ObjectsDeleteAll(ChartID(), "PAFQIE_MK_");
  }

//+------------------------------------------------------------------+
bool IsNewBar()
  {
   datetime t = iTime(g_symbol, InpEntryTF, 0);
   if(t == g_lastBar)
      return false;
   g_lastBar = t;
   return true;
  }

double Atr(int period = 14)
  {
   int h = iATR(g_symbol, InpEntryTF, period);
   double buf[];
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, 1, 1, buf) < 1)
      return 0;
   return buf[0];
  }

//+------------------------------------------------------------------+
//| Chart markers: broken structure level (MSS) + FVG box            |
//+------------------------------------------------------------------+
void MarkSetup(const SMss &mss, const SFvg &fvg)
  {
   string p = "PAFQIE_MK_";
   string an = p + "mss_arrow_" + TimeToString(mss.time);
   if(ObjectFind(0, an) < 0)
     {
      ObjectCreate(0, an, OBJ_ARROW, 0, mss.time, mss.level);
      ObjectSetInteger(0, an, OBJPROP_ARROWCODE, mss.bullish ? 233 : 234);
      ObjectSetInteger(0, an, OBJPROP_COLOR, PAF_CLR_MAGENTA);
     }
   string ln = p + "mss_line_" + TimeToString(mss.time);
   if(ObjectFind(0, ln) < 0)
     {
      ObjectCreate(0, ln, OBJ_TREND, 0, mss.time, mss.level, iTime(g_symbol, InpEntryTF, 0), mss.level);
      ObjectSetInteger(0, ln, OBJPROP_COLOR, PAF_CLR_GOLD);
      ObjectSetInteger(0, ln, OBJPROP_STYLE, STYLE_DASH);
     }
   string fn = p + "fvg_" + TimeToString(fvg.time);
   if(fvg.valid && ObjectFind(0, fn) < 0)
     {
      ObjectCreate(0, fn, OBJ_RECTANGLE, 0, fvg.time, fvg.top,
                   iTime(g_symbol, InpEntryTF, 0), fvg.bottom);
      ObjectSetInteger(0, fn, OBJPROP_COLOR, fvg.bullish ? PAF_CLR_GREEN : PAF_CLR_RED);
      ObjectSetInteger(0, fn, OBJPROP_FILL, false);
     }
  }

//+------------------------------------------------------------------+
void Journal(string bias, const SDecision &d, double entry, double rr, bool executed, string note)
  {
   if(!InpLogJournal)
      return;
   string fname = "PAFQIE_journal.csv";
   int fh = FileOpen(fname, FILE_READ | FILE_WRITE | FILE_TXT | FILE_UNICODE);
   if(fh == INVALID_HANDLE)
      return;
   if(FileSize(fh) == 0)
      FileWriteString(fh, "time;symbol;bias;action;confidence;entry;sl;tp;rr;executed;note;reason\n");
   FileSeek(fh, 0, SEEK_END);
   FileWriteString(fh, StringFormat("%s;%s;%s;%s;%d;%.5f;%.5f;%.5f;%.2f;%s;%s;%s\n",
                   TimeToString(TimeCurrent()), g_symbol, bias, d.action, d.confidence,
                   entry, d.sl, d.tp, rr, executed ? "YES" : "NO", note, d.reason));
   FileClose(fh);
  }

//+------------------------------------------------------------------+
//| Ask the LLM about a confirmed setup, validate, maybe execute     |
//+------------------------------------------------------------------+
void AskBrainAndDecide(const SMss &mss, const SFvg &fvg,
                       const SPool &pools[], int nPools, string trend, double atr)
  {
   string bias = mss.bullish ? "BUY" : "SELL";
   double bid  = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double ask  = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   string reqJson = PafBuildDecideRequest(g_symbol, InpEntryTF, bid, ask, atr, trend, bias,
                                          mss, fvg, pools, nPools, InpCtxBars, InpMinRR);
   string respJson, err;

   g_llmStatus = "THINKING...";
   if(!PafCallBrain(InpBrainUrl, reqJson, InpTimeoutMs, respJson, err))
     {
      g_llmStatus = "ERROR";
      g_lastReason = err;
      Print("PAF-QIE brain: ", err);
      return;
     }

   SDecision d;
   PafParseDecision(respJson, d);
   if(d.action != "BUY" && d.action != "SELL" && d.action != "SKIP")
     {
      g_llmStatus = "PARSE FAIL";
      g_lastReason = StringSubstr(respJson, 0, 120);
      Print("PAF-QIE brain: respons tak terduga: ", respJson);
      return;
     }

   g_lastConf   = d.confidence;
   g_lastReason = d.reason;
   g_llmStatus  = d.action;
   g_lastDecisionAt = iTime(g_symbol, InpEntryTF, 0);

   bool   isBuy = (d.action == "BUY");
   double entry = isBuy ? ask : bid;
   double rr = 0;

   //--- Python (brain/decision.py) sudah memvalidasi (arah/RR/confidence/ATR).
   //--- EA tetap re-check di sini sebagai lapis kedua — jangan pernah percaya
   //--- penuh ke server eksternal untuk order sungguhan.
   string note = d.reason;
   bool ok = d.valid;
   if(ok && d.action != bias)                       { ok = false; note = "counter-bias -> skip"; }
   if(ok)
     {
      if(isBuy  && !(d.sl < entry && d.tp > entry)) { ok = false; note = "level BUY tidak sane"; }
      if(!isBuy && !(d.sl > entry && d.tp < entry)) { ok = false; note = "level SELL tidak sane"; }
     }
   if(ok)
     {
      double risk = MathAbs(entry - d.sl);
      rr = (risk > 0) ? MathAbs(d.tp - entry) / risk : 0;
      if(risk <= 0)                      { ok = false; note = "risk<=0"; }
      else if(rr < InpMinRR)             { ok = false; note = "RR < min (EA re-check)"; }
      else if(d.confidence < InpMinConfidence)
                                         { ok = false; note = "confidence < min (EA re-check)"; }
      else if(atr > 0 && risk > InpMaxSlAtr * atr)
                                         { ok = false; note = "SL terlalu lebar vs ATR (EA re-check)"; }
     }

   string msg = StringFormat("PAF-QIE %s: %s conf=%d%% sl=%s tp=%s rr=%.2f — %s %s",
                             g_symbol, d.action, d.confidence,
                             DoubleToString(d.sl, _Digits), DoubleToString(d.tp, _Digits),
                             rr, ok ? "EXECUTE" : "NO-TRADE", note);
   Print(msg, " | ", d.reason);
   Alert(msg);
   if(InpPushNotif)
      SendNotification(msg);

   bool executed = false;
   if(ok && InpAutoTrade)
     {
      string lerr;
      double lots = g_exec.CalcLots(g_symbol, InpRiskPct, MathAbs(entry - d.sl),
                                    InpMaxLotRiskMult, lerr);
      if(lots <= 0)
         Print("PAF-QIE sizing: ", lerr);
      else
         executed = g_exec.Open(g_symbol, isBuy, lots,
                                NormalizeDouble(d.sl, _Digits),
                                NormalizeDouble(d.tp, _Digits), "PAFQIE");
     }
   Journal(bias, d, entry, rr, executed, note);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   g_exec.Manage(g_symbol);

   if(!IsNewBar())
      return;

   //--- rebuild picture
   SSwing swH[];
   int nH = PafFindSwings(g_symbol, PERIOD_H1, 150, InpSwingK, swH);
   string trendH = PafTrendFromSwings(swH, nH);
   double atr = Atr();

   SPool pools[];
   int nPools = PafBuildPools(g_symbol, InpEntryTF, InpUseAsia, InpAsiaStart, InpAsiaEnd,
                              swH, nH, InpMaxSwingPools, pools);

   double refHigh, refLow; bool bias;
   SMss mss;
   bool justFlipped = PafComputeStructure(g_symbol, InpEntryTF, InpLookback, InpSwingK,
                                          refHigh, refLow, bias, mss);
   bool hasBias = (refHigh > 0 && refLow > 0);

   SFvg fvg; fvg.valid = false;
   if(justFlipped)
      PafFindFvg(g_symbol, InpEntryTF, mss.bullish, InpFvgLookback, fvg);

   //--- state
   bool inTrade = (g_exec.CountOpen(g_symbol) > 0);
   int  barsSinceDecision = (g_lastDecisionAt == 0) ? 99999 :
        (int)((iTime(g_symbol, InpEntryTF, 0) - g_lastDecisionAt) / PeriodSeconds(InpEntryTF));

   ENUM_PAF_STATE state = PAF_SEARCHING;
   if(hasBias)                    state = PAF_TRACKING;
   if(justFlipped)                state = PAF_ENTRY_READY;
   if(barsSinceDecision < InpCooldownBars) state = PAF_COOLDOWN;
   if(inTrade)                    state = PAF_TRADE_ACTIVE;

   if(justFlipped)
      MarkSetup(mss, fvg);

   //--- brain trigger: MSS just flipped this bar, not yet asked, no trade, no cooldown
   if(justFlipped && !inTrade && mss.time != g_lastAskedMss
      && barsSinceDecision >= InpCooldownBars)
     {
      g_lastAskedMss = mss.time;
      AskBrainAndDecide(mss, fvg, pools, nPools, trendH, atr);
      inTrade = (g_exec.CountOpen(g_symbol) > 0);
      if(inTrade)
         state = PAF_TRADE_ACTIVE;
     }

   //--- dashboard
   string structTxt = hasBias ? (bias ? "BULLISH" : "BEARISH") : "-";
   string liqTxt = "TP TARGETS " + IntegerToString(nPools);

   string mssTxt = "-"; color mssClr = PAF_CLR_GRAY;
   if(justFlipped)
     { mssTxt = mss.bullish ? "BULLISH CONFIRMED" : "BEARISH CONFIRMED";
       mssClr = mss.bullish ? PAF_CLR_GREEN : PAF_CLR_RED; }

   string entryTxt = "-"; color entryClr = PAF_CLR_GRAY;
   if(g_llmStatus == "BUY")  { entryTxt = "BUY";  entryClr = PAF_CLR_GREEN; }
   if(g_llmStatus == "SELL") { entryTxt = "SELL"; entryClr = PAF_CLR_RED;  }
   if(g_llmStatus == "SKIP") { entryTxt = "SKIP"; entryClr = PAF_CLR_GRAY; }

   g_dash.Update(trendH, structTxt, liqTxt, PAF_CLR_GRAY, mssTxt, mssClr,
                 entryTxt, entryClr, state, g_lastConf, g_llmStatus, g_lastReason);
  }
