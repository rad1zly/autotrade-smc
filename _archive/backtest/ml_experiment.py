"""Eksperimen ML meta-labeling di sinyal pullback XAUUSD.

Protokol anti-overfit:
  TRAIN   : 2022-04 .. 2023-12  (model belajar)
  VALIDASI: 2024-01 .. 2024-12  (pilih model & threshold)
  TEST    : 2025-01 .. 2026-07  (disentuh SEKALI di akhir; catatan: window
            ini sudah pernah dipakai 1x utk validasi filter manual, jadi
            statusnya "semi-burned" - diungkap jujur di laporan)

Desain:
  - Sinyal dasar: pullback ke EMA20 H1 saat ada bias EMA H4, sesi 07-20,
    TANPA filter konfirmasi (biar sampel banyak); manajemen trade sama
    dgn engine trend (partial 50% @1R, BE, TP 2.5R).
  - Label: 1 jika result_r > 0 (trade menghasilkan uang), 0 jika tidak.
  - Model: HistGradientBoosting (sklearn), fitur konteks multi-TF.
  - Metrik yang menentukan: net R & PF di subset p>threshold, BUKAN akurasi.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from jdz_engine import load_mt5_csv, detect_point, resample
from trend_engine import TrendConfig, run_trend, _ema_map, _atr_map

CSV = "/Users/dalinfo-air-01/Downloads/XAUUSD+_M15_202204250715_202607172345.csv"
TRAIN_END = "2024-01-01"
VAL_END = "2025-01-01"


def build_features(m15: pd.DataFrame) -> pd.DataFrame:
    f = m15[["time"]].copy()
    o = m15["open"].to_numpy(); h = m15["high"].to_numpy()
    l = m15["low"].to_numpy(); c = m15["close"].to_numpy()
    atr = _atr_map(m15, "1h", 14)
    f["atr"] = atr
    f["atr_pct"] = pd.Series(atr).rolling(3000, min_periods=500).rank(pct=True)

    ef = _ema_map(m15, "4h", 20); es = _ema_map(m15, "4h", 50)
    ed = _ema_map(m15, "1D", 20); ep = _ema_map(m15, "1h", 20)
    f["sep_atr"] = (ef - es) / atr
    f["dist_day_atr"] = (c - ed) / atr
    f["dist_pull_atr"] = (c - ep) / atr

    cs = pd.Series(c)
    for n in (1, 4, 16, 64):
        f[f"ret_{n}"] = (cs - cs.shift(n)).to_numpy() / atr
    rng = np.maximum(h - l, 1e-9)
    f["body_ratio"] = np.abs(c - o) / rng
    f["upwick"] = (h - np.maximum(o, c)) / rng
    f["dnwick"] = (np.minimum(o, c) - l) / rng
    f["bar_dir"] = np.sign(c - o)

    # posisi harga dlm range 24 jam terakhir (96 bar M15)
    hh = pd.Series(h).rolling(96).max().to_numpy()
    ll = pd.Series(l).rolling(96).min().to_numpy()
    f["day_range_pos"] = (c - ll) / np.maximum(hh - ll, 1e-9)
    f["day_range_atr"] = (hh - ll) / atr

    f["hour"] = m15["time"].dt.hour
    f["dow"] = m15["time"].dt.dayofweek
    return f


def gen_events(m15: pd.DataFrame, spread: float):
    """Sinyal dasar longgar + hasil trade-nya (label)."""
    cfg = TrendConfig(spread=spread, partial_at_rr=1.0, partial_pct=50,
                      tp_rr=2.5, cooldown_bars=4)
    # matikan trigger candle: terima semua touch dgn close di sisi trend
    # (pakai confirm 'close' bawaan trigger engine = candle searah; ini
    # trigger paling longgar yang masih eksekusi realistis)
    eng = run_trend(m15, cfg)
    rows = []
    for tr in eng.trades:
        rows.append(dict(time=tr.entry_time, direction=tr.direction,
                         risk_atr=np.nan, r=tr.result_r,
                         win=1 if tr.result_r > 0 else 0, risk=tr.risk))
    ev = pd.DataFrame(rows)
    return ev


def pick_threshold(y, p, rs, grid):
    """Threshold dgn netR terbaik di validasi, syarat minimal 60 trade."""
    best, best_net = grid[0], -1e9
    for th in grid:
        m = p >= th
        if m.sum() < 60:
            continue
        net = rs[m].sum()
        if net > best_net:
            best_net, best = net, th
    return best


def subset_stats(rs):
    rs = pd.Series(rs)
    if not len(rs):
        return "kosong"
    gp = rs[rs > 0].sum(); gl = abs(rs[rs < 0].sum())
    pf = gp / gl if gl > 0 else float("inf")
    eq = rs.cumsum(); dd = (eq - eq.cummax()).min()
    wr = 100 * (rs > 0).mean()
    return (f"n={len(rs)}, WR {wr:.1f}%, netR {rs.sum():+.1f}, "
            f"exp {rs.mean():+.3f}R, PF {pf:.2f}, maxDD {dd:.1f}R")


def main():
    m15 = load_mt5_csv(CSV)
    spread = float(m15["spread_pts"].median()) * detect_point(m15)
    ev = gen_events(m15, spread)
    feats = build_features(m15)
    ev = ev.merge(feats, on="time", how="left")
    ev["risk_atr"] = ev["risk"] / ev["atr"]
    ev = ev.dropna().reset_index(drop=True)

    fcols = [c for c in ev.columns
             if c not in ("time", "r", "win", "risk")]
    tr_m = ev["time"] < pd.Timestamp(TRAIN_END)
    va_m = (ev["time"] >= pd.Timestamp(TRAIN_END)) & \
           (ev["time"] < pd.Timestamp(VAL_END))
    te_m = ev["time"] >= pd.Timestamp(VAL_END)
    print(f"total event: {len(ev)} | train {tr_m.sum()} | "
          f"val {va_m.sum()} | test {te_m.sum()}")
    print(f"baseline (semua sinyal):")
    print(f"  train: {subset_stats(ev.loc[tr_m, 'r'])}")
    print(f"  val  : {subset_stats(ev.loc[va_m, 'r'])}")
    print(f"  test : {subset_stats(ev.loc[te_m, 'r'])}\n")

    X, y, rs = ev[fcols], ev["win"], ev["r"]
    model = HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=300,
        l2_regularization=1.0, min_samples_leaf=40,
        early_stopping=True, validation_fraction=0.15, random_state=42)
    model.fit(X[tr_m], y[tr_m])

    for name, mask in (("train", tr_m), ("val", va_m)):
        p = model.predict_proba(X[mask])[:, 1]
        print(f"AUC {name}: {roc_auc_score(y[mask], p):.3f}")

    p_va = model.predict_proba(X[va_m])[:, 1]
    grid = np.arange(0.40, 0.71, 0.02)
    th = pick_threshold(y[va_m].to_numpy(), p_va, rs[va_m].to_numpy(), grid)
    print(f"\nthreshold dipilih di VALIDASI: p >= {th:.2f}")
    print(f"  val  (p>=th): {subset_stats(rs[va_m][p_va >= th])}")
    print(f"  val  (p< th): {subset_stats(rs[va_m][p_va < th])}")

    # ---- SATU KALI: test 2025-2026 ----
    p_te = model.predict_proba(X[te_m])[:, 1]
    print(f"\nAUC test: {roc_auc_score(y[te_m], p_te):.3f}")
    print(f"  test (p>=th): {subset_stats(rs[te_m][p_te >= th])}")
    print(f"  test (p< th): {subset_stats(rs[te_m][p_te < th])}")

    # kepentingan fitur (permutation sederhana via built-in)
    try:
        from sklearn.inspection import permutation_importance
        imp = permutation_importance(model, X[va_m], y[va_m], n_repeats=5,
                                     random_state=0)
        order = np.argsort(-imp.importances_mean)[:10]
        print("\ntop fitur (permutation importance @val):")
        for i in order:
            print(f"  {fcols[i]:16s} {imp.importances_mean[i]:+.4f}")
    except Exception as e:
        print("importance skip:", e)


if __name__ == "__main__":
    main()
