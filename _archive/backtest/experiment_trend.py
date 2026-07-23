"""Eksperimen strategi trend - HANYA di data in-sample (2022-04 .. 2024-12).

Data 2025-2026 sengaja TIDAK disentuh di sini; dipakai sekali saja untuk
validasi buta setelah konfigurasi final dipilih.
"""
import sys
import numpy as np
import pandas as pd

from jdz_engine import load_mt5_csv, detect_point
from trend_engine import TrendConfig, run_trend

CSV = "/Users/dalinfo-air-01/Downloads/XAUUSD+_M15_202204250715_202607172345.csv"
IS_END = "2025-01-01"


def metrics(eng, label):
    t = pd.DataFrame([{
        "entry_time": tr.entry_time, "r": tr.result_r,
        "reason": tr.exit_reason} for tr in eng.trades])
    if not len(t):
        return {"label": label, "trades": 0}
    r = t["r"]
    wins = (r > 0).sum()
    gp = r[r > 0].sum()
    gl = abs(r[r < 0].sum())
    eq = r.cumsum()
    dd = (eq - eq.cummax()).min()
    weeks = pd.to_datetime(t["entry_time"]).dt.strftime("%G-W%V")
    per_week = t.groupby(weeks).size()
    nweeks = len(pd.period_range(t['entry_time'].min(), t['entry_time'].max(),
                                 freq='W'))
    return {
        "label": label,
        "trades": len(t),
        "per_wk": round(len(t) / max(nweeks, 1), 2),
        "wk>=2": f"{(per_week >= 2).sum()}/{nweeks}",
        "wr%": round(100 * wins / len(t), 1),
        "netR": round(r.sum(), 1),
        "expR": round(r.mean(), 3),
        "pf": round(gp / gl, 2) if gl > 0 else float("inf"),
        "maxDD_R": round(dd, 1),
    }


def main():
    m15 = load_mt5_csv(CSV)
    point = detect_point(m15)
    spread = float(m15["spread_pts"].median()) * point
    m15 = m15[m15["time"] < pd.Timestamp(IS_END)].reset_index(drop=True)
    print(f"in-sample: {len(m15)} bar M15 "
          f"({m15['time'].iloc[0]} .. {m15['time'].iloc[-1]}), "
          f"spread {spread:.3f}\n")

    base = dict(spread=spread, partial_at_rr=1.0, partial_pct=50,
                tp_rr=2.5, cooldown_bars=16)
    variants = {
        "combo-base": TrendConfig(**base),
        "c+sep0.4": TrendConfig(**base, min_sep_atr=0.4),
        "c+sep0.8": TrendConfig(**base, min_sep_atr=0.8),
        "c+strong": TrendConfig(**base, strong_trigger=True),
        "c+sep0.4+strong": TrendConfig(**base, min_sep_atr=0.4,
                                       strong_trigger=True),
        "c+sep0.8+strong": TrendConfig(**base, min_sep_atr=0.8,
                                       strong_trigger=True),
        "c+sep0.8+str+e34": TrendConfig(**base, min_sep_atr=0.8,
                                        strong_trigger=True, ema_pull_h1=34),
        "c+sep1.2+strong": TrendConfig(**base, min_sep_atr=1.2,
                                       strong_trigger=True),
    }

    rows = []
    for label, cfg in variants.items():
        eng = run_trend(m15, cfg)
        rows.append(metrics(eng, label))
        d = eng.diag
        print(f"{label:20s} touch={d['touch']:5d} trig_fail={d['trigger_fail']:5d} "
              f"risk_block={d['risk_block']:4d} sess={d['session_block']:5d} "
              f"entries={d['entries']:4d}")
    print()
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
