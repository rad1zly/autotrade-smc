//+------------------------------------------------------------------+
//| PAF-QIE — market structure: swings, trend, MSS                   |
//+------------------------------------------------------------------+
#ifndef PAFQIE_STRUCTURE_MQH
#define PAFQIE_STRUCTURE_MQH

#include "Types.mqh"

// Confirmed k-bar fractal swings, chronological (oldest first). Returns count.
int PafFindSwings(const string sym, ENUM_TIMEFRAMES tf, int lookback, int k, SSwing &out[])
  {
   ArrayResize(out, 0);
   int bars = Bars(sym, tf);
   if(bars < lookback + k + 2)
      lookback = bars - k - 2;
   if(lookback <= k)
      return 0;

   for(int i = lookback; i >= k + 1; i--)
     {
      double hi = iHigh(sym, tf, i);
      double lo = iLow(sym, tf, i);
      bool isHigh = true, isLow = true;
      for(int j = 1; j <= k && (isHigh || isLow); j++)
        {
         if(hi <= iHigh(sym, tf, i + j) || hi < iHigh(sym, tf, i - j))
            isHigh = false;
         if(lo >= iLow(sym, tf, i + j) || lo > iLow(sym, tf, i - j))
            isLow = false;
        }
      if(!isHigh && !isLow)
         continue;
      int n = ArraySize(out);
      ArrayResize(out, n + 1);
      out[n].time   = iTime(sym, tf, i);
      out[n].bar    = i;
      out[n].price  = isHigh ? hi : lo;
      out[n].isHigh = isHigh;
     }
   return ArraySize(out);
  }

// Simple trend read from the last two highs + two lows
string PafTrendFromSwings(const SSwing &sw[], int n)
  {
   double h1 = 0, h2 = 0, l1 = 0, l2 = 0; // 1 = most recent
   int hc = 0, lc = 0;
   for(int i = n - 1; i >= 0 && (hc < 2 || lc < 2); i--)
     {
      if(sw[i].isHigh && hc < 2) { if(hc == 0) h1 = sw[i].price; else h2 = sw[i].price; hc++; }
      if(!sw[i].isHigh && lc < 2) { if(lc == 0) l1 = sw[i].price; else l2 = sw[i].price; lc++; }
     }
   if(hc < 2 || lc < 2)
      return "RANGING";
   bool hh = h1 > h2, hl = l1 > l2;
   bool lh = h1 < h2, ll = l1 < l2;
   if(hh && hl) return "BULLISH";
   if(lh && ll) return "BEARISH";
   return "RANGING";
  }

// After a sweep, look for a close beyond the nearest opposing swing (CHoCH/MSS).
// BSL swept -> bearish MSS = close below last swing low formed before the sweep.
bool PafDetectMss(const string sym, ENUM_TIMEFRAMES tf, const SSweep &sweep,
                  const SSwing &sw[], int n, int windowBars, SMss &mss)
  {
   mss.confirmed = false;
   if(!sweep.valid)
      return false;
   bool wantBull = !sweep.buySideSwept; // SSL swept -> bullish MSS

   // nearest opposing swing formed before the sweep bar
   double level = 0;
   for(int i = n - 1; i >= 0; i--)
     {
      if(sw[i].bar <= sweep.bar)
         continue;                       // formed at/after sweep
      if(wantBull && sw[i].isHigh)  { level = sw[i].price; break; }
      if(!wantBull && !sw[i].isHigh){ level = sw[i].price; break; }
     }
   if(level <= 0)
      return false;

   int from = sweep.bar - 1;
   int to   = MathMax(1, sweep.bar - windowBars);
   for(int s = from; s >= to; s--)
     {
      double c = iClose(sym, tf, s);
      if((wantBull && c > level) || (!wantBull && c < level))
        {
         mss.confirmed = true;
         mss.bullish   = wantBull;
         mss.level     = level;
         mss.time      = iTime(sym, tf, s);
         return true;
        }
     }
   return false;
  }

// Most recent FVG in MSS direction, formed at/after the sweep bar
bool PafFindFvg(const string sym, ENUM_TIMEFRAMES tf, bool bullish, int fromBar, SFvg &fvg)
  {
   fvg.valid = false;
   for(int i = 2; i <= fromBar; i++)
     {
      if(bullish && iLow(sym, tf, i - 1) > iHigh(sym, tf, i + 1))
        {
         fvg.valid = true; fvg.bullish = true;
         fvg.top = iLow(sym, tf, i - 1); fvg.bottom = iHigh(sym, tf, i + 1);
         fvg.time = iTime(sym, tf, i);
         return true;
        }
      if(!bullish && iHigh(sym, tf, i - 1) < iLow(sym, tf, i + 1))
        {
         fvg.valid = true; fvg.bullish = false;
         fvg.top = iLow(sym, tf, i + 1); fvg.bottom = iHigh(sym, tf, i - 1);
         fvg.time = iTime(sym, tf, i);
         return true;
        }
     }
   return false;
  }

#endif // PAFQIE_STRUCTURE_MQH
