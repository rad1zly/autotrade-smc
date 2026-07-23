//+------------------------------------------------------------------+
//|                                                      Confirm.mqh |
//|  JDI BARZ EA - konfirmasi price action di zona (TF entry)        |
//+------------------------------------------------------------------+
#ifndef JDZ_CONFIRM_MQH
#define JDZ_CONFIRM_MQH

#include "Structure.mqh"

//+------------------------------------------------------------------+
//| Engulfing searah bias pada bar sh (butuh bar sh+1)                |
//+------------------------------------------------------------------+
bool IsEngulfing(const MqlRates &r[],const int sh,const int dir,const int total)
  {
   if(sh+1>=total)
      return false;
   if(dir>0)
      return (r[sh+1].close<r[sh+1].open &&          // bar sebelumnya bearish
              r[sh].close>r[sh].open &&              // bar konfirmasi bullish
              r[sh].close>=r[sh+1].open &&
              r[sh].open<=r[sh+1].close &&
              BodySize(r[sh])>BodySize(r[sh+1]));
   return (r[sh+1].close>r[sh+1].open &&
           r[sh].close<r[sh].open &&
           r[sh].close<=r[sh+1].open &&
           r[sh].open>=r[sh+1].close &&
           BodySize(r[sh])>BodySize(r[sh+1]));
  }

//+------------------------------------------------------------------+
//| Rejection / pin-bar searah bias pada bar sh                       |
//+------------------------------------------------------------------+
bool IsRejection(const MqlRates &r[],const int sh,const int dir)
  {
   double range=r[sh].high-r[sh].low;
   if(range<=0.0)
      return false;
   if(dir>0)
     {
      double lowerWick=MathMin(r[sh].open,r[sh].close)-r[sh].low;
      return (lowerWick>=g_cfg.wickRatio*range &&
              r[sh].close>r[sh].low+0.5*range);      // close di paruh atas
     }
   double upperWick=r[sh].high-MathMax(r[sh].open,r[sh].close);
   return (upperWick>=g_cfg.wickRatio*range &&
           r[sh].close<r[sh].high-0.5*range);        // close di paruh bawah
  }

//+------------------------------------------------------------------+
//| Sinyal konfirmasi lengkap: pattern + close reclaim zona           |
//+------------------------------------------------------------------+
bool ConfirmSignal(const MqlRates &r[],const int sh,const int dir,
                   const double zTop,const double zBottom)
  {
   int total=ArraySize(r);
   if(sh>=total)
      return false;

   // harga harus close kembali ke sisi zona yang benar (bukan tembus)
   if(dir>0 && r[sh].close<zBottom)
      return false;
   if(dir<0 && r[sh].close>zTop)
      return false;

   bool eng=IsEngulfing(r,sh,dir,total);
   bool rej=IsRejection(r,sh,dir);
   bool cls=(dir>0 ? r[sh].close>r[sh].open : r[sh].close<r[sh].open);

   switch(g_cfg.confirmMode)
     {
      case CONFIRM_ENGULF:    return eng;
      case CONFIRM_REJECTION: return rej;
      case CONFIRM_CLOSE:     return cls;
      default:                return (eng || rej);
     }
  }

#endif // JDZ_CONFIRM_MQH
