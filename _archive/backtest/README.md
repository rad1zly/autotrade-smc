# Backtest & Forward Test — SMC Liquidity-to-Liquidity

Engine Python yang mensimulasikan strategi SMC: sweep pool liquidity (PDH/PDL,
Asia high/low, swing H1) → CHoCH → entry limit di FVG displacement → TP ke
pool liquidity di seberang (RR ≥ 2). Hasil dalam **R-multiple** (kelipatan
risiko awal), jadi bebas dari urusan lot/deposit.

## Persiapan (sekali saja)

```bash
pip3 install --user pandas numpy matplotlib
```

Export data dari MT5: buka chart simbol → `Ctrl+S` / klik-kanan → *Save*
(atau View > Symbols > Bars > Export) → pilih **M15** → simpan CSV.
Makin panjang periodenya makin baik (minimal 1 tahun).

## 1. Backtest

```bash
cd /Users/dalinfo-air-01/Fadhil/mt5project/backtest

# backtest penuh
python3 run_backtest.py --strategy smc \
  --csv "/Users/dalinfo-air-01/Downloads/XAUUSD+_M15_202204250715_202607172345.csv" \
  --out output_smc --max-charts 150
```

Opsi penting:

| Opsi | Default | Fungsi |
|---|---|---|
| `--min-rr` | 2.0 | RR minimal ke target liquidity |
| `--from` / `--to` | - | batasi periode, format `YYYY-MM-DD` |
| `--out` | output | folder hasil |
| `--max-charts` | 40 | jumlah chart per-trade yang digambar |
| `--spread` | auto | override spread (satuan harga) |

## 2. Protokol yang BENAR (supaya hasilnya jujur)

Jangan menilai dari satu run atas seluruh data. Pisahkan:

```bash
# in-sample: untuk melihat & (nanti) tuning
python3 run_backtest.py --strategy smc --csv <file> --to 2025-01-01 --out smc_is

# out-of-sample: HANYA dijalankan setelah puas dengan in-sample,
# dan hasilnya TIDAK boleh dipakai untuk tuning ulang
python3 run_backtest.py --strategy smc --csv <file> --from 2025-01-01 --out smc_oos
```

Aturan main: kalau hasil out-of-sample jelek lalu parameternya diubah sampai
bagus, out-of-sample itu sudah rusak (jadi in-sample terselubung) — hasil
bagusnya tidak bisa dipercaya lagi.

## 3. Forward test

Forward test = menjalankan sistem yang SAMA (parameter dibekukan) pada data
yang belum ada saat sistem dibuat:

1. Tiap akhir bulan, export ulang CSV M15 dari MT5 (data bulan berjalan ikut).
2. Jalankan: `python3 run_backtest.py --strategy smc --csv <file_baru> --from <tanggal_beku> --out fwd_YYYYMM`
   (`tanggal_beku` = tanggal terakhir data yang pernah dipakai menyetel sistem.)
3. Jangan ubah parameter apa pun di antara run forward.
4. Kumpulkan `summary.txt` + `trades.csv` tiap bulan.

## 4. Output & apa yang dikirim untuk dianalisa

Di folder `--out`:
- `summary.txt` — funnel (sweep → choch → entry), WR, net R, expectancy,
  profit factor, max drawdown, rekap per minggu.
- `trades.csv` — semua trade: waktu, arah, pool yang di-sweep, entry/SL/TP,
  RR target, hasil R, alasan exit (tp/sl/be).
- `trade_XXX.png` — chart per trade (zona FVG, level entry/SL/TP) untuk
  verifikasi visual bahwa setup-nya masuk akal.

Kirim `summary.txt` + `trades.csv` (atau paste isinya) untuk dianalisa.

## Definisi setup yang disimulasikan

1. **Pool liquidity**: previous day high/low, Asia range (00:00-07:00 server)
   high/low, swing H1 fractal-3 yang belum di-close-through.
2. **Sweep**: wick M15 menembus pool, close balik ke sisi dalam.
3. **CHoCH**: dalam 16 bar M15, close menembus swing structure M15 terakhir
   yang berlawanan.
4. **Entry**: limit di tepi proximal FVG yang terbentuk pada leg sweep→CHoCH;
   hangus setelah 24 bar; hanya diisi pada jam 07-20 waktu server.
5. **SL**: ekstrem sweep ± 0.25 ATR(H1). **TP**: pool liquidity terdekat di
   arah seberang dengan RR ≥ 2.
6. **Manajemen**: partial 50% di +1R lalu SL ke breakeven; sisa ke TP.
   Satu posisi pada satu waktu; SL dicek lebih dulu jika SL & TP kena di bar
   yang sama (konservatif).

## Strategi lain di repo ini

`--strategy flip` (zone-retest), `--strategy pivot` (fib pivot bounce),
`--strategy trend` (EMA pullback multi-TF) — semuanya sudah diuji pada
XAUUSD 2022-2026 dan TIDAK lolos validasi buta (lihat memori proyek /
percakapan riset). Dibiarkan di sini sebagai pembanding dan bahan belajar.
