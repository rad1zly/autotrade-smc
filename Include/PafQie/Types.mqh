//+------------------------------------------------------------------+
//| PAF-QIE — shared types & enums                                   |
//+------------------------------------------------------------------+
#ifndef PAFQIE_TYPES_MQH
#define PAFQIE_TYPES_MQH

enum ENUM_PAF_STATE
  {
   PAF_SEARCHING = 0,
   PAF_LIQUIDITY_FOUND,
   PAF_SWEEP_DETECTED,
   PAF_WAIT_MSS,
   PAF_ENTRY_READY,
   PAF_TRADE_ACTIVE,
   PAF_COOLDOWN
  };

string PafStateName(ENUM_PAF_STATE s)
  {
   switch(s)
     {
      case PAF_SEARCHING:       return "SEARCHING";
      case PAF_LIQUIDITY_FOUND: return "LIQUIDITY FOUND";
      case PAF_SWEEP_DETECTED:  return "SWEEP DETECTED";
      case PAF_WAIT_MSS:        return "WAIT MSS";
      case PAF_ENTRY_READY:     return "ENTRY READY";
      case PAF_TRADE_ACTIVE:    return "TRADE ACTIVE";
      case PAF_COOLDOWN:        return "COOLDOWN";
     }
   return "?";
  }

// Confirmed swing point (fractal)
struct SSwing
  {
   datetime          time;
   int               bar;      // shift on its timeframe at scan time
   double            price;
   bool              isHigh;
  };

// Liquidity pool: BSL above price (buy-side), SSL below (sell-side)
struct SPool
  {
   double            price;
   bool              buySide;
   string            origin;   // "PDH","PDL","ASIA-H","ASIA-L","SWING-H","SWING-L"
  };

// Detected liquidity sweep
struct SSweep
  {
   bool              valid;
   bool              buySideSwept; // true: BSL swept (bearish bias), false: SSL swept (bullish bias)
   double            poolPrice;
   double            extreme;      // wick extreme of the sweep bar
   datetime          time;
   int               bar;          // shift at scan time
   string            origin;
  };

// Market Structure Shift after a sweep
struct SMss
  {
   bool              confirmed;
   bool              bullish;
   double            level;    // broken swing level
   datetime          time;
  };

// Fair value gap (context for the LLM)
struct SFvg
  {
   bool              valid;
   bool              bullish;
   double            top;
   double            bottom;
   datetime          time;
  };

// LLM decision
struct SDecision
  {
   bool              valid;
   string            action;   // "BUY" | "SELL" | "SKIP"
   double            sl;
   double            tp;
   int               confidence; // 0-100
   string            reason;
  };

#endif // PAFQIE_TYPES_MQH
