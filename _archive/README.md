# JDI BARZ EA — MT5

EA otomatis MQL5 yang mengidentifikasi **Order-Block, Breaker-Block, Flip-Zone, dan FVG** (konsep template JDI BARZ), lalu entry **tanpa limit order**: EA menunggu harga retest zona dan baru mengeksekusi **market order** setelah ada konfirmasi price action (reaction candle). Satu EA memantau beberapa simbol sekaligus dari satu chart (default: `NAS100, BTCUSD, XAUUSD`).

## Logika strategi

### Lifecycle Order-Block

Setiap displacement (leg impulsif yang meninggalkan FVG) membentuk OB = candle berlawanan terakhir sebelum leg. OB kemudian diklasifikasikan:

| Kejadian | Hasil | Label chart |
|---|---|---|
| Reaksi di OB **men-sweep** liquidity lawan | **OB Valid** | `OB*` (biru) |
| **Tidak ada reaksi**, OB langsung ditembus | **Breaker-Block** — zona berbalik arah | `BB` (merah muda) |
| Ada reaksi tapi **gagal sweep** liquidity, lalu OB ditembus | **Flip-Zone** — origin leg penembus jadi zona baru | `FLIP` (hijau) |

### Flip-Zone (setup utama yang ditradingkan)

1. Reaksi OB gagal men-sweep liquidity lawan (swing sebelumnya tidak tersentuh).
2. Harga balik dan close menembus OB.
3. Flipzone = candle berlawanan terakhir sebelum leg penembus.
4. **Filter 50%**: flipzone harus berada di sisi diskon — di bawah 50% fib dari `1 = low/high sebelum reaksi` ke `0 = high/low setelah reaksi`. Sebelum lolos filter ini, label di chart tampil `FLIP?` dan zona tidak ditradingkan.
5. Saat harga **retest** flipzone, EA menunggu **reaction candle** di TF entry (engulfing atau rejection/pin-bar searah bias, close tidak menembus zona) → **market order**.

- SL: sisi jauh zona (atau low/high candle konfirmasi jika lebih ekstrem) + buffer `x ATR`.
- TP (default `TP_LIQUIDITY`): **liquidity lama** — swing low/high di TF zona yang belum pernah di-sweep, terdekat dengan harga tapi jaraknya minimal `InpMinRRLiq` x risiko (default 1:5). Tidak ada target layak = sinyal di-skip (tercatat di funnel sebagai "ditolak target RR"). Mode `TP_FIXED_RR` tersedia untuk TP rasio tetap.
- Manajemen posisi: saat profit berjalan mencapai `InpPartialAtRR` x risiko (default 3R), EA menutup `InpPartialPct`% posisi (default 50%) dan memindahkan **SL ke breakeven**; sisanya dibiarkan lari ke TP liquidity.
- Lot: dihitung dari `% risk per trade` terhadap equity.
- Satu trade per zona; zona mati jika close menembus sisi jauhnya atau umurnya lewat batas.

OB valid, Breaker, dan FVG juga dideteksi & digambar; bisa ikut ditradingkan lewat input `InpTradeOB / InpTradeBB / InpTradeFVG` (default off, mesin entry-nya sama).

## Instalasi

1. Buka MetaTrader 5 → `File > Open Data Folder` → masuk folder `MQL5\`.
2. Salin isi repo ini:
   - `Experts/JdiBarzEA.mq5` → `MQL5\Experts\`
   - `Include/JdiBarz/` (seluruh folder) → `MQL5\Include\JdiBarz\`
3. Buka MetaEditor (F4), buka `Experts\JdiBarzEA.mq5`, tekan **Compile** (F7). Target: 0 error.
4. Pasang EA di chart mana pun — default-nya EA trade **simbol chart itu**. Untuk memantau beberapa simbol dari satu chart, isi `InpSymbols` (mis. `NAS100,BTCUSD,XAUUSD`, sesuaikan nama simbol broker); zona hanya digambar untuk simbol chart aktif.
5. Pastikan **AutoTrading** aktif dan ketiga simbol ada di Market Watch.

> **Nama simbol broker**: kalau broker memakai nama lain (mis. `US100`, `USTEC`, `XAUUSD.a`), sesuaikan input `InpSymbols`. EA menulis warning di tab Experts jika simbol tidak ditemukan.

## Input penting

| Input | Default | Keterangan |
|---|---|---|
| `InpSymbols` | *(kosong)* | Kosong = trade simbol chart ini saja. Isi `NAS100,BTCUSD,XAUUSD` (sesuai nama broker) untuk multi-simbol |
| `InpZoneTF` / `InpEntryTF` | H1 / M15 | TF deteksi zona / TF konfirmasi entry — bebas diubah |
| `InpSwingBars` | 3 | Sensitivitas swing (liquidity) |
| `InpMinReactATR` | 0.5 | Jarak minimal supaya dihitung "ada reaksi" di OB |
| `InpFlipFibMax` | 0.5 | Batas fib flipzone (0.5 = harus di bawah 50%) |
| `InpConfirmMode` | Either | Engulfing / Rejection / salah satu |
| `InpRiskPercent` | 1.0 | Risiko per trade (% equity) |
| `InpTPMode` / `InpMinRRLiq` | Liquidity / 5.0 | TP ke liquidity lama; skip jika RR < minimal |
| `InpUsePartial` / `InpPartialAtRR` / `InpPartialPct` | true / 3.0 / 50 | Partial close di 3R + SL ke breakeven |
| `InpMaxLotRiskMult` | 1.5 | Batal trade jika lot minimum broker memaksa risiko > 1.5x target |
| `InpMaxPosPerSymbol` | 1 | Posisi bersamaan per simbol |
| `InpMaxSpreadPts` | 0 | Lewati sinyal saat spread lebar (0 = off) |

## Backtest (Strategy Tester)

1. Buka Strategy Tester (Ctrl+R) → pilih `JdiBarzEA`.
2. Pilih salah satu simbol (mis. XAUUSD) sebagai simbol chart; MT5 otomatis memuat data simbol lain yang dibutuhkan (tester MT5 mendukung multi-simbol).
3. Mode **Every tick based on real ticks** untuk hasil paling akurat.
4. Aktifkan **Visual mode** untuk memverifikasi: zona `OB`/`BB`/`FLIP`/`FVG` tergambar sesuai template, dan entry hanya muncul setelah reaction candle di retest flipzone.
5. Untuk fokus per simbol saat optimasi, isi `InpSymbols` dengan satu simbol saja.

## Troubleshooting: tidak ada trade?

Dengan `InpDebugLog = true` (default), di akhir backtest EA mencetak **laporan funnel** per simbol di tab Journal:

```
[JDZ] ===== FUNNEL XAUUSD =====
[JDZ] FVG terdeteksi        : 120
[JDZ] OB terdeteksi         : 95
[JDZ]   -> jadi FLIPZONE    : 14 (fib OK saat lahir: 6)
[JDZ] Retest zona tradable  : 9
[JDZ]   konfirmasi gagal    : 7
[JDZ] ENTRY terkirim        : 2
```

Baca dari atas ke bawah — angka pertama yang nol menunjukkan tahap yang menyumbat:

- **FVG/OB = 0** → data histori kurang (perpanjang periode test / tunggu simbol termuat) atau simbol tidak valid.
- **FLIPZONE = 0** → naikkan `InpMaxZoneAge`, atau turunkan `InpMinReactATR` (reaksi kecil dianggap "tanpa reaksi" → lari ke Breaker).
- **fib OK = 0** → longgarkan `InpFlipFibMax` (mis. 0.6).
- **Retest = 0** → zona ada tapi harga tidak kembali; wajar jika periode test pendek.
- **Konfirmasi gagal tinggi** → ganti `InpConfirmMode` ke `CONFIRM_CLOSE` (paling longgar) untuk melihat potensi maksimal, lalu ketatkan lagi.
- **Ditolak target RR tinggi** → turunkan `InpMinRRLiq` (mis. 3.0) atau ganti `InpTPMode` ke `TP_FIXED_RR`.
- **Order gagal (lot/dll) tinggi, atau equity anjlok padahal risk 1%** → broker menetapkan lot minimum (mis. 0.01 untuk XAUUSD). Untuk simbol dengan tick value besar (XAUUSD, BTCUSD), 0.01 lot bisa berarti $1+ per $1 pergerakan harga — jika jarak SL lebar (zona besar), lot minimum itu sendirian sudah melebihi risiko 1% yang diminta, kadang sampai 5-10x lipat. EA membatalkan trade seperti itu (bukan memaksakan lot minimum) jika risikonya melebihi `InpMaxLotRiskMult` x target — naikkan nilainya untuk lebih permisif, atau turunkan agar lebih protektif. Modal kecil ($1000) dengan XAUUSD memang rawan kena batasan ini karena granularitas lot minimum broker.

## Catatan perilaku

- Saat EA baru dipasang, ia me-replay `InpHistoryBars` bar histori supaya peta zona langsung terbentuk. Zona yang retest-nya **sudah terjadi di masa lalu** ditandai hangus (tidak ditradingkan) karena candle konfirmasinya sudah lewat.
- Keputusan diambil pada **candle close** (TF zona untuk lifecycle zona, TF entry untuk konfirmasi) — tidak repaint.
- Status ringkas per simbol tampil di pojok kiri atas chart.

## Disclaimer

Ini alat bantu eksekusi, bukan jaminan profit. Wajib backtest + forward test di akun demo sebelum dipakai di akun riil, dan mulai dengan risk kecil.
