//+------------------------------------------------------------------+
//| PAF-QIE — shared types & enums                                   |
//+------------------------------------------------------------------+
#ifndef PAFQIE_TYPES_MQH
#define PAFQIE_TYPES_MQH

enum ENUM_PAF_STATE
  {
   PAF_SEARCHING = 0,   // histori swing belum cukup buat nentuin bias
   PAF_TRACKING,        // bias sudah ada, nunggu break level referensi
   PAF_ENTRY_READY,     // MSS baru saja confirmed, sedang tanya otak
   PAF_TRADE_ACTIVE,
   PAF_COOLDOWN
  };

string PafStateName(ENUM_PAF_STATE s)
  {
   switch(s)
     {
      case PAF_SEARCHING:    return "SEARCHING";
      case PAF_TRACKING:     return "TRACKING";
      case PAF_ENTRY_READY:  return "ENTRY READY";
      case PAF_TRADE_ACTIVE: return "TRADE ACTIVE";
      case PAF_COOLDOWN:     return "COOLDOWN";
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

// Market Structure Shift: close menembus level swing referensi (high/low
// paling baru confirmed yang berlawanan dengan bias saat ini) -> bias flip.
// TIDAK butuh sweep pool eksternal dulu — swing/structure-nya sendiri yang
// jadi "sweep" di skala itu.
struct SMss
  {
   bool              confirmed;
   bool              bullish;
   double            level;    // level swing yang baru saja ditembus
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
