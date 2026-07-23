"""Runner backtest JDI BARZ.

Pemakaian:
  python3 run_backtest.py --csv XAUUSD_M15.csv [--spread 0.20] [--out output]
  python3 run_backtest.py --selftest        # uji engine dengan data sintetis

Output di folder --out:
  trades.csv        detail semua trade (zona, entry, SL, TP, hasil R, alasan)
  summary.txt       statistik: expectancy R, win rate, PF, mingguan
  equity_r.png      kurva ekuitas dalam R
  trade_NNN.png     chart verifikasi per trade (zona + entry/SL/TP)
"""
import argparse
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from jdz_engine import Config, load_mt5_csv, run, resample, detect_point


def plot_candles(ax, df: pd.DataFrame):
    w = 0.6 * (df["time"].iloc[1] - df["time"].iloc[0]).total_seconds() / 86400
    for _, r in df.iterrows():
        t = matplotlib.dates.date2num(r["time"])
        up = r["close"] >= r["open"]
        color = "#3a7d44" if up else "#b04a4a"
        ax.plot([t, t], [r["low"], r["high"]], color=color, lw=0.7, zorder=2)
        body_lo, body_hi = min(r["open"], r["close"]), max(r["open"], r["close"])
        ax.add_patch(Rectangle((t - w / 2, body_lo), w, max(body_hi - body_lo, 1e-9),
                               facecolor=color, edgecolor=color, zorder=3))
    ax.xaxis_date()


def plot_trade(tr, m15: pd.DataFrame, path: str, idx: int):
    pad_before = pd.Timedelta(hours=30)
    pad_after = pd.Timedelta(hours=10)
    t_end = tr.exit_time if tr.exit_time is not None else tr.entry_time
    win = m15[(m15["time"] >= tr.zone_t0 - pad_before) &
              (m15["time"] <= t_end + pad_after)]
    if len(win) < 5:
        return
    fig, ax = plt.subplots(figsize=(14, 7))
    plot_candles(ax, win)
    x0 = matplotlib.dates.date2num(max(tr.zone_t0, win["time"].iloc[0]))
    x1 = matplotlib.dates.date2num(win["time"].iloc[-1])
    ax.add_patch(Rectangle((x0, tr.zone_bottom), x1 - x0,
                           tr.zone_top - tr.zone_bottom,
                           facecolor="#b6d0b8", alpha=0.45, zorder=1))
    ax.axhline(tr.entry, color="#1f6fb2", lw=1.2, ls="-")
    ax.axhline(tr.sl, color="#c0392b", lw=1.2, ls="--")
    ax.axhline(tr.tp, color="#27ae60", lw=1.2, ls="--")
    ax.axvline(matplotlib.dates.date2num(tr.entry_time), color="#1f6fb2",
               lw=0.8, ls=":")
    if tr.exit_time is not None:
        ax.axvline(matplotlib.dates.date2num(tr.exit_time), color="#555",
                   lw=0.8, ls=":")
    side = "BUY" if tr.direction > 0 else "SELL"
    ax.set_title(f"#{idx:03d} {side} {tr.zone_kind} zona {tr.zone_t0}  "
                 f"entry {tr.entry:.2f} ({tr.confirm})  SL {tr.sl:.2f}  "
                 f"TP {tr.tp:.2f} (RR {tr.rr_target:.1f})  "
                 f"hasil {tr.result_r:+.2f}R [{tr.exit_reason}]"
                 f"{' partial' if tr.partial_done else ''}")
    ax.grid(alpha=0.2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def report(eng, m15, outdir, max_charts=150):
    os.makedirs(outdir, exist_ok=True)
    trades = eng.trades
    rows = []
    for tr in trades:
        rows.append(dict(
            entry_time=tr.entry_time, direction="BUY" if tr.direction > 0 else "SELL",
            zone=tr.zone_kind, zone_t0=tr.zone_t0,
            zone_top=round(tr.zone_top, 3), zone_bottom=round(tr.zone_bottom, 3),
            confirm=tr.confirm, entry=round(tr.entry, 3), sl=round(tr.sl, 3),
            tp=round(tr.tp, 3), rr_target=round(tr.rr_target, 2),
            exit_time=tr.exit_time, exit_reason=tr.exit_reason,
            partial=tr.partial_done, result_r=round(tr.result_r, 3)))
    tdf = pd.DataFrame(rows)
    tdf.to_csv(os.path.join(outdir, "trades.csv"), index=False)

    lines = []
    lines.append("=== FUNNEL ===")
    for k, v in eng.diag.items():
        lines.append(f"{k:14s}: {v}")
    lines.append("")
    if len(tdf):
        r = tdf["result_r"]
        wins = tdf[r > 0]
        losses = tdf[r < 0]
        pf = wins["result_r"].sum() / abs(losses["result_r"].sum()) \
            if len(losses) and losses["result_r"].sum() != 0 else float("inf")
        lines.append("=== HASIL (dalam R) ===")
        lines.append(f"total trade     : {len(tdf)}")
        lines.append(f"win rate        : {len(wins)}/{len(tdf)} "
                     f"({100*len(wins)/len(tdf):.1f}%)")
        lines.append(f"net R           : {r.sum():+.2f}R")
        lines.append(f"expectancy      : {r.mean():+.3f}R per trade")
        lines.append(f"profit factor   : {pf:.2f}")
        lines.append(f"best / worst    : {r.max():+.2f}R / {r.min():+.2f}R")
        eq = r.cumsum()
        dd = (eq - eq.cummax()).min()
        lines.append(f"max drawdown    : {dd:.2f}R")
        lines.append("")
        lines.append("=== PER MINGGU ===")
        wk = tdf.copy()
        wk["week"] = pd.to_datetime(wk["entry_time"]).dt.strftime("%G-W%V")
        g = wk.groupby("week").agg(
            trades=("result_r", "size"),
            wins=("result_r", lambda x: int((x > 0).sum())),
            netR=("result_r", "sum")).reset_index()
        for _, row in g.iterrows():
            lines.append(f"{row['week']}: {row['trades']} trade, "
                         f"{row['wins']} win, {row['netR']:+.2f}R")

        fig, ax = plt.subplots(figsize=(11, 4.5))
        ax.plot(pd.to_datetime(tdf["entry_time"]), eq.values, lw=1.4)
        ax.set_title("Kurva ekuitas (R kumulatif)")
        ax.grid(alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "equity_r.png"), dpi=100)
        plt.close(fig)
    else:
        lines.append("TIDAK ADA TRADE - lihat funnel di atas untuk tahap yang menyumbat")

    txt = "\n".join(lines)
    with open(os.path.join(outdir, "summary.txt"), "w") as f:
        f.write(txt + "\n")
    print(txt)

    for i, tr in enumerate(trades[:max_charts], 1):
        plot_trade(tr, m15, os.path.join(outdir, f"trade_{i:03d}.png"), i)
    print(f"\nchart per-trade: {min(len(trades), max_charts)} file di {outdir}/")


def make_synthetic(n_days=90, seed=7) -> pd.DataFrame:
    """Data M15 sintetis: random walk + regime trend supaya ada displacement."""
    rng = np.random.default_rng(seed)
    n = n_days * 96
    drift = np.zeros(n)
    i = 0
    while i < n:
        seg = rng.integers(96, 96 * 5)
        drift[i:i + seg] = rng.choice([-0.35, 0.0, 0.35], p=[0.35, 0.3, 0.35])
        i += seg
    vol = rng.uniform(0.8, 2.4, n)
    steps = rng.normal(0, 1, n) * vol + drift
    close = 2400 + np.cumsum(steps)
    open_ = np.roll(close, 1); open_[0] = close[0]
    spread_hl = np.abs(rng.normal(0, 1.2, n)) + 0.3
    high = np.maximum(open_, close) + spread_hl
    low = np.minimum(open_, close) - spread_hl
    times = pd.date_range("2025-01-06", periods=n, freq="15min")
    mask = times.dayofweek < 5
    return pd.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close})[mask].reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="file CSV M15 export MT5")
    ap.add_argument("--out", default="output")
    ap.add_argument("--spread", type=float, default=-1.0,
                    help="spread dlm satuan harga; default: median kolom SPREAD CSV")
    ap.add_argument("--strategy", default="flip",
                    choices=["flip", "pivot", "trend", "smc"])
    ap.add_argument("--from", dest="date_from", default=None,
                    help="filter data mulai tanggal (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="filter data sampai tanggal (YYYY-MM-DD)")
    ap.add_argument("--min-rr", type=float, default=None,
                    help="default: 5.0 utk flip, 1.5 utk pivot")
    ap.add_argument("--no-sweep", action="store_true",
                    help="pivot: matikan syarat sweep liquidity")
    ap.add_argument("--smc-loose", action="store_true",
                    help="smc: jendela lebar + entry cadangan OTE + tanpa "
                         "sweep AsiaHigh + partial 1.5R (varian frekuensi)")
    ap.add_argument("--confirm", default="either",
                    choices=["engulf", "reject", "either", "close"])
    ap.add_argument("--max-charts", type=int, default=150)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        m15 = make_synthetic()
        print(f"[selftest] data sintetis: {len(m15)} bar M15 "
              f"({m15['time'].iloc[0]} .. {m15['time'].iloc[-1]})")
    elif args.csv:
        m15 = load_mt5_csv(args.csv)
        print(f"data: {len(m15)} bar M15 "
              f"({m15['time'].iloc[0]} .. {m15['time'].iloc[-1]})")
    else:
        ap.error("wajib --csv <file> atau --selftest")

    spread = max(args.spread, 0.0)
    if args.spread < 0:
        if "spread_pts" in m15.columns:
            point = detect_point(m15)
            spread = float(m15["spread_pts"].median()) * point
            print(f"spread auto: median {m15['spread_pts'].median():.0f} pts "
                  f"x point {point} = {spread:.3f}")
        else:
            spread = 0.0

    if args.date_from:
        m15 = m15[m15["time"] >= pd.Timestamp(args.date_from)].reset_index(drop=True)
    if args.date_to:
        m15 = m15[m15["time"] < pd.Timestamp(args.date_to)].reset_index(drop=True)

    if args.strategy == "smc":
        from smc_engine import SmcConfig, run_smc
        if args.smc_loose:
            scfg = SmcConfig(spread=spread,
                             min_rr=args.min_rr if args.min_rr else 2.0,
                             choch_window=28, entry_window=36,
                             fallback_ote=True, exclude_pools=("ASIAH",),
                             partial_at_rr=1.5)
        else:
            scfg = SmcConfig(spread=spread,
                             min_rr=args.min_rr if args.min_rr else 2.0)
        eng = run_smc(m15, scfg)
    elif args.strategy == "trend":
        from trend_engine import TrendConfig, run_trend
        tcfg = TrendConfig(spread=spread)
        eng = run_trend(m15, tcfg)
    elif args.strategy == "pivot":
        from pivot_engine import PivotConfig, run_pivot
        pcfg = PivotConfig(spread=spread,
                           min_rr=args.min_rr if args.min_rr else 1.5,
                           require_sweep=not args.no_sweep,
                           confirm_mode=args.confirm)
        eng = run_pivot(m15, pcfg)
    else:
        cfg = Config(spread=spread,
                     min_rr_liq=args.min_rr if args.min_rr else 5.0,
                     confirm_mode=args.confirm)
        eng = run(m15, cfg)
    report(eng, m15, args.out, args.max_charts)


if __name__ == "__main__":
    main()
