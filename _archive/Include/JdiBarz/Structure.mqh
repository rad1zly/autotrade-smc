//+------------------------------------------------------------------+
//|                                                    Structure.mqh |
//|  JDI BARZ EA - tipe data, konfigurasi, util struktur pasar       |
//+------------------------------------------------------------------+
#ifndef JDZ_STRUCTURE_MQH
#define JDZ_STRUCTURE_MQH

//--- mode konfirmasi price action di zona
enum ENUM_CONFIRM_MODE
  {
   CONFIRM_ENGULF    = 0, // Engulfing saja
   CONFIRM_REJECTION = 1, // Rejection / pin-bar saja
   CONFIRM_EITHER    = 2, // Engulfing ATAU rejection
   CONFIRM_CLOSE     = 3  // Candle close searah saja (paling longgar)
  };

//--- jenis zona
enum ENUM_ZONE_KIND
  {
   ZK_FVG     = 0,
   ZK_OB      = 1,
   ZK_BREAKER = 2,
   ZK_FLIP    = 3
  };

//--- fase lifecycle order block
enum ENUM_OB_PHASE
  {
   OBP_WAIT      = 0,  // belum tervalidasi
   OBP_VALIDATED = 1   // reaksi men-sweep liquidity (OB valid)
  };

//--- mode take profit
enum ENUM_TP_MODE
  {
   TP_FIXED_RR  = 0, // RR tetap dari jarak SL
   TP_LIQUIDITY = 1  // Liquidity lama (swing low/high belum di-sweep)
  };

//--- fase trading zona
enum ENUM_TRADE_PHASE
  {
   TP_FRESH    = 0,
   TP_PRICE_IN = 1,
   TP_TRADED   = 2,
   TP_INVALID  = 3
  };

//--- satu zona (FVG / OB / Breaker / Flipzone)
struct SZone
  {
   int      kind;          // ENUM_ZONE_KIND
   int      dir;           // +1 demand (buy), -1 supply (sell)
   double   top;
   double   bottom;
   datetime t0;            // waktu candle pembentuk zona (TF zona)
   //--- lifecycle OB
   int      phase;         // ENUM_OB_PHASE
   bool     touched;       // OB sudah di-tap harga
   bool     reacted;       // ada reaksi berarti dari OB
   double   liqLevel;      // liquidity lawan yang harus di-sweep reaksi
   double   fib1;          // anchor fib 1: low/high sebelum reaksi
   double   reactionExt;   // ekstrem reaksi setelah tap
   //--- khusus flipzone
   double   postExt;       // anchor fib 0: ekstrem setelah OB ditembus
   bool     fibOk;         // flipzone berada di sisi diskon (<50%) fib
   //--- trading
   int      tphase;        // ENUM_TRADE_PHASE
   bool     tradable;
   bool     alive;
   int      age;           // umur dalam bar TF zona
   bool     touchedAfter;  // pernah disentuh lagi setelah jadi zona tradable
  };

//--- konfigurasi global (diisi dari input EA di OnInit)
struct SConfig
  {
   ENUM_TIMEFRAMES ztf;             // timeframe zona
   ENUM_TIMEFRAMES etf;             // timeframe entry
   int             swingBars;       // fractal N kiri/kanan
   int             atrPeriod;
   double          dispBodyATR;     // body minimal candle displacement (x ATR)
   double          minReactATR;     // jarak reaksi minimal dari OB (x ATR)
   int             maxZoneAge;      // umur maksimal zona (bar TF zona)
   int             historyBars;     // bar histori yang di-replay saat init
   double          flipFibMax;      // batas fib flipzone (default 0.5)
   int             confirmMode;     // ENUM_CONFIRM_MODE
   double          wickRatio;       // rasio wick minimal utk rejection
   double          riskPct;
   double          rr;              // RR tetap (mode TP_FIXED_RR)
   int             tpMode;          // ENUM_TP_MODE
   double          minRRLiq;        // RR minimal ke target liquidity, kurang = skip
   bool            usePartial;      // partial close + SL ke BE saat profit berjalan
   double          partialAtRR;     // trigger partial (x risiko awal)
   double          partialPct;      // % posisi yang ditutup
   double          maxLotRiskMult;  // batas toleransi risiko saat lot dibulatkan ke lot minimum
   double          slBufATR;
   int             maxPosPerSym;
   long            magic;
   int             maxSpreadPoints; // 0 = nonaktif
   bool            tradeFlip;
   bool            tradeOB;
   bool            tradeBB;
   bool            tradeFVG;
   bool            draw;
   bool            useTimeFilter;
   int             hourStart;
   int             hourEnd;
   bool            debugLog;        // log detail alasan sinyal lolos/ditolak
  };

//--- penghitung funnel sinyal per simbol (untuk diagnosa "kenapa tidak ada trade")
struct SDiag
  {
   int fvg;          // FVG terdeteksi
   int ob;           // OB terdeteksi
   int obValid;      // OB tervalidasi (liquidity swept)
   int bb;           // OB jadi breaker
   int flip;         // flipzone terbentuk
   int flipFibOk;    // flipzone langsung lolos filter 50% saat dibuat
   int retest;       // zona tradable diretest harga
   int confirmFail;  // retest ada, konfirmasi candle tidak muncul
   int tpBlock;      // sinyal valid tapi target liquidity < RR minimal
   int lotBlock;     // sinyal valid tapi lot minimum bikin risiko meledak
   int fibBlock;     // retest flip ditolak karena belum <50% fib
   int filterBlock;  // ditolak filter jam/spread/max posisi
   int entries;      // order terkirim
  };

SConfig g_cfg;

//+------------------------------------------------------------------+
//| Inisialisasi field zona ke nilai default                          |
//+------------------------------------------------------------------+
void InitZone(SZone &z)
  {
   z.kind=ZK_OB;   z.dir=0;
   z.top=0.0;      z.bottom=0.0;    z.t0=0;
   z.phase=OBP_WAIT;
   z.touched=false; z.reacted=false;
   z.liqLevel=0.0; z.fib1=0.0;      z.reactionExt=0.0;
   z.postExt=0.0;  z.fibOk=false;
   z.tphase=TP_FRESH;
   z.tradable=false;
   z.alive=true;
   z.age=0;
   z.touchedAfter=false;
  }

//+------------------------------------------------------------------+
//| Swing high/low fractal N bar kiri-kanan (array series)            |
//+------------------------------------------------------------------+
bool IsSwingHigh(const MqlRates &r[],const int i,const int n,const int total)
  {
   if(i-n<0 || i+n>=total)
      return false;
   double h=r[i].high;
   for(int k=1;k<=n;k++)
      if(r[i-k].high>=h || r[i+k].high>=h)
         return false;
   return true;
  }

bool IsSwingLow(const MqlRates &r[],const int i,const int n,const int total)
  {
   if(i-n<0 || i+n>=total)
      return false;
   double l=r[i].low;
   for(int k=1;k<=n;k++)
      if(r[i-k].low<=l || r[i+k].low<=l)
         return false;
   return true;
  }

//+------------------------------------------------------------------+
//| Swing high/low terkonfirmasi paling baru SEBELUM index fromIdx    |
//| (hanya memakai bar yang sudah close pada saat fromIdx)            |
//+------------------------------------------------------------------+
double LastSwingHighBefore(const MqlRates &r[],const int fromIdx,const int n,const int total)
  {
   int maxScan=fromIdx+n+200;
   for(int i=fromIdx+n;i<total-n && i<maxScan;i++)
      if(IsSwingHigh(r,i,n,total))
         return r[i].high;
   return 0.0;
  }

double LastSwingLowBefore(const MqlRates &r[],const int fromIdx,const int n,const int total)
  {
   int maxScan=fromIdx+n+200;
   for(int i=fromIdx+n;i<total-n && i<maxScan;i++)
      if(IsSwingLow(r,i,n,total))
         return r[i].low;
   return 0.0;
  }

//+------------------------------------------------------------------+
//| ATR sederhana dihitung langsung dari array rates (tanpa handle)   |
//| supaya konsisten antara replay histori dan live                   |
//+------------------------------------------------------------------+
double SimpleATR(const MqlRates &r[],const int sh,const int period,const int total)
  {
   int cnt=0;
   double sum=0.0;
   for(int i=sh;i<sh+period && i+1<total;i++)
     {
      double tr=MathMax(r[i].high,r[i+1].close)-MathMin(r[i].low,r[i+1].close);
      sum+=tr;
      cnt++;
     }
   return (cnt>0 ? sum/cnt : 0.0);
  }

double BodySize(const MqlRates &bar)
  {
   return MathAbs(bar.close-bar.open);
  }

//+------------------------------------------------------------------+
//| Filter jam trading (waktu server)                                 |
//+------------------------------------------------------------------+
bool TimeFilterOK()
  {
   if(!g_cfg.useTimeFilter)
      return true;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(),dt);
   if(g_cfg.hourStart<=g_cfg.hourEnd)
      return (dt.hour>=g_cfg.hourStart && dt.hour<g_cfg.hourEnd);
   return (dt.hour>=g_cfg.hourStart || dt.hour<g_cfg.hourEnd); // rentang lewat tengah malam
  }

#endif // JDZ_STRUCTURE_MQH
