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

// Replay struktur swing dari histori (stateless, dihitung ulang tiap panggilan
// -- konsisten dengan gaya PafFindSwings/PafBuildPools yang juga rebuild tiap
// bar baru) buat cari bias & level referensi TERKINI, dan apakah MSS (bias
// flip) baru saja terjadi PERSIS di bar shift 1 (bar yang baru close).
//
// Bias awal ditentukan dari swing (high/low) mana yang confirmed paling akhir.
// Selama bias bullish, refLow (swing low confirmed terbaru) itu yang "dijaga":
// close < refLow -> bearish MSS, bias flip. Simetris utk bias bearish & refHigh.
// Swing SEARAH bias (bikin high/low baru yang lebih ekstrem) itu BOS/lanjutan,
// bukan reversal -- cuma update level referensi, TIDAK memicu MSS.
bool PafComputeStructure(const string sym, ENUM_TIMEFRAMES tf, int lookback, int k,
                         double &outRefHigh, double &outRefLow, bool &outBias, SMss &mss)
  {
   mss.confirmed = false;
   int bars = Bars(sym, tf);
   if(bars < lookback + k + 2)
      lookback = bars - k - 2;
   if(lookback <= k + 1)
      return false;

   bool   biasSet = false, bias = false;
   bool   highSet = false, lowSet = false;
   double refHigh = 0, refLow = 0;

   for(int i = lookback; i >= 1; i--)
     {
      int j = i + k; // kandidat swing yang baru confirmed persis saat proses sampai bar i
      if(j + k <= bars - 1)
        {
         double hj = iHigh(sym, tf, j), lj = iLow(sym, tf, j);
         bool isHigh = true, isLow = true;
         for(int m = 1; m <= k && (isHigh || isLow); m++)
           {
            if(hj <= iHigh(sym, tf, j + m) || hj < iHigh(sym, tf, j - m)) isHigh = false;
            if(lj >= iLow(sym, tf, j + m) || lj > iLow(sym, tf, j - m))   isLow = false;
           }
         if(isHigh)
           {
            refHigh = hj; highSet = true;
            if(!biasSet && lowSet) { bias = false; biasSet = true; } // high paling baru -> bearish
           }
         if(isLow)
           {
            refLow = lj; lowSet = true;
            if(!biasSet && highSet) { bias = true; biasSet = true; } // low paling baru -> bullish
           }
        }

      double c = iClose(sym, tf, i);
      if(biasSet && bias && lowSet && c < refLow)
        {
         bias = false;
         if(i == 1) { mss.confirmed = true; mss.bullish = false; mss.level = refLow; mss.time = iTime(sym, tf, i); }
        }
      else if(biasSet && !bias && highSet && c > refHigh)
        {
         bias = true;
         if(i == 1) { mss.confirmed = true; mss.bullish = true; mss.level = refHigh; mss.time = iTime(sym, tf, i); }
        }
     }

   outRefHigh = refHigh; outRefLow = refLow; outBias = bias;
   return mss.confirmed;
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
