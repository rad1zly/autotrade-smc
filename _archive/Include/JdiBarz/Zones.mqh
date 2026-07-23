//+------------------------------------------------------------------+
//|                                                        Zones.mqh |
//|  JDI BARZ EA - deteksi FVG/OB + lifecycle OB->Valid/Breaker/Flip |
//|                dan engine per simbol (zona + entry)              |
//+------------------------------------------------------------------+
#ifndef JDZ_ZONES_MQH
#define JDZ_ZONES_MQH

#include "Structure.mqh"
#include "Confirm.mqh"
#include "TradeExec.mqh"
#include "Draw.mqh"

//+------------------------------------------------------------------+
//| Engine per simbol: memelihara daftar zona & mengeksekusi entry    |
//+------------------------------------------------------------------+
class CSymbolEngine
  {
private:
   string            m_symbol;
   bool              m_ready;
   datetime          m_zBar0;      // waktu bar 0 TF zona terakhir
   datetime          m_eBar0;      // waktu bar 0 TF entry terakhir
   double            m_atrZ;       // ATR TF zona terbaru
   SZone             m_zones[];
   SZone             m_pending[];  // flipzone baru: ditunda agar m_zones tidak
                                   // di-resize saat loop update masih memegang
                                   // referensi elemennya (dangling reference)
   SDiag             m_diag;       // funnel counter untuk diagnosa

   //--- util array zona -------------------------------------------------
   void              ZonesPush(const SZone &z)
     {
      int n=ArraySize(m_zones);
      ArrayResize(m_zones,n+1);
      m_zones[n]=z;
     }

   int               FindZone(const int kind,const datetime t0,const int dir) const
     {
      for(int i=0;i<ArraySize(m_zones);i++)
         if(m_zones[i].kind==kind && m_zones[i].t0==t0 && m_zones[i].dir==dir)
            return i;
      return -1;
     }

   void              PruneZones()
     {
      int n=ArraySize(m_zones);
      if(n<250)
         return;
      SZone keep[];
      int k=0;
      for(int i=0;i<n;i++)
        {
         if(m_zones[i].alive)
           {
            ArrayResize(keep,k+1);
            keep[k]=m_zones[i];
            k++;
           }
         else
            RemoveZoneObjects(m_symbol,m_zones[i]);
        }
      ArrayResize(m_zones,k);
      for(int i=0;i<k;i++)
         m_zones[i]=keep[i];
     }

   //--- lifecycle OB demand (dir=+1): tap dari atas, reaksi ke atas -----
   void              UpdateDemandOB(SZone &z,const MqlRates &r[],const int total,const int sh)
     {
      if(!z.touched)
        {
         if(r[sh].low<=z.top)
           {
            z.touched=true;
            double sw=LastSwingHighBefore(r,sh,g_cfg.swingBars,total);
            z.fib1=(sw>0.0 ? sw : z.liqLevel);   // high sebelum reaksi
            z.reactionExt=r[sh].high;
           }
         else
            z.liqLevel=MathMax(z.liqLevel,r[sh].high); // liquidity lawan terus di-update
        }
      else
        {
         z.reactionExt=MathMax(z.reactionExt,r[sh].high);
         if(!z.reacted && m_atrZ>0.0 && z.reactionExt>=z.top+g_cfg.minReactATR*m_atrZ)
            z.reacted=true;
        }

      // reaksi berhasil men-sweep liquidity lawan -> OB valid
      if(z.reacted && z.phase!=OBP_VALIDATED && z.reactionExt>=z.liqLevel)
        {
         z.phase=OBP_VALIDATED;
         z.tradable=g_cfg.tradeOB;
         m_diag.obValid++;
         if(g_cfg.debugLog)
            PrintFormat("[JDZ] %s OB demand %s VALID (liquidity swept)",
                        m_symbol,TimeToString(z.t0));
        }

      if(r[sh].close<z.bottom) // OB demand ditembus ke bawah
        {
         if(!z.reacted)
           {
            // tidak ada reaksi sama sekali -> breaker block (jadi supply)
            RemoveZoneObjects(m_symbol,z);
            z.kind=ZK_BREAKER;
            z.dir=-1;
            z.tradable=g_cfg.tradeBB;
            z.tphase=TP_FRESH;
            z.touchedAfter=false;
            z.age=0;
            m_diag.bb++;
            if(g_cfg.debugLog)
               PrintFormat("[JDZ] %s OB demand %s tanpa reaksi ditembus -> BREAKER supply",
                           m_symbol,TimeToString(z.t0));
           }
         else if(z.reactionExt>=z.liqLevel)
            z.alive=false; // OB valid sudah dimainkan, lalu tembus -> selesai
         else
           {
            // reaksi gagal sweep liquidity + OB tembus -> FLIPZONE supply
            SpawnFlip(z,r,total,sh);
            z.alive=false;
           }
        }
     }

   //--- lifecycle OB supply (dir=-1): tap dari bawah, reaksi ke bawah ---
   void              UpdateSupplyOB(SZone &z,const MqlRates &r[],const int total,const int sh)
     {
      if(!z.touched)
        {
         if(r[sh].high>=z.bottom)
           {
            z.touched=true;
            double sw=LastSwingLowBefore(r,sh,g_cfg.swingBars,total);
            z.fib1=(sw>0.0 ? sw : z.liqLevel);   // low sebelum reaksi
            z.reactionExt=r[sh].low;
           }
         else
            z.liqLevel=MathMin(z.liqLevel,r[sh].low);
        }
      else
        {
         z.reactionExt=MathMin(z.reactionExt,r[sh].low);
         if(!z.reacted && m_atrZ>0.0 && z.reactionExt<=z.bottom-g_cfg.minReactATR*m_atrZ)
            z.reacted=true;
        }

      if(z.reacted && z.phase!=OBP_VALIDATED && z.reactionExt<=z.liqLevel)
        {
         z.phase=OBP_VALIDATED;
         z.tradable=g_cfg.tradeOB;
         m_diag.obValid++;
         if(g_cfg.debugLog)
            PrintFormat("[JDZ] %s OB supply %s VALID (liquidity swept)",
                        m_symbol,TimeToString(z.t0));
        }

      if(r[sh].close>z.top) // OB supply ditembus ke atas
        {
         if(!z.reacted)
           {
            RemoveZoneObjects(m_symbol,z);
            z.kind=ZK_BREAKER;
            z.dir=1;
            z.tradable=g_cfg.tradeBB;
            z.tphase=TP_FRESH;
            z.touchedAfter=false;
            z.age=0;
            m_diag.bb++;
            if(g_cfg.debugLog)
               PrintFormat("[JDZ] %s OB supply %s tanpa reaksi ditembus -> BREAKER demand",
                           m_symbol,TimeToString(z.t0));
           }
         else if(z.reactionExt<=z.liqLevel)
            z.alive=false;
         else
           {
            SpawnFlip(z,r,total,sh);
            z.alive=false;
           }
        }
     }

   //--- buat flipzone dari OB yang reaksinya gagal sweep lalu tembus ----
   void              SpawnFlip(const SZone &ob,const MqlRates &r[],const int total,const int sh)
     {
      // origin leg penembus = candle berlawanan terakhir sebelum bar break
      int found=-1;
      for(int k=sh+1;k<=sh+12 && k<total;k++)
        {
         if(ob.dir>0)  { if(r[k].close>r[k].open){found=k;break;} } // demand tembus turun: cari candle bullish terakhir
         else          { if(r[k].close<r[k].open){found=k;break;} } // supply tembus naik: cari candle bearish terakhir
        }
      if(found<0)
        {
         PrintFormat("[JDZ] %s flipzone dari OB %s gagal dibuat (origin leg tidak ditemukan)",
                     m_symbol,TimeToString(ob.t0));
         return;
        }

      SZone f;
      InitZone(f);
      f.kind=ZK_FLIP;
      f.dir=-ob.dir;
      f.top=r[found].high;
      f.bottom=r[found].low;
      f.t0=r[found].time;
      f.tradable=g_cfg.tradeFlip;
      f.fib1=ob.fib1;

      // cek posisi flipzone terhadap 50% fib [fib1 .. postExt]
      if(f.dir>0)
        {
         f.postExt=r[sh].high;   // 0 = high setelah reaksi
         f.fibOk=(f.top<=f.fib1+g_cfg.flipFibMax*(f.postExt-f.fib1));
        }
      else
        {
         f.postExt=r[sh].low;    // 0 = low setelah reaksi
         f.fibOk=(f.bottom>=f.fib1-g_cfg.flipFibMax*(f.fib1-f.postExt));
        }

      // dedup: flipzone dari origin candle & arah yang sama cukup sekali
      if(FindZone(ZK_FLIP,f.t0,f.dir)>=0)
         return;
      for(int p=0;p<ArraySize(m_pending);p++)
         if(m_pending[p].t0==f.t0 && m_pending[p].dir==f.dir)
            return;

      // masuk antrean; di-push ke m_zones setelah loop update selesai
      int np=ArraySize(m_pending);
      ArrayResize(m_pending,np+1);
      m_pending[np]=f;

      m_diag.flip++;
      if(f.fibOk)
         m_diag.flipFibOk++;
      if(g_cfg.debugLog)
         PrintFormat("[JDZ] %s FLIPZONE %s terbentuk [%.5f .. %.5f] dari OB %s, fib %s",
                     m_symbol,f.dir>0?"demand":"supply",f.bottom,f.top,
                     TimeToString(ob.t0),f.fibOk?"OK":"belum lolos 50%");
     }

   //--- update satu zona dengan bar TF zona yang baru close -------------
   void              UpdateZoneWithBar(SZone &z,const MqlRates &r[],const int total,const int sh)
     {
      z.age++;
      if(z.age>g_cfg.maxZoneAge)
        {
         z.alive=false;
         if(z.tphase==TP_FRESH || z.tphase==TP_PRICE_IN)
            z.tphase=TP_INVALID;
         return;
        }

      if(z.kind==ZK_OB)
        {
         if(z.dir>0) UpdateDemandOB(z,r,total,sh);
         else        UpdateSupplyOB(z,r,total,sh);
         if(!z.alive || z.kind!=ZK_OB)
            return;   // mati / berubah jadi breaker: bar berikutnya diproses sebagai zona baru
        }

      // update anchor fib 0 flipzone selama belum diretest
      if(z.kind==ZK_FLIP && !z.touchedAfter)
        {
         if(z.dir>0)
           {
            z.postExt=MathMax(z.postExt,r[sh].high);
            if(!z.fibOk)
               z.fibOk=(z.top<=z.fib1+g_cfg.flipFibMax*(z.postExt-z.fib1));
           }
         else
           {
            z.postExt=MathMin(z.postExt,r[sh].low);
            if(!z.fibOk)
               z.fibOk=(z.bottom>=z.fib1-g_cfg.flipFibMax*(z.fib1-z.postExt));
           }
        }

      // catat kontak untuk zona tradable (dipakai deteksi retest basi saat init)
      if(z.kind!=ZK_OB || z.phase==OBP_VALIDATED)
         if(r[sh].low<=z.top && r[sh].high>=z.bottom)
            z.touchedAfter=true;

      // invalidasi close-through utk zona non-OB (OB ditangani lifecycle-nya)
      if(z.kind!=ZK_OB)
        {
         if((z.dir>0 && r[sh].close<z.bottom) ||
            (z.dir<0 && r[sh].close>z.top))
           {
            z.alive=false;
            if(z.tphase!=TP_TRADED)
               z.tphase=TP_INVALID;
           }
        }
     }

   //--- deteksi FVG baru + OB pembentuknya di bar sh ---------------------
   void              DetectNewZones(const MqlRates &r[],const int total,const int sh)
     {
      if(sh+14>=total)
         return;

      //=== displacement NAIK: FVG bullish (low[sh] > high[sh+2]) ===
      if(r[sh].low>r[sh+2].high)
        {
         double body=BodySize(r[sh+1]);
         if(m_atrZ<=0.0 || body>=g_cfg.dispBodyATR*m_atrZ)
           {
            if(FindZone(ZK_FVG,r[sh+1].time,1)<0)
              {
               SZone f;
               InitZone(f);
               f.kind=ZK_FVG; f.dir=1;
               f.top=r[sh].low; f.bottom=r[sh+2].high;
               f.t0=r[sh+1].time;
               f.tradable=g_cfg.tradeFVG;
               ZonesPush(f);
               m_diag.fvg++;
              }
            // OB = candle bearish terakhir sebelum leg naik
            int ob=-1;
            for(int k=sh+2;k<=sh+14;k++)
               if(r[k].close<r[k].open){ob=k;break;}
            if(ob>0 && FindZone(ZK_OB,r[ob].time,1)<0 && FindZone(ZK_BREAKER,r[ob].time,-1)<0)
              {
               SZone z;
               InitZone(z);
               z.kind=ZK_OB; z.dir=1;
               z.top=r[ob].high; z.bottom=r[ob].low;
               z.t0=r[ob].time;
               z.liqLevel=r[sh].high; // liquidity lawan awal = high displacement
               ZonesPush(z);
               m_diag.ob++;
              }
           }
        }

      //=== displacement TURUN: FVG bearish (high[sh] < low[sh+2]) ===
      if(r[sh].high<r[sh+2].low)
        {
         double body=BodySize(r[sh+1]);
         if(m_atrZ<=0.0 || body>=g_cfg.dispBodyATR*m_atrZ)
           {
            if(FindZone(ZK_FVG,r[sh+1].time,-1)<0)
              {
               SZone f;
               InitZone(f);
               f.kind=ZK_FVG; f.dir=-1;
               f.top=r[sh+2].low; f.bottom=r[sh].high;
               f.t0=r[sh+1].time;
               f.tradable=g_cfg.tradeFVG;
               ZonesPush(f);
               m_diag.fvg++;
              }
            // OB = candle bullish terakhir sebelum leg turun
            int ob=-1;
            for(int k=sh+2;k<=sh+14;k++)
               if(r[k].close>r[k].open){ob=k;break;}
            if(ob>0 && FindZone(ZK_OB,r[ob].time,-1)<0 && FindZone(ZK_BREAKER,r[ob].time,1)<0)
              {
               SZone z;
               InitZone(z);
               z.kind=ZK_OB; z.dir=-1;
               z.top=r[ob].high; z.bottom=r[ob].low;
               z.t0=r[ob].time;
               z.liqLevel=r[sh].low;
               ZonesPush(z);
               m_diag.ob++;
              }
           }
        }
     }

   //--- cari target TP = liquidity lama (swing yang belum di-sweep) -----
   //    sell: swing low di bawah entry yang low-nya belum pernah ditembus
   //          bar mana pun setelahnya; ambil yang TERDEKAT dengan harga
   //          tapi tetap memenuhi RR minimal. 0 = tidak ada target layak.
   double            FindLiquidityTP(const int dir,const double entry,const double risk)
     {
      if(risk<=0.0)
         return 0.0;
      MqlRates r[];
      ArraySetAsSeries(r,true);
      int total=CopyRates(m_symbol,g_cfg.ztf,0,320,r);
      if(total<60)
         return 0.0;
      int n=g_cfg.swingBars;
      double best=0.0;
      if(dir<0)
        {
         double runMin=DBL_MAX;   // low terendah dari bar-bar SETELAH kandidat
         for(int i=1;i<total-n;i++)
           {
            if(i>n && IsSwingLow(r,i,n,total))
              {
               double v=r[i].low;
               if(v<runMin && v<entry)          // belum di-sweep & di bawah entry
                 {
                  double rr=(entry-v)/risk;
                  if(rr>=g_cfg.minRRLiq && (best==0.0 || v>best))
                     best=v;                    // terdekat dgn harga yg memenuhi RR
                 }
              }
            runMin=MathMin(runMin,r[i].low);
           }
        }
      else
        {
         double runMax=-DBL_MAX;
         for(int i=1;i<total-n;i++)
           {
            if(i>n && IsSwingHigh(r,i,n,total))
              {
               double v=r[i].high;
               if(v>runMax && v>entry)
                 {
                  double rr=(v-entry)/risk;
                  if(rr>=g_cfg.minRRLiq && (best==0.0 || v<best))
                     best=v;
                 }
              }
            runMax=MathMax(runMax,r[i].high);
           }
        }
      return best;
     }

   //--- entry: retest zona + konfirmasi price action di TF entry --------
   void              TryEnter(SZone &z,const MqlRates &e[],const int total)
     {
      if(!z.alive || !z.tradable)
         return;
      if(z.tphase!=TP_FRESH && z.tphase!=TP_PRICE_IN)
         return;
      if(z.kind==ZK_OB && z.phase!=OBP_VALIDATED)
         return;                        // OB hanya ditradingkan setelah valid
      if(e[1].time<=z.t0)
         return;                        // bar entry harus setelah zona terbentuk

      bool contact=(e[1].low<=z.top && e[1].high>=z.bottom);
      if(!contact)
         return;
      if(z.tphase==TP_FRESH)
        {
         m_diag.retest++;
         if(g_cfg.debugLog)
            PrintFormat("[JDZ] %s retest %s zona %s @ %s - menunggu konfirmasi",
                        m_symbol,ZoneKindLabel(z),TimeToString(z.t0),
                        TimeToString(e[1].time));
        }
      z.tphase=TP_PRICE_IN;
      z.touchedAfter=true;

      if(z.kind==ZK_FLIP && !z.fibOk)
        {
         m_diag.fibBlock++;
         if(g_cfg.debugLog)
            PrintFormat("[JDZ] %s retest FLIP %s ditolak: belum lolos filter 50%% fib",
                        m_symbol,TimeToString(z.t0));
         return;
        }
      if(!ConfirmSignal(e,1,z.dir,z.top,z.bottom))
        {
         m_diag.confirmFail++;
         return;
        }
      if(!TimeFilterOK())
        {
         m_diag.filterBlock++;
         return;
        }
      if(g_cfg.maxSpreadPoints>0 &&
         SymbolInfoInteger(m_symbol,SYMBOL_SPREAD)>g_cfg.maxSpreadPoints)
        {
         m_diag.filterBlock++;
         PrintFormat("[JDZ] %s sinyal dilewati: spread %d > %d",
                     m_symbol,(int)SymbolInfoInteger(m_symbol,SYMBOL_SPREAD),
                     g_cfg.maxSpreadPoints);
         return;
        }
      if(CountOpenPositions(m_symbol,g_cfg.magic)>=g_cfg.maxPosPerSym)
        {
         m_diag.filterBlock++;
         return;
        }

      double sl;
      if(z.dir>0) sl=MathMin(z.bottom,e[1].low)-g_cfg.slBufATR*m_atrZ;
      else        sl=MathMax(z.top,e[1].high)+g_cfg.slBufATR*m_atrZ;

      // TP: liquidity lama atau RR tetap
      double tpOverride=0.0;
      if(g_cfg.tpMode==TP_LIQUIDITY)
        {
         double approxEntry=SymbolInfoDouble(m_symbol,z.dir>0 ? SYMBOL_ASK : SYMBOL_BID);
         double risk=MathAbs(approxEntry-sl);
         tpOverride=FindLiquidityTP(z.dir,approxEntry,risk);
         if(tpOverride<=0.0)
           {
            m_diag.tpBlock++;
            if(g_cfg.debugLog)
               PrintFormat("[JDZ] %s sinyal %s dilewati: tidak ada target liquidity dengan RR >= %.1f",
                           m_symbol,ZoneKindLabel(z),g_cfg.minRRLiq);
            return;
           }
        }

      string cmt="JDZ "+ZoneKindLabel(z);
      if(ExecMarket(m_symbol,z.dir,sl,tpOverride,cmt))
        {
         z.tphase=TP_TRADED;
         m_diag.entries++;
         PrintFormat("[JDZ] %s ENTRY %s via %s zona %s",
                     m_symbol,z.dir>0?"BUY":"SELL",
                     ZoneKindLabel(z),TimeToString(z.t0));
        }
      else
         m_diag.lotBlock++;
     }

   void              ProcessEntryBar()
     {
      MqlRates e[];
      ArraySetAsSeries(e,true);
      int total=CopyRates(m_symbol,g_cfg.etf,0,10,e);
      if(total<5)
         return;
      int n=ArraySize(m_zones);
      for(int i=0;i<n;i++)
         TryEnter(m_zones[i],e,total);
     }

public:
                     CSymbolEngine(const string sym)
     {
      m_symbol=sym;
      m_ready=false;
      m_zBar0=0;
      m_eBar0=0;
      m_atrZ=0.0;
      ZeroMemory(m_diag);
     }

   //--- laporan funnel sinyal (dipanggil di OnDeinit / akhir backtest) ---
   void              ReportDiag() const
     {
      PrintFormat("[JDZ] ===== FUNNEL %s =====",m_symbol);
      PrintFormat("[JDZ] FVG terdeteksi        : %d",m_diag.fvg);
      PrintFormat("[JDZ] OB terdeteksi         : %d",m_diag.ob);
      PrintFormat("[JDZ]   -> OB valid (sweep) : %d",m_diag.obValid);
      PrintFormat("[JDZ]   -> jadi BREAKER     : %d",m_diag.bb);
      PrintFormat("[JDZ]   -> jadi FLIPZONE    : %d (fib OK saat lahir: %d)",m_diag.flip,m_diag.flipFibOk);
      PrintFormat("[JDZ] Retest zona tradable  : %d",m_diag.retest);
      PrintFormat("[JDZ]   ditolak fib 50%%     : %d",m_diag.fibBlock);
      PrintFormat("[JDZ]   konfirmasi gagal    : %d",m_diag.confirmFail);
      PrintFormat("[JDZ]   ditolak filter      : %d",m_diag.filterBlock);
      PrintFormat("[JDZ]   ditolak target RR   : %d",m_diag.tpBlock);
      PrintFormat("[JDZ]   order gagal (lot/dll): %d",m_diag.lotBlock);
      PrintFormat("[JDZ] ENTRY terkirim        : %d",m_diag.entries);
     }

   string            Symbol() const { return m_symbol; }
   bool              Ready()  const { return m_ready;  }

   int               CountAlive(const int kind) const
     {
      int c=0;
      for(int i=0;i<ArraySize(m_zones);i++)
         if(m_zones[i].alive && (kind<0 || m_zones[i].kind==kind))
            c++;
      return c;
     }

   //--- proses satu bar TF zona yang baru close (sh = shift bar itu) ----
   void              ProcessZoneBar(const MqlRates &r[],const int total,const int sh,const bool replay)
     {
      m_atrZ=SimpleATR(r,sh,g_cfg.atrPeriod,total);
      int n=ArraySize(m_zones); // zona yang lahir bar ini baru diproses bar berikutnya
      for(int i=0;i<n;i++)
         if(m_zones[i].alive)
            UpdateZoneWithBar(m_zones[i],r,total,sh);
      // flush flipzone yang lahir bar ini (aman: loop di atas sudah selesai)
      for(int p=0;p<ArraySize(m_pending);p++)
         ZonesPush(m_pending[p]);
      ArrayResize(m_pending,0);
      DetectNewZones(r,total,sh);
      if(!replay)
        {
         PruneZones();
         if(g_cfg.draw)
            RedrawAll(r[sh].time+2*PeriodSeconds(g_cfg.ztf));
        }
     }

   //--- replay histori supaya state zona konsisten sejak awal -----------
   bool              InitHistory()
     {
      MqlRates r[];
      ArraySetAsSeries(r,true);
      int need=g_cfg.historyBars+40;
      int total=CopyRates(m_symbol,g_cfg.ztf,0,need,r);
      if(total<120)
         return false; // data belum siap, dicoba lagi tick/timer berikutnya

      int start=MathMin(total-30,g_cfg.historyBars);
      for(int sh=start;sh>=1;sh--)
         ProcessZoneBar(r,total,sh,true);

      // retest yang sudah terjadi di histori dianggap hangus (konfirmasinya sudah lewat)
      int stale=0;
      for(int i=0;i<ArraySize(m_zones);i++)
         if(m_zones[i].alive && m_zones[i].tradable && m_zones[i].touchedAfter &&
            (m_zones[i].tphase==TP_FRESH || m_zones[i].tphase==TP_PRICE_IN))
           {
            m_zones[i].tphase=TP_TRADED;
            stale++;
           }

      m_zBar0=iTime(m_symbol,g_cfg.ztf,0);
      m_eBar0=iTime(m_symbol,g_cfg.etf,0);
      if(g_cfg.draw)
         RedrawAll(TimeCurrent()+PeriodSeconds(g_cfg.ztf));
      m_ready=true;
      PrintFormat("[JDZ] %s siap: %d bar histori, %d zona hidup (FLIP %d, OB %d, BB %d, FVG %d), %d retest hangus",
                  m_symbol,start,CountAlive(-1),CountAlive(ZK_FLIP),CountAlive(ZK_OB),
                  CountAlive(ZK_BREAKER),CountAlive(ZK_FVG),stale);
      return true;
     }

   //--- dipanggil dari OnTick / OnTimer ---------------------------------
   void              Update()
     {
      if(!m_ready)
        {
         InitHistory();
         return;
        }
      ManageOpenPositions(m_symbol);   // partial close + SL ke BE
      datetime z0=iTime(m_symbol,g_cfg.ztf,0);
      if(z0>0 && z0!=m_zBar0)
        {
         m_zBar0=z0;
         MqlRates r[];
         ArraySetAsSeries(r,true);
         int total=CopyRates(m_symbol,g_cfg.ztf,0,650,r);
         if(total>=60)
            ProcessZoneBar(r,total,1,false);
        }
      datetime e0=iTime(m_symbol,g_cfg.etf,0);
      if(e0>0 && e0!=m_eBar0)
        {
         m_eBar0=e0;
         ProcessEntryBar();
        }
     }

   void              RedrawAll(const datetime rightTime)
     {
      if(m_symbol!=_Symbol)
         return;
      for(int i=0;i<ArraySize(m_zones);i++)
        {
         if(m_zones[i].alive)
            DrawZoneRect(m_symbol,m_zones[i],rightTime);
         else
            RemoveZoneObjects(m_symbol,m_zones[i]);
        }
      ChartRedraw(0);
     }
  };

#endif // JDZ_ZONES_MQH
