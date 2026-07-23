//+------------------------------------------------------------------+
//|                                                         Draw.mqh |
//|  JDI BARZ EA - gambar zona di chart (hanya simbol chart aktif)   |
//+------------------------------------------------------------------+
#ifndef JDZ_DRAW_MQH
#define JDZ_DRAW_MQH

#include "Structure.mqh"

#define JDZ_OBJ_PREFIX "JDZ_"

//+------------------------------------------------------------------+
//| Label singkat jenis zona                                          |
//+------------------------------------------------------------------+
string ZoneKindLabel(const SZone &z)
  {
   switch(z.kind)
     {
      case ZK_OB:      return (z.phase==OBP_VALIDATED ? "OB*" : "OB");
      case ZK_BREAKER: return "BB";
      case ZK_FLIP:    return (z.fibOk ? "FLIP" : "FLIP?");
      case ZK_FVG:     return "FVG";
     }
   return "?";
  }

string ZoneObjName(const string sym,const SZone &z)
  {
   return JDZ_OBJ_PREFIX+sym+"_"+IntegerToString(z.kind)+"_"+
          IntegerToString((long)z.t0)+"_"+(z.dir>0?"B":"S");
  }

color ZoneColor(const SZone &z)
  {
   switch(z.kind)
     {
      case ZK_OB:      return C'170,188,230';   // biru muda
      case ZK_BREAKER: return C'232,178,178';   // merah muda
      case ZK_FLIP:    return C'182,208,184';   // hijau muda (seperti template)
      case ZK_FVG:     return C'214,214,214';   // abu-abu
     }
   return clrGray;
  }

//+------------------------------------------------------------------+
//| Buat / update rectangle + label zona                              |
//+------------------------------------------------------------------+
void DrawZoneRect(const string sym,const SZone &z,const datetime rightTime)
  {
   if(sym!=_Symbol)
      return;
   string name=ZoneObjName(sym,z);
   if(ObjectFind(0,name)<0)
     {
      ObjectCreate(0,name,OBJ_RECTANGLE,0,z.t0,z.top,rightTime,z.bottom);
      ObjectSetInteger(0,name,OBJPROP_COLOR,ZoneColor(z));
      ObjectSetInteger(0,name,OBJPROP_FILL,true);
      ObjectSetInteger(0,name,OBJPROP_BACK,true);
      ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(0,name,OBJPROP_HIDDEN,true);

      string lname=name+"_L";
      ObjectCreate(0,lname,OBJ_TEXT,0,z.t0,z.top);
      ObjectSetInteger(0,lname,OBJPROP_COLOR,clrDimGray);
      ObjectSetInteger(0,lname,OBJPROP_FONTSIZE,8);
      ObjectSetInteger(0,lname,OBJPROP_ANCHOR,ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0,lname,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(0,lname,OBJPROP_HIDDEN,true);
     }
   else
     {
      ObjectSetInteger(0,name,OBJPROP_TIME,1,(long)rightTime);
      ObjectSetInteger(0,name,OBJPROP_COLOR,ZoneColor(z)); // kind bisa berubah (OB->BB)
     }
   // label selalu di-refresh (OB bisa jadi OB*, FLIP? bisa jadi FLIP)
   ObjectSetString(0,name+"_L",OBJPROP_TEXT,ZoneKindLabel(z));
  }

void RemoveZoneObjects(const string sym,const SZone &z)
  {
   if(sym!=_Symbol)
      return;
   string name=ZoneObjName(sym,z);
   ObjectDelete(0,name);
   ObjectDelete(0,name+"_L");
  }

void CleanupAllObjects()
  {
   ObjectsDeleteAll(0,JDZ_OBJ_PREFIX);
  }

#endif // JDZ_DRAW_MQH
