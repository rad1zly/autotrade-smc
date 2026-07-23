//+------------------------------------------------------------------+
//| PAF-QIE — execution & position management                        |
//+------------------------------------------------------------------+
#ifndef PAFQIE_TRADEEXEC_MQH
#define PAFQIE_TRADEEXEC_MQH

#include <Trade/Trade.mqh>
#include "Types.mqh"

class CPafExec
  {
private:
   CTrade            m_trade;
   long              m_magic;
   double            m_partialR;      // take partial at this R multiple
   double            m_partialPct;    // % of volume to close

public:
   void              Init(long magic, double partialR, double partialPct)
     {
      m_magic = magic;
      m_partialR = partialR;
      m_partialPct = partialPct;
      m_trade.SetExpertMagicNumber(magic);
      m_trade.SetDeviationInPoints(30);
     }

   // Risk-based sizing. Refuses the trade (returns 0) when broker minimum lot
   // would force risk above riskPct * maxRiskMult — small account + wide SL
   // on high tick-value symbols must NOT silently over-risk.
   double            CalcLots(const string sym, double riskPct, double slDistance,
                              double maxRiskMult, string &err)
     {
      err = "";
      double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
      double riskMoney = equity * riskPct / 100.0;
      double tickVal   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      if(tickVal <= 0 || tickSize <= 0 || slDistance <= 0)
        { err = "parameter sizing tidak valid"; return 0; }

      double lossPerLot = slDistance / tickSize * tickVal;
      double lots       = riskMoney / lossPerLot;

      double minLot  = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
      double maxLot  = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
      double lotStep = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      lots = MathFloor(lots / lotStep) * lotStep;

      if(lots < minLot)
        {
         double minLotRisk = minLot * lossPerLot;
         if(minLotRisk > riskMoney * maxRiskMult)
           {
            err = StringFormat("risiko minLot %.2f > %.1fx target %.2f — trade dibatalkan",
                               minLotRisk, maxRiskMult, riskMoney);
            return 0;
           }
         lots = minLot;
        }
      if(lots > maxLot)
         lots = maxLot;
      return lots;
     }

   bool              Open(const string sym, bool buy, double lots, double sl, double tp, string comment)
     {
      double price = buy ? SymbolInfoDouble(sym, SYMBOL_ASK)
                         : SymbolInfoDouble(sym, SYMBOL_BID);
      bool ok = buy ? m_trade.Buy(lots, sym, price, sl, tp, comment)
                    : m_trade.Sell(lots, sym, price, sl, tp, comment);
      if(!ok)
         PrintFormat("PAF-QIE: order gagal ret=%d (%s)",
                     m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription());
      return ok;
     }

   int               CountOpen(const string sym)
     {
      int cnt = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) == sym &&
            PositionGetInteger(POSITION_MAGIC) == m_magic)
            cnt++;
        }
      return cnt;
     }

   // Partial close at m_partialR then SL to breakeven; partial-done is
   // encoded as "SL already at (or beyond) breakeven".
   void              Manage(const string sym)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != sym ||
            PositionGetInteger(POSITION_MAGIC) != m_magic)
            continue;

         long   type  = PositionGetInteger(POSITION_TYPE);
         double open  = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl    = PositionGetDouble(POSITION_SL);
         double tp    = PositionGetDouble(POSITION_TP);
         double vol   = PositionGetDouble(POSITION_VOLUME);
         bool   isBuy = (type == POSITION_TYPE_BUY);
         double px    = isBuy ? SymbolInfoDouble(sym, SYMBOL_BID)
                              : SymbolInfoDouble(sym, SYMBOL_ASK);

         if(sl <= 0) continue;
         bool beDone = isBuy ? (sl >= open) : (sl <= open);
         if(beDone) continue;

         double riskDist = MathAbs(open - sl);
         double moved    = isBuy ? (px - open) : (open - px);
         if(moved < m_partialR * riskDist)
            continue;

         double minLot  = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
         double lotStep = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
         double closeVol = MathFloor(vol * m_partialPct / 100.0 / lotStep) * lotStep;
         if(closeVol >= minLot && (vol - closeVol) >= minLot)
            m_trade.PositionClosePartial(ticket, closeVol);
         m_trade.PositionModify(ticket, open, tp);
         PrintFormat("PAF-QIE: partial %.0f%% @ %.1fR + SL->BE ticket %I64u",
                     m_partialPct, m_partialR, ticket);
        }
     }
  };

#endif // PAFQIE_TRADEEXEC_MQH
