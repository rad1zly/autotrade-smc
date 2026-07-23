//+------------------------------------------------------------------+
//|                                                    TradeExec.mqh |
//|  JDI BARZ EA - lot sizing % risk, SL/TP RR, eksekusi market      |
//+------------------------------------------------------------------+
#ifndef JDZ_TRADEEXEC_MQH
#define JDZ_TRADEEXEC_MQH

#include "Structure.mqh"
#include <Trade\Trade.mqh>

CTrade g_trade;

//+------------------------------------------------------------------+
//| Set magic & deviation, dipanggil sekali dari OnInit               |
//+------------------------------------------------------------------+
void TradeInit(const long magic,const int deviationPoints)
  {
   g_trade.SetExpertMagicNumber(magic);
   g_trade.SetDeviationInPoints(deviationPoints);
   g_trade.SetAsyncMode(false);
  }

//+------------------------------------------------------------------+
//| Hitung posisi terbuka milik EA ini per simbol                     |
//+------------------------------------------------------------------+
int CountOpenPositions(const string sym,const long magic)
  {
   int cnt=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0)
         continue;
      if(PositionGetString(POSITION_SYMBOL)==sym &&
         PositionGetInteger(POSITION_MAGIC)==magic)
         cnt++;
     }
   return cnt;
  }

//+------------------------------------------------------------------+
//| Lot dari % risk equity dan jarak SL.                               |
//| Jika lot minimum broker memaksa risiko jauh > target (SL lebar,   |
//| lot minimum tak bisa dikecilkan lagi), trade DIBATALKAN (return   |
//| 0) alih-alih dipaksakan pakai lot minimum dengan risiko meledak.  |
//| Ini yang menyebabkan setiap kekalahan di backtest awal persis     |
//| sebesar jarak SL dalam $ (0.01 lot XAUUSD = $1 P&L per $1 harga), |
//| jauh di atas 1% yang diminta - lihat funnel "ditolak lot min".    |
//+------------------------------------------------------------------+
double CalcLots(const string sym,const double slDist)
  {
   if(slDist<=0.0)
      return 0.0;
   double riskMoney=AccountInfoDouble(ACCOUNT_EQUITY)*g_cfg.riskPct/100.0;
   double tickSize =SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0.0 || tickValue<=0.0)
     {
      PrintFormat("[JDZ] %s: tick size/value tidak valid, trade dibatalkan",sym);
      return 0.0;
     }
   double lossPerLot=slDist/tickSize*tickValue;
   if(lossPerLot<=0.0)
      return 0.0;

   double lots=riskMoney/lossPerLot;
   double step=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   double minL=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double maxL=SymbolInfoDouble(sym,SYMBOL_VOLUME_MAX);
   if(step>0.0)
      lots=MathFloor(lots/step)*step;
   if(lots<minL)
     {
      double actualRiskAtMin=minL*lossPerLot;
      if(actualRiskAtMin>riskMoney*g_cfg.maxLotRiskMult)
        {
         PrintFormat("[JDZ] %s: SL $%.2f terlalu lebar untuk lot minimum %.2f "
                     "(risiko jadi $%.2f, target $%.2f) - trade dibatalkan",
                     sym,slDist,minL,actualRiskAtMin,riskMoney);
         return 0.0;
        }
      PrintFormat("[JDZ] %s: lot hasil hitung %.4f < min lot %.2f, risiko aktual $%.2f (target $%.2f)",
                  sym,lots,minL,actualRiskAtMin,riskMoney);
      lots=minL;
     }
   if(lots>maxL)
      lots=maxL;
   return lots;
  }

//+------------------------------------------------------------------+
//| Market order dengan SL absolut dan TP eksplisit / berbasis RR     |
//| tpOverride > 0 : pakai harga TP itu (mode liquidity)              |
//| tpOverride = 0 : TP dihitung dari RR tetap                        |
//+------------------------------------------------------------------+
bool ExecMarket(const string sym,const int dir,double sl,const double tpOverride,
                const string comment)
  {
   int    digits=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
   double point =SymbolInfoDouble(sym,SYMBOL_POINT);
   double ask   =SymbolInfoDouble(sym,SYMBOL_ASK);
   double bid   =SymbolInfoDouble(sym,SYMBOL_BID);
   double entry =(dir>0 ? ask : bid);
   if(entry<=0.0)
      return false;

   // pastikan SL tidak melanggar stops level broker
   double minDist=(double)SymbolInfoInteger(sym,SYMBOL_TRADE_STOPS_LEVEL)*point;
   double slDist=MathAbs(entry-sl);
   if(slDist<minDist)
     {
      sl=(dir>0 ? entry-minDist : entry+minDist);
      slDist=minDist;
     }
   double tp;
   if(tpOverride>0.0)
     {
      tp=tpOverride;
      if((dir>0 && tp<=entry+minDist) || (dir<0 && tp>=entry-minDist))
        {
         PrintFormat("[JDZ] %s TP liquidity %.5f tidak valid terhadap entry %.5f",sym,tp,entry);
         return false;
        }
     }
   else
      tp=(dir>0 ? entry+g_cfg.rr*slDist : entry-g_cfg.rr*slDist);
   sl=NormalizeDouble(sl,digits);
   tp=NormalizeDouble(tp,digits);

   double lots=CalcLots(sym,slDist);
   if(lots<=0.0)
      return false;

   g_trade.SetTypeFillingBySymbol(sym);
   bool ok=(dir>0 ? g_trade.Buy(lots,sym,0.0,sl,tp,comment)
                  : g_trade.Sell(lots,sym,0.0,sl,tp,comment));
   if(ok)
      PrintFormat("[JDZ] %s %s %.2f lot @ %.5f SL %.5f TP %.5f (%s)",
                  sym,dir>0?"BUY":"SELL",lots,entry,sl,tp,comment);
   else
      PrintFormat("[JDZ] %s order GAGAL: retcode=%d (%s)",
                  sym,(int)g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription());
   return ok;
  }

//+------------------------------------------------------------------+
//| Manajemen posisi berjalan: saat profit mencapai partialAtRR x     |
//| risiko awal -> tutup partialPct % posisi dan pindahkan SL ke BE.  |
//| Setelah SL di BE, risiko awal <= 0 sehingga tidak diproses lagi.  |
//+------------------------------------------------------------------+
void ManageOpenPositions(const string sym)
  {
   if(!g_cfg.usePartial)
      return;
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0)
         continue;
      if(PositionGetString(POSITION_SYMBOL)!=sym ||
         PositionGetInteger(POSITION_MAGIC)!=g_cfg.magic)
         continue;

      double entry=PositionGetDouble(POSITION_PRICE_OPEN);
      double sl   =PositionGetDouble(POSITION_SL);
      double tp   =PositionGetDouble(POSITION_TP);
      double vol  =PositionGetDouble(POSITION_VOLUME);
      int    dir  =(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY ? 1 : -1);
      if(sl<=0.0)
         continue;
      double risk=(dir>0 ? entry-sl : sl-entry);
      if(risk<=0.0)
         continue;                       // SL sudah di BE / profit -> sudah dikelola

      double cur=(dir>0 ? SymbolInfoDouble(sym,SYMBOL_BID)
                        : SymbolInfoDouble(sym,SYMBOL_ASK));
      if(cur<=0.0)
         continue;
      double prog=(dir>0 ? cur-entry : entry-cur)/risk;
      if(prog<g_cfg.partialAtRR)
         continue;

      // partial close
      double minLot=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
      double step  =SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
      double closeVol=vol*g_cfg.partialPct/100.0;
      if(step>0.0)
         closeVol=MathFloor(closeVol/step)*step;
      if(closeVol>=minLot && (vol-closeVol)>=minLot)
        {
         if(g_trade.PositionClosePartial(ticket,closeVol))
            PrintFormat("[JDZ] %s partial close %.2f lot @ profit berjalan %.1fR",
                        sym,closeVol,prog);
        }

      // SL ke breakeven (juga penanda posisi sudah dikelola)
      int digits=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
      double be=NormalizeDouble(entry,digits);
      if(g_trade.PositionModify(ticket,be,tp))
         PrintFormat("[JDZ] %s SL dipindah ke breakeven %.5f",sym,be);
     }
  }

#endif // JDZ_TRADEEXEC_MQH
