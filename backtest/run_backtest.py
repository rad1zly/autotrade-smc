"""Runner backtest PAF-QIE (SMC + otak LLM).

Pemakaian:
  cd backtest
  python3 run_backtest.py --csv <file.csv> --mode mock   # cepat, tanpa panggil LLM
  python3 run_backtest.py --csv <file.csv> --mode llm    # panggil LLM sungguhan (biaya token!)
  python3 run_backtest.py --selftest --mode mock         # uji pipa dgn data sintetis

Output di folder --out: trades.csv, summary.txt, equity_r.png, trade_NNN.png
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

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import load_mt5_csv, detect_point, detect_timeframe_label
from smc_engine import SmcConfig, run_smc


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
    win = m15[(m15["time"] >= tr.zone_t0 - pad_before) & (m15["time"] <= t_end + pad_after)]
    if len(win) < 5:
        return
    fig, ax = plt.subplots(figsize=(14, 7))
    plot_candles(ax, win)
    ax.axhline(tr.entry, color="#1f6fb2", lw=1.2, ls="-")
    ax.axhline(tr.sl, color="#c0392b", lw=1.2, ls="--")
    ax.axhline(tr.tp, color="#27ae60", lw=1.2, ls="--")
    ax.axvline(matplotlib.dates.date2num(tr.entry_time), color="#1f6fb2", lw=0.8, ls=":")
    if tr.exit_time is not None:
        ax.axvline(matplotlib.dates.date2num(tr.exit_time), color="#555", lw=0.8, ls=":")
    side = "BUY" if tr.direction > 0 else "SELL"
    ax.set_title(f"#{idx:03d} {side} {tr.zone_kind}  entry {tr.entry:.2f} (LLM conf {tr.confidence}%)  "
                 f"SL {tr.sl:.2f}  TP {tr.tp:.2f} (RR {tr.rr_target:.1f})  "
                 f"hasil {tr.result_r:+.2f}R [{tr.exit_reason}]"
                 f"{' partial' if tr.partial_done else ''}\n{tr.reason}", fontsize=9)
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
            zone=tr.zone_kind, confidence=tr.confidence,
            entry=round(tr.entry, 3), sl=round(tr.sl, 3), tp=round(tr.tp, 3),
            rr_target=round(tr.rr_target, 2), exit_time=tr.exit_time,
            exit_reason=tr.exit_reason, partial=tr.partial_done,
            result_r=round(tr.result_r, 3), reason=tr.reason))
    tdf = pd.DataFrame(rows)
    tdf.to_csv(os.path.join(outdir, "trades.csv"), index=False, encoding="utf-8")

    # setiap setup MSS-confirmed (traded ATAU skip) dgn alasan ASLI dari LLM —
    # ini yang dipakai buat diagnosa kenapa brain menolak, bukan cuma tally count
    sdf = pd.DataFrame(eng.setups)
    sdf.to_csv(os.path.join(outdir, "setups.csv"), index=False, encoding="utf-8")

    lines = ["=== FUNNEL ==="]
    for k, v in eng.diag.items():
        lines.append(f"{k:16s}: {v}")
    if eng.reject_reasons:
        lines.append("")
        lines.append("=== ALASAN BRAIN MENOLAK ===")
        for k, v in eng.reject_reasons.most_common():
            lines.append(f"{k:30s}: {v}")
    lines.append("")

    if len(tdf):
        r = tdf["result_r"]
        wins = tdf[r > 0]
        losses = tdf[r < 0]
        pf = wins["result_r"].sum() / abs(losses["result_r"].sum()) \
            if len(losses) and losses["result_r"].sum() != 0 else float("inf")
        lines.append("=== HASIL (dalam R) ===")
        lines.append(f"total trade     : {len(tdf)}")
        lines.append(f"win rate        : {len(wins)}/{len(tdf)} ({100*len(wins)/len(tdf):.1f}%)")
        lines.append(f"net R           : {r.sum():+.2f}R")
        lines.append(f"expectancy      : {r.mean():+.3f}R per trade")
        lines.append(f"profit factor   : {pf:.2f}")
        lines.append(f"best / worst    : {r.max():+.2f}R / {r.min():+.2f}R")
        lines.append(f"confidence avg  : {tdf['confidence'].mean():.0f}%")
        eq = r.cumsum()
        dd = (eq - eq.cummax()).min()
        lines.append(f"max drawdown    : {dd:.2f}R")
        lines.append("")
        lines.append("=== PER MINGGU ===")
        wk = tdf.copy()
        wk["week"] = pd.to_datetime(wk["entry_time"]).dt.strftime("%G-W%V")
        g = wk.groupby("week").agg(trades=("result_r", "size"),
                                   wins=("result_r", lambda x: int((x > 0).sum())),
                                   netR=("result_r", "sum")).reset_index()
        for _, row in g.iterrows():
            lines.append(f"{row['week']}: {row['trades']} trade, {row['wins']} win, {row['netR']:+.2f}R")

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
    with open(os.path.join(outdir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt.encode("ascii", errors="replace").decode("ascii"))

    for i, tr in enumerate(trades[:max_charts], 1):
        plot_trade(tr, m15, os.path.join(outdir, f"trade_{i:03d}.png"), i)
    print(f"\nchart per-trade: {min(len(trades), max_charts)} file di {outdir}/")


def make_synthetic(n_days=90, seed=7) -> pd.DataFrame:
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
    ap.add_argument("--mode", default="mock", choices=["mock", "llm"],
                    help="mock = rule-based cepat (uji pipa); llm = panggil LLM sungguhan (biaya token)")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--min-rr", type=float, default=2.0)
    ap.add_argument("--max-charts", type=int, default=150)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        m15 = make_synthetic()
        tf_label = "M15"
        print(f"[selftest] data sintetis: {len(m15)} bar {tf_label} ({m15['time'].iloc[0]} .. {m15['time'].iloc[-1]})")
    elif args.csv:
        m15 = load_mt5_csv(args.csv)
        tf_label = detect_timeframe_label(m15)
        print(f"data: {len(m15)} bar {tf_label} ({m15['time'].iloc[0]} .. {m15['time'].iloc[-1]})")
    else:
        ap.error("wajib --csv <file> atau --selftest")

    spread = max(args.spread, 0.0)
    digits = 2
    if args.spread < 0:
        if "spread_pts" in m15.columns:
            point = detect_point(m15)
            spread = float(m15["spread_pts"].median()) * point
            digits = max(0, int(round(-np.log10(point))))
            print(f"spread auto: median {m15['spread_pts'].median():.0f} pts x point {point} = {spread:.5f}")

    if args.date_from:
        m15 = m15[m15["time"] >= pd.Timestamp(args.date_from)].reset_index(drop=True)
    if args.date_to:
        m15 = m15[m15["time"] < pd.Timestamp(args.date_to)].reset_index(drop=True)

    if args.mode == "llm":
        from brain.decision import decide
        from brain.config import load_config
        cfg_brain = load_config()
        print(f"mode LLM: provider={cfg_brain.provider} — setiap setup confirmed akan memanggil API "
              f"(biaya token nyata). Disarankan uji di rentang data pendek dulu.")
        decide_fn = lambda ctx: decide(ctx, cfg_brain)
    else:
        from brain.decision import decide_mock
        decide_fn = decide_mock
        print("mode MOCK: SL/TP rule-based (bukan LLM sungguhan) — hanya utk uji pipa/frekuensi setup.")

    scfg = SmcConfig(spread=spread, min_rr=args.min_rr, digits=digits, tf_label=tf_label)
    eng = run_smc(m15, scfg, decide_fn)
    report(eng, m15, args.out, args.max_charts)


if __name__ == "__main__":
    main()
