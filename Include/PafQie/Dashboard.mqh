//+------------------------------------------------------------------+
//| PAF-QIE — on-chart dashboard (dark premium: black/gold/blue)     |
//+------------------------------------------------------------------+
#ifndef PAFQIE_DASHBOARD_MQH
#define PAFQIE_DASHBOARD_MQH

#include "Types.mqh"

#define PAF_CLR_BG      C'11,11,13'
#define PAF_CLR_GOLD    C'212,175,55'
#define PAF_CLR_BLUE    C'30,58,138'
#define PAF_CLR_GREEN   C'50,205,50'
#define PAF_CLR_RED     C'220,60,60'
#define PAF_CLR_MAGENTA C'216,70,239'
#define PAF_CLR_GRAY    C'150,150,155'
#define PAF_CLR_WHITE   C'235,235,235'

class CPafDashboard
  {
private:
   string            m_prefix;
   long              m_chart;
   int               m_x, m_y, m_w, m_rowH;
   int               m_rows;

   void              Rect(string name, int x, int y, int w, int h, color bg, color border)
     {
      string obj = m_prefix + name;
      if(ObjectFind(m_chart, obj) < 0)
        {
         ObjectCreate(m_chart, obj, OBJ_RECTANGLE_LABEL, 0, 0, 0);
         ObjectSetInteger(m_chart, obj, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
         ObjectSetInteger(m_chart, obj, OBJPROP_BACK, false);
         ObjectSetInteger(m_chart, obj, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(m_chart, obj, OBJPROP_HIDDEN, true);
        }
      ObjectSetInteger(m_chart, obj, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(m_chart, obj, OBJPROP_YDISTANCE, y);
      ObjectSetInteger(m_chart, obj, OBJPROP_XSIZE, w);
      ObjectSetInteger(m_chart, obj, OBJPROP_YSIZE, h);
      ObjectSetInteger(m_chart, obj, OBJPROP_BGCOLOR, bg);
      ObjectSetInteger(m_chart, obj, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(m_chart, obj, OBJPROP_COLOR, border);
     }

   void              Label(string name, int x, int y, string text, color clr, int size, string font = "Consolas")
     {
      string obj = m_prefix + name;
      if(ObjectFind(m_chart, obj) < 0)
        {
         ObjectCreate(m_chart, obj, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(m_chart, obj, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
         ObjectSetInteger(m_chart, obj, OBJPROP_ANCHOR, ANCHOR_RIGHT_UPPER);
         ObjectSetInteger(m_chart, obj, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(m_chart, obj, OBJPROP_HIDDEN, true);
        }
      ObjectSetInteger(m_chart, obj, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(m_chart, obj, OBJPROP_YDISTANCE, y);
      ObjectSetString(m_chart, obj, OBJPROP_TEXT, text);
      ObjectSetInteger(m_chart, obj, OBJPROP_COLOR, clr);
      ObjectSetInteger(m_chart, obj, OBJPROP_FONTSIZE, size);
      ObjectSetString(m_chart, obj, OBJPROP_FONT, font);
     }

public:
   void              Init(long chart, string prefix = "PAFQIE_", int x = 10, int y = 30)
     {
      m_chart = chart; m_prefix = prefix;
      m_x = x; m_y = y; m_w = 240; m_rowH = 18; m_rows = 10;
     }

   // One key:value row; value right-aligned in gold panel style
   void              Row(int idx, string key, string val, color valClr)
     {
      int y = m_y + 46 + idx * m_rowH;
      Label("k" + IntegerToString(idx), m_x + m_w - 14, y, key, PAF_CLR_GRAY, 9);
      Label("v" + IntegerToString(idx), m_x + 10, y, val, valClr, 9);
     }

   void              Update(string trend, string structure, string liquidity, color liqClr,
                            string mssTxt, color mssClr, string entryTxt, color entryClr,
                            ENUM_PAF_STATE state, int confidence, string llmStatus, string reason)
     {
      int h = 46 + m_rows * m_rowH + 10;
      Rect("bg", m_x, m_y, m_w, h, PAF_CLR_BG, PAF_CLR_GOLD);
      Label("title", m_x + m_w - 14, m_y + 8, "PAF-QIE", PAF_CLR_GOLD, 13, "Arial Black");
      Label("subtitle", m_x + m_w - 14, m_y + 28, "QUANTUM ENGINE", PAF_CLR_GRAY, 7, "Arial");

      color trendClr = (trend == "BULLISH") ? PAF_CLR_GREEN : (trend == "BEARISH") ? PAF_CLR_RED : PAF_CLR_GRAY;
      Row(0, "Trend",      trend, trendClr);
      Row(1, "Structure",  structure, (structure == "BULLISH") ? PAF_CLR_GREEN : (structure == "BEARISH") ? PAF_CLR_RED : PAF_CLR_GRAY);
      Row(2, "Liquidity",  liquidity, liqClr);
      Row(3, "MSS",        mssTxt, mssClr);
      Row(4, "Entry",      entryTxt, entryClr);
      Row(5, "State",      PafStateName(state), (state == PAF_ENTRY_READY) ? PAF_CLR_GOLD : (state == PAF_TRADE_ACTIVE) ? PAF_CLR_GREEN : PAF_CLR_WHITE);
      Row(6, "Confidence", (confidence > 0) ? IntegerToString(confidence) + "%" : "-",
          (confidence >= 80) ? PAF_CLR_GREEN : (confidence >= 60) ? PAF_CLR_GOLD : PAF_CLR_GRAY);
      Row(7, "LLM",        llmStatus, PAF_CLR_WHITE);

      // reason wrapped over the last two rows
      string r1 = reason, r2 = "";
      int maxChars = 38;
      if(StringLen(reason) > maxChars)
        {
         r1 = StringSubstr(reason, 0, maxChars);
         r2 = StringSubstr(reason, maxChars, maxChars);
        }
      Row(8, "", r1, PAF_CLR_GRAY);
      Row(9, "", r2, PAF_CLR_GRAY);
     }

   void              Destroy()
     {
      ObjectsDeleteAll(m_chart, m_prefix);
     }
  };

#endif // PAFQIE_DASHBOARD_MQH
