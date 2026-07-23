//+------------------------------------------------------------------+
//|                                                    JdiBarzEA.mq5 |
//|  EA multi-simbol: Order-Block / Breaker / Flipzone / FVG         |
//|  Entry bukan limit order - menunggu retest zona + konfirmasi     |
//|  price action (reaction candle), lalu market order.              |
//|  Berdasarkan template "JDI BARZ" (OB / BB / FLIP-ZONE).          |
//+------------------------------------------------------------------+
#property copyright "Fadhil - dibuat dengan Claude Code"
#property version   "1.00"
#property description "JDI BARZ EA - identifikasi OB/BB/Flipzone/FVG, entry price action di retest zona"

#include <JdiBarz\Zones.mqh>

//--- input -----------------------------------------------------------
input group "=== Simbol & Timeframe ==="
input string          InpSymbols        = ""; // Daftar simbol (kosong = simbol chart ini saja)
input ENUM_TIMEFRAMES InpZoneTF         = PERIOD_H1;              // Timeframe zona (OB/FLIP/FVG)
input ENUM_TIMEFRAMES InpEntryTF        = PERIOD_M15;             // Timeframe konfirmasi entry

input group "=== Deteksi Struktur ==="
input int             InpSwingBars      = 3;    // Fractal: bar kiri/kanan swing
input int             InpATRPeriod      = 14;   // Periode ATR
input double          InpDispBodyATR    = 0.0;  // Body minimal candle displacement (x ATR, 0 = cukup ada FVG)
input double          InpMinReactATR    = 0.3;  // Jarak reaksi minimal dari OB (x ATR)
input int             InpMaxZoneAge     = 300;  // Umur maksimal zona (bar TF zona)
input int             InpHistoryBars    = 500;  // Bar histori di-replay saat init
input double          InpFlipFibMax     = 0.5;  // Flipzone harus <= level fib ini (0.5 = 50%)

input group "=== Zona yang Ditradingkan ==="
input bool            InpTradeFlip      = true;  // Trade FLIPZONE (setup utama)
input bool            InpTradeOB        = false; // Trade OB valid (retest setelah sweep)
input bool            InpTradeBB        = false; // Trade Breaker Block
input bool            InpTradeFVG       = false; // Trade FVG

input group "=== Konfirmasi Entry ==="
input ENUM_CONFIRM_MODE InpConfirmMode  = CONFIRM_EITHER; // Pola konfirmasi
input double          InpWickRatio      = 0.5;  // Rasio wick minimal rejection (x range)

input group "=== Risk Management ==="
input double          InpRiskPercent    = 1.0;  // Risiko per trade (% equity)
input ENUM_TP_MODE    InpTPMode         = TP_LIQUIDITY; // Mode take profit
input double          InpMinRRLiq       = 5.0;  // RR minimal ke target liquidity (skip jika kurang)
input double          InpRR             = 2.0;  // RR tetap (dipakai mode TP_FIXED_RR)
input bool            InpUsePartial     = true; // Partial close + SL ke BE saat profit berjalan
input double          InpPartialAtRR    = 3.0;  // Trigger partial (x risiko awal)
input double          InpPartialPct     = 50.0; // % posisi yang ditutup saat partial
input double          InpMaxLotRiskMult = 1.5;  // Batas risiko saat lot dibulatkan ke lot minimum (x target)
input double          InpSLBufferATR    = 0.25; // Buffer SL di luar zona (x ATR)
input int             InpMaxPosPerSym   = 1;    // Maks posisi bersamaan per simbol
input long            InpMagic          = 770077;
input int             InpSlippagePoints = 20;   // Deviation (points)
input int             InpMaxSpreadPts   = 0;    // Spread maksimal (points), 0 = off

input group "=== Filter & Tampilan ==="
input bool            InpUseTimeFilter  = false; // Aktifkan filter jam (waktu server)
input int             InpHourStart      = 7;     // Jam mulai
input int             InpHourEnd        = 21;    // Jam selesai
input bool            InpDrawZones      = true;  // Gambar zona di chart
input bool            InpCleanupOnExit  = false; // Hapus objek saat EA dilepas
input bool            InpDebugLog       = true;  // Log detail funnel sinyal (diagnosa)

//--- state -----------------------------------------------------------
CSymbolEngine *g_engines[];

//+------------------------------------------------------------------+
int OnInit()
  {
   //--- salin input ke konfigurasi global
   g_cfg.ztf            =InpZoneTF;
   g_cfg.etf            =InpEntryTF;
   g_cfg.swingBars      =InpSwingBars;
   g_cfg.atrPeriod      =InpATRPeriod;
   g_cfg.dispBodyATR    =InpDispBodyATR;
   g_cfg.minReactATR    =InpMinReactATR;
   g_cfg.maxZoneAge     =InpMaxZoneAge;
   g_cfg.historyBars    =InpHistoryBars;
   g_cfg.flipFibMax     =InpFlipFibMax;
   g_cfg.confirmMode    =InpConfirmMode;
   g_cfg.wickRatio      =InpWickRatio;
   g_cfg.riskPct        =InpRiskPercent;
   g_cfg.rr             =InpRR;
   g_cfg.tpMode         =InpTPMode;
   g_cfg.minRRLiq       =InpMinRRLiq;
   g_cfg.usePartial     =InpUsePartial;
   g_cfg.partialAtRR    =InpPartialAtRR;
   g_cfg.partialPct     =InpPartialPct;
   g_cfg.maxLotRiskMult =InpMaxLotRiskMult;
   g_cfg.slBufATR       =InpSLBufferATR;
   g_cfg.maxPosPerSym   =InpMaxPosPerSym;
   g_cfg.magic          =InpMagic;
   g_cfg.maxSpreadPoints=InpMaxSpreadPts;
   g_cfg.tradeFlip      =InpTradeFlip;
   g_cfg.tradeOB        =InpTradeOB;
   g_cfg.tradeBB        =InpTradeBB;
   g_cfg.tradeFVG       =InpTradeFVG;
   g_cfg.draw           =InpDrawZones;
   g_cfg.useTimeFilter  =InpUseTimeFilter;
   g_cfg.hourStart      =InpHourStart;
   g_cfg.hourEnd        =InpHourEnd;
   g_cfg.debugLog       =InpDebugLog;

   if(PeriodSeconds(InpEntryTF)>PeriodSeconds(InpZoneTF))
      Print("[JDZ] PERINGATAN: TF entry lebih besar dari TF zona - biasanya entry TF <= zona TF");
   if(InpRiskPercent<=0.0 || InpRiskPercent>10.0)
      Print("[JDZ] PERINGATAN: risk % di luar rentang wajar (0-10%)");

   TradeInit(InpMagic,InpSlippagePoints);

   //--- parse daftar simbol; kosong = pakai simbol chart tempat EA dipasang
   string symList=InpSymbols;
   StringTrimLeft(symList);
   StringTrimRight(symList);
   if(symList=="")
     {
      symList=_Symbol;
      PrintFormat("[JDZ] daftar simbol kosong -> memakai simbol chart: %s",_Symbol);
     }
   string parts[];
   int n=StringSplit(symList,(ushort)',',parts);
   if(n<=0)
     {
      Print("[JDZ] ERROR: daftar simbol kosong");
      return INIT_PARAMETERS_INCORRECT;
     }
   for(int i=0;i<n;i++)
     {
      string s=parts[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s=="")
         continue;
      if(!SymbolSelect(s,true))
        {
         PrintFormat("[JDZ] simbol '%s' tidak ditemukan di broker ini. Cek Market Watch - "
                     "NAS100 kadang bernama US100/USTEC/NDX, BTCUSD kadang BTCUSD.x dsb.",s);
         continue;
        }
      int k=ArraySize(g_engines);
      ArrayResize(g_engines,k+1);
      g_engines[k]=new CSymbolEngine(s);
      PrintFormat("[JDZ] memantau %s (zona %s, entry %s)",
                  s,EnumToString(InpZoneTF),EnumToString(InpEntryTF));
     }
   if(ArraySize(g_engines)==0)
     {
      Print("[JDZ] ERROR: tidak ada simbol valid untuk dipantau");
      return INIT_PARAMETERS_INCORRECT;
     }

   EventSetTimer(1);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   for(int i=0;i<ArraySize(g_engines);i++)
      if(CheckPointer(g_engines[i])==POINTER_DYNAMIC)
        {
         g_engines[i].ReportDiag(); // ringkasan funnel: di tester muncul di tab Journal
         delete g_engines[i];
        }
   ArrayFree(g_engines);
   if(InpCleanupOnExit)
      CleanupAllObjects();
   Comment("");
  }

//+------------------------------------------------------------------+
void UpdateAll()
  {
   for(int i=0;i<ArraySize(g_engines);i++)
      g_engines[i].Update();
  }

//+------------------------------------------------------------------+
void UpdateComment()
  {
   string txt="JDI BARZ EA | zona "+EnumToString(g_cfg.ztf)+
              " | entry "+EnumToString(g_cfg.etf)+"\n";
   for(int i=0;i<ArraySize(g_engines);i++)
     {
      CSymbolEngine *e=g_engines[i];
      if(!e.Ready())
        {
         txt+=e.Symbol()+": memuat data...\n";
         continue;
        }
      txt+=StringFormat("%s: FLIP %d | OB %d | BB %d | FVG %d | posisi %d\n",
                        e.Symbol(),
                        e.CountAlive(ZK_FLIP),e.CountAlive(ZK_OB),
                        e.CountAlive(ZK_BREAKER),e.CountAlive(ZK_FVG),
                        CountOpenPositions(e.Symbol(),g_cfg.magic));
     }
   Comment(txt);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   UpdateAll();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   UpdateAll();     // simbol lain tetap terpantau walau chart sepi tick
   UpdateComment();
  }
//+------------------------------------------------------------------+
