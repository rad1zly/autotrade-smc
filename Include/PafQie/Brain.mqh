//+------------------------------------------------------------------+
//| PAF-QIE — bridge ke otak Python (brain/server.py)                 |
//| EA TIDAK memanggil LLM langsung. EA kirim konteks setup ke server |
//| lokal (mis. http://127.0.0.1:8787/decide); Python yang memanggil  |
//| Minimax/Anthropic dan memvalidasi, lalu balik keputusan bersih.   |
//| Kunci API tidak pernah disimpan/lewat di sisi MQL5.               |
//| Requires: Tools > Options > Expert Advisors >                     |
//|   "Allow WebRequest for listed URL" + http://127.0.0.1:8787       |
//+------------------------------------------------------------------+
#ifndef PAFQIE_BRAIN_MQH
#define PAFQIE_BRAIN_MQH

#include "Types.mqh"

string PafJsonEscape(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", "\\r");
   StringReplace(s, "\n", "\\n");
   StringReplace(s, "\t", "\\t");
   return s;
  }

string PafJsonGetStr(const string src, const string key)
  {
   int p = StringFind(src, "\"" + key + "\"");
   if(p < 0) return "";
   p = StringFind(src, ":", p);
   if(p < 0) return "";
   p = StringFind(src, "\"", p);
   if(p < 0) return "";
   p++;
   string out = "";
   int len = StringLen(src);
   while(p < len)
     {
      ushort ch = StringGetCharacter(src, p);
      if(ch == '\\' && p + 1 < len)
        {
         ushort nx = StringGetCharacter(src, p + 1);
         if(nx == 'n') out += "\n";
         else if(nx == 't') out += "\t";
         else if(nx == 'u' && p + 5 < len) { p += 4; }
         else out += ShortToString(nx);
         p += 2;
         continue;
        }
      if(ch == '"')
         break;
      out += ShortToString(ch);
      p++;
     }
   return out;
  }

double PafJsonGetNum(const string src, const string key, double fallback = 0.0)
  {
   int p = StringFind(src, "\"" + key + "\"");
   if(p < 0) return fallback;
   p = StringFind(src, ":", p);
   if(p < 0) return fallback;
   p++;
   int len = StringLen(src);
   while(p < len)
     {
      ushort ch = StringGetCharacter(src, p);
      if(ch == ' ' || ch == '"') { p++; continue; }
      break;
     }
   string num = "";
   while(p < len)
     {
      ushort ch = StringGetCharacter(src, p);
      if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-' || ch == '+' || ch == 'e' || ch == 'E')
        { num += ShortToString(ch); p++; }
      else
         break;
     }
   if(num == "") return fallback;
   return StringToDouble(num);
  }

bool PafJsonGetBool(const string src, const string key, bool fallback = false)
  {
   int p = StringFind(src, "\"" + key + "\"");
   if(p < 0) return fallback;
   p = StringFind(src, ":", p);
   if(p < 0) return fallback;
   p++;
   int len = StringLen(src);
   while(p < len && StringGetCharacter(src, p) == ' ')
      p++;
   if(p + 4 <= len && StringSubstr(src, p, 4) == "true")
      return true;
   if(p + 5 <= len && StringSubstr(src, p, 5) == "false")
      return false;
   return fallback;
  }

// Bangun JSON request konteks setup untuk dikirim ke brain/server.py.
// Field HARUS cocok dengan yang dibaca brain/decision.py:build_user_prompt.
string PafBuildDecideRequest(const string sym, ENUM_TIMEFRAMES tf,
                             double bid, double ask, double atr,
                             const string trend, const string bias,
                             const SMss &mss, const SFvg &fvg,
                             const SPool &pools[], int nPools,
                             int ctxBars, double minRR)
  {
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   string s = "{";
   s += "\"symbol\":\"" + sym + "\",\"tf\":\"" + EnumToString(tf) + "\",";
   s += "\"digits\":" + IntegerToString(dg) + ",";
   s += "\"time\":\"" + TimeToString(TimeCurrent()) + "\",";
   s += "\"bid\":" + DoubleToString(bid, dg) + ",\"ask\":" + DoubleToString(ask, dg) + ",";
   s += "\"atr\":" + DoubleToString(atr, dg) + ",";
   s += "\"trend\":\"" + trend + "\",\"bias\":\"" + bias + "\",";
   s += "\"min_rr\":" + DoubleToString(minRR, 2) + ",";

   // "sweep" = level swing structure yang baru saja ditembus (bukan pool eksternal) --
   // sama persis dengan mss.level, dipakai brain/decision.py buat validasi SL.
   s += "\"sweep\":{\"origin\":\"" + (string)(mss.bullish ? "SWING-H" : "SWING-L") + "\","
        + "\"side\":\"" + (string)(mss.bullish ? "HIGH" : "LOW") + "\","
        + "\"extreme\":" + DoubleToString(mss.level, dg) + ","
        + "\"poolPrice\":" + DoubleToString(mss.level, dg) + ","
        + "\"time\":\"" + TimeToString(mss.time) + "\"},";

   s += "\"mss\":{\"dir\":\"" + (string)(mss.bullish ? "BULLISH" : "BEARISH") + "\","
        + "\"level\":" + DoubleToString(mss.level, dg) + ","
        + "\"time\":\"" + TimeToString(mss.time) + "\"},";

   if(fvg.valid)
      s += "\"fvg\":{\"valid\":true,\"bullish\":" + (string)(fvg.bullish ? "true" : "false") + ","
           + "\"top\":" + DoubleToString(fvg.top, dg) + ","
           + "\"bottom\":" + DoubleToString(fvg.bottom, dg) + "},";
   else
      s += "\"fvg\":null,";

   s += "\"pools\":[";
   for(int i = 0; i < nPools; i++)
     {
      if(i > 0) s += ",";
      s += "{\"origin\":\"" + pools[i].origin + "\","
           + "\"buySide\":" + (string)(pools[i].buySide ? "true" : "false") + ","
           + "\"price\":" + DoubleToString(pools[i].price, dg) + "}";
     }
   s += "],";

   s += "\"candles\":[";
   for(int i = ctxBars; i >= 1; i--)
     {
      if(i < ctxBars) s += ",";
      s += "[\"" + TimeToString(iTime(sym, tf, i)) + "\","
           + DoubleToString(iOpen(sym, tf, i), dg) + ","
           + DoubleToString(iHigh(sym, tf, i), dg) + ","
           + DoubleToString(iLow(sym, tf, i), dg) + ","
           + DoubleToString(iClose(sym, tf, i), dg) + "]";
     }
   s += "]}";
   return s;
  }

// POST ke bridge lokal (brain/server.py). Balikin body respons di outJson.
bool PafCallBrain(const string url, const string jsonBody, int timeoutMs, string &outJson, string &err)
  {
   outJson = ""; err = "";
   if(MQLInfoInteger(MQL_TESTER))
     { err = "WebRequest tidak tersedia di Strategy Tester"; return false; }

   string headers = "Content-Type: application/json\r\n";
   char post[], result[];
   string resultHeaders;
   StringToCharArray(jsonBody, post, 0, WHOLE_ARRAY, CP_UTF8);
   int n = ArraySize(post);
   if(n > 0 && post[n - 1] == 0)
      ArrayResize(post, n - 1);

   ResetLastError();
   int status = WebRequest("POST", url, headers, timeoutMs, post, result, resultHeaders);
   if(status == -1)
     {
      int code = GetLastError();
      err = "WebRequest gagal, error " + IntegerToString(code);
      if(code == 4014)
         err += " — tambahkan " + url + " di Tools > Options > Expert Advisors > Allow WebRequest";
      else
         err += " — pastikan brain/server.py sedang jalan (python3 -m brain.server)";
      return false;
     }
   outJson = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(status != 200)
     {
      err = "HTTP " + IntegerToString(status) + ": " + StringSubstr(outJson, 0, 300);
      return false;
     }
   return true;
  }

// Parse respons brain (sudah tervalidasi RR/confidence/dsb di sisi Python).
bool PafParseDecision(const string json, SDecision &d)
  {
   d.action = PafJsonGetStr(json, "action");
   StringToUpper(d.action);
   d.sl = PafJsonGetNum(json, "sl");
   d.tp = PafJsonGetNum(json, "tp");
   d.confidence = (int)PafJsonGetNum(json, "confidence", 0);
   d.reason = PafJsonGetStr(json, "reason");
   if(d.reason == "")
      d.reason = PafJsonGetStr(json, "note");
   d.valid = PafJsonGetBool(json, "valid", false)
             && (d.action == "BUY" || d.action == "SELL");
   return true;
  }

#endif // PAFQIE_BRAIN_MQH
