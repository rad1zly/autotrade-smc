//+------------------------------------------------------------------+
//| PAF-QIE — liquidity pools: kandidat target TP (magnet arah).     |
//| BUKAN syarat entry — MSS (Structure.mqh) berdiri sendiri, tidak  |
//| perlu pool ini di-sweep dulu.                                    |
//+------------------------------------------------------------------+
#ifndef PAFQIE_LIQUIDITY_MQH
#define PAFQIE_LIQUIDITY_MQH

#include "Types.mqh"

void PafAddPool(SPool &pools[], double price, bool buySide, string origin)
  {
   if(price <= 0)
      return;
   int n = ArraySize(pools);
   ArrayResize(pools, n + 1);
   pools[n].price = price;
   pools[n].buySide = buySide;
   pools[n].origin = origin;
  }

// Pools: previous-day H/L, Asia-session range, unswept H1 swing highs/lows
int PafBuildPools(const string sym, ENUM_TIMEFRAMES entryTf,
                  bool useAsia, int asiaStartHour, int asiaEndHour,
                  const SSwing &htfSwings[], int nSwings, int maxSwingPoolsPerSide,
                  SPool &pools[])
  {
   ArrayResize(pools, 0);

   // Previous day high/low
   PafAddPool(pools, iHigh(sym, PERIOD_D1, 1), true,  "PDH");
   PafAddPool(pools, iLow(sym, PERIOD_D1, 1),  false, "PDL");

   // Asia session range (server hours), today's bars on entry TF
   if(useAsia)
     {
      double ah = 0, al = 0;
      datetime dayStart = iTime(sym, PERIOD_D1, 0);
      for(int i = 1; i < 500; i++)
        {
         datetime t = iTime(sym, entryTf, i);
         if(t < dayStart)
            break;
         MqlDateTime dt;
         TimeToStruct(t, dt);
         if(dt.hour >= asiaStartHour && dt.hour < asiaEndHour)
           {
            double h = iHigh(sym, entryTf, i), l = iLow(sym, entryTf, i);
            if(ah == 0 || h > ah) ah = h;
            if(al == 0 || l < al) al = l;
           }
        }
      if(ah > 0) PafAddPool(pools, ah, true,  "ASIA-H");
      if(al > 0) PafAddPool(pools, al, false, "ASIA-L");
     }

   // Recent HTF swing highs/lows (newest first, capped per side)
   int hc = 0, lc = 0;
   for(int i = nSwings - 1; i >= 0; i--)
     {
      if(htfSwings[i].isHigh && hc < maxSwingPoolsPerSide)
        { PafAddPool(pools, htfSwings[i].price, true, "SWING-H"); hc++; }
      if(!htfSwings[i].isHigh && lc < maxSwingPoolsPerSide)
        { PafAddPool(pools, htfSwings[i].price, false, "SWING-L"); lc++; }
     }
   return ArraySize(pools);
  }

#endif // PAFQIE_LIQUIDITY_MQH
