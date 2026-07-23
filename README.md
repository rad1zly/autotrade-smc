# PAF-QIE — Prof AF Quantum Institutional Engine

EA MT5 dengan engine deteksi **market structure shift (MSS)** + **otak keputusan
LLM** yang jalan di Python (default: **Minimax**, bisa ganti Anthropic tinggal env
var). Engine men-track struktur swing high/low secara berkelanjutan — begitu harga
close menembus swing berlawanan yang paling baru confirmed, itu MSS, dan **LLM yang
memutuskan** BUY / SELL / SKIP beserta level SL & TP secara diskresioner — bukan
hard rule. Karena otaknya di Python, strategi yang sama bisa **di-backtest** dengan
data historis (CSV export MT5) sebelum dipakai live.

> Project riset lama (JDI BARZ + backtest engine strategi lama) diarsipkan utuh
> di `_archive/`.

## Arsitektur — kenapa dibelah MQL5 + Python

- **MetaTrader5 (Python library resmi) itu Windows-only** — tidak jalan native di
  Mac. Supaya bisa develop & backtest di Mac tanpa VM Windows, pembagian tugasnya:
  - **MQL5** (jalan di terminal MT5 kamu, di mana pun itu — Windows/VPS/Wine)
    tetap yang deteksi struktur pasar (swing/bias/MSS, pool TP, FVG) dan
    **eksekusi order** — kode existing dipakai lagi, hanya bagian panggil-LLM
    yang diganti.
  - **Python** (jalan di Mac kamu) yang jadi **otak keputusan**: terima konteks
    setup dari EA lewat bridge HTTP lokal, susun prompt, panggil LLM (Minimax/
    Anthropic/lainnya), validasi (RR, confidence, lebar SL), balikin keputusan.
  - **Python juga** yang jalankan **backtest** — engine deteksi setup yang sama
    persis (structure/MSS) dijalankan di atas CSV historis, lalu memanggil modul
    otak yang **sama** (bukan logic duplikat) untuk tiap setup confirmed.

```
brain/decision.py             — otak: system prompt, decide()/decide_mock(), validasi
brain/providers.py            — Minimax (OpenAI-compatible) & Anthropic, gampang tambah lain
brain/config.py               — baca .env (kunci API TIDAK PERNAH masuk ke MQL5)
brain/server.py                — bridge HTTP lokal yang dipanggil EA (WebRequest)

backtest/common.py            — util: load CSV MT5, resample, ATR, compute_structure (MSS)
backtest/smc_engine.py        — pool TP + deteksi MSS + panggil brain per setup
backtest/run_backtest.py      — CLI: --mode mock (cepat, tanpa LLM) / --mode llm (sungguhan)

Experts/PafQieEA.mq5           — state machine live + eksekusi order
Include/PafQie/Types.mqh       — enum & struct bersama
Include/PafQie/Structure.mqh   — swing fractal, trend H1, PafComputeStructure (MSS), FVG
Include/PafQie/Liquidity.mqh   — pool TP target (PDH/PDL, Asia range, swing H1)
Include/PafQie/Brain.mqh       — bangun JSON konteks, POST ke bridge Python, parse balasan
Include/PafQie/Dashboard.mqh   — panel on-chart hitam-emas gaya PAF-QIE
Include/PafQie/TradeExec.mqh   — lot sizing 1% risk (guard minLot), partial + BE
```

## Alur — Market Structure Shift (MSS)

**MSS itu murni break structure, TIDAK butuh sweep pool liquidity eksternal dulu.**
Swing high/low di-track terus-menerus (fractal-n bar). Bias (bullish/bearish)
ditentukan dari swing mana yang confirmed paling akhir; level swing berlawanan
yang paling baru ("referensi") itu yang dijaga. Begitu close menembus level
referensi itu → bias flip → **MSS confirmed**. Kalau harga malah lanjut searah
bias (bikin high/low baru yang lebih ekstrem) itu BOS/lanjutan, bukan reversal —
tidak memicu apa pun, cuma update level referensi.

Pool liquidity (PDH/PDL, Asia range, swing H1) **bukan** syarat entry — itu murni
daftar kandidat **target TP** (magnet arah) yang dikirim sebagai konteks ke otak.

SEARCHING → TRACKING (bias sudah ada) → **ENTRY READY** (MSS baru confirmed)
→ *(otak dipanggil)* → TRADE ACTIVE → MANAGE (partial @1.5R + SL→BE).

Saat ENTRY READY: konteks (40 candle terakhir, ATR, spread, pool TP, level yang
baru ditembus) dikirim ke otak. Otak membalas
`{"action","sl","tp","confidence","reason"}`. Eksekusi hanya jika lolos validasi
(arah = bias, RR ≥ min, confidence ≥ min, lebar SL ≤ 5×ATR, **SL di luar level
yang baru ditembus**) — dicek **dua kali**: sekali di Python (brain/decision.py,
sumber kebenaran tunggal, sama untuk live & backtest), sekali lagi di EA sebagai
lapis kedua sebelum order sungguhan dikirim (jangan pernah percaya penuh ke
server eksternal untuk uang beneran).

## Setup — Backtest (Mac, tanpa MT5 sama sekali)

```bash
cd /Users/dalinfo-air-01/Fadhil/mt5project
pip3 install -r requirements.txt
cp .env.example .env        # isi MINIMAX_API_KEY (lihat bagian Minimax di bawah)

# uji pipa dulu, cepat, tanpa panggil LLM (SL/TP rule-based sementara)
cd backtest
python3 run_backtest.py --selftest --mode mock

# backtest sungguhan dengan data kamu (export CSV M15 dari MT5)
python3 run_backtest.py --csv /path/ke/XAUUSD_M15.csv --mode mock --out out_mock

# baru setelah plumbing oke, uji dengan LLM sungguhan (BERBAYAR per token —
# mulai dari rentang tanggal pendek dulu pakai --from/--to)
python3 run_backtest.py --csv /path/ke/XAUUSD_M15.csv --mode llm \
  --from 2025-06-01 --to 2025-09-01 --out out_llm
```

Output di folder `--out`: `summary.txt` (funnel + alasan brain menolak + WR/PF/
expectancy/DD), `trades.csv` (semua trade termasuk confidence & alasan LLM),
`equity_r.png`, `trade_NNN.png` per trade.

**Penting soal mode:** `--mode mock` pakai rumus SL/TP tetap (bukan LLM) — hanya
untuk uji frekuensi setup & plumbing dengan cepat/gratis. `--mode llm` yang
memanggil LLM sungguhan dan itulah yang benar-benar mengevaluasi kualitas
keputusan diskresioner otaknya. Jangan simpulkan kualitas strategi dari mode mock.

## Setup — Live (EA + bridge)

1. Jalankan bridge Python (di mesin manapun yang bisa diakses terminal MT5 kamu —
   kalau MT5 juga di Mac yang sama, ya di situ juga):
   ```bash
   python3 -m brain.server
   ```
2. Copy `Experts/PafQieEA.mq5` → `MQL5/Experts/`, `Include/PafQie/` →
   `MQL5/Include/PafQie/`, compile di MetaEditor.
3. **Wajib:** MT5 → Tools → Options → Expert Advisors → centang *Allow WebRequest
   for listed URL* → tambahkan `http://127.0.0.1:8787` (atau URL bridge kamu).
4. Pasang EA di chart (disarankan M15). `InpBrainUrl` default sudah mengarah ke
   bridge lokal. Mulai dengan `InpAutoTrade=false` (mode sinyal/alert saja).
5. Jurnal keputusan tersimpan di `MQL5/Files/PAFQIE_journal.csv`.

## Ganti provider LLM (Minimax default, atau Anthropic)

Semua di `.env`, tidak ada yang perlu diubah di MQL5 atau di kode:

```bash
LLM_PROVIDER=minimax
MINIMAX_API_KEY=isi_disini
MINIMAX_BASE_URL=https://api.minimax.io/v1   # cek console Minimax kamu, ini bisa berubah
MINIMAX_MODEL=MiniMax-M2                     # cek nama model terkini di console
```

Minimax expose endpoint chat-completions yang **OpenAI-compatible**, jadi
integrasinya cuma HTTP request biasa (`brain/providers.py:MinimaxProvider`) —
gampang ditambah provider lain (OpenAI, OpenRouter, dst.) dengan pola yang sama.
Untuk balik ke Anthropic: `LLM_PROVIDER=anthropic` + isi `ANTHROPIC_API_KEY`.

## Batasan penting

- MetaTrader5 Python library **tidak dipakai** di sini justru karena Windows-only
  — itu sebabnya arsitekturnya dibelah (Python = otak & backtest, MQL5 = eksekusi).
  Kalau nanti mau full-Python live (tanpa MQL5 sama sekali), itu tetap butuh
  Windows/VM/Wine untuk terminal MT5-nya.
- LLM = pengambil keputusan diskresioner, bukan jaminan edge. Validasi (RR min,
  confidence min, sizing guard) membatasi kerusakan, tapi profitabilitas tetap
  harus dibuktikan lewat backtest `--mode llm` + forward test — bukan diasumsikan.
- Backtest `--mode llm` memanggil API sungguhan per setup confirmed (bukan per
  bar) — biayanya wajar, tapi tetap uji di rentang tanggal pendek dulu sebelum
  menjalankan ke seluruh histori multi-tahun.
