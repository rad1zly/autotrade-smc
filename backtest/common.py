"""Util bersama: load CSV MT5, resample, ATR, struct Trade.

Diambil dari engine riset sebelumnya (_archive/backtest/jdz_engine.py +
trend_engine.py) — hanya bagian generik, tanpa logic strategi JDI BARZ.
"""
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class Trade:
    zone_kind: str
    zone_t0: pd.Timestamp
    zone_top: float
    zone_bottom: float
    direction: int              # +1 buy, -1 sell
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    risk: float
    rr_target: float
    confirm: str                # "llm" | "mock"
    confidence: int = 0
    reason: str = ""
    exit_time: Optional[pd.Timestamp] = None
    exit_reason: str = ""
    result_r: float = 0.0
    partial_done: bool = False


def load_mt5_csv(path: str) -> pd.DataFrame:
    """Baca CSV export MT5 (tab/koma, header <DATE> dst)."""
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip().strip("<>").upper() for c in df.columns]
    if "TIME" in df.columns and "DATE" in df.columns:
        ts = pd.to_datetime(df["DATE"].astype(str) + " " + df["TIME"].astype(str),
                            format="%Y.%m.%d %H:%M:%S")
    elif "DATE" in df.columns:
        ts = pd.to_datetime(df["DATE"].astype(str), format="%Y.%m.%d")
    else:
        raise ValueError("kolom DATE tidak ditemukan: %s" % df.columns.tolist())
    out = pd.DataFrame({
        "time": ts,
        "open": df["OPEN"].astype(float),
        "high": df["HIGH"].astype(float),
        "low": df["LOW"].astype(float),
        "close": df["CLOSE"].astype(float),
    })
    if "SPREAD" in df.columns:
        out["spread_pts"] = df["SPREAD"].astype(float)
    return out.sort_values("time").reset_index(drop=True)


def detect_point(df: pd.DataFrame) -> float:
    """Perkiraan point size dari jumlah desimal harga."""
    sample = df["close"].head(500).astype(str)
    dec = sample.str.split(".").str[-1].str.len().max()
    return 10.0 ** (-int(dec))


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = df.set_index("time").resample(rule, label="left", closed="left")
    out = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
    }).dropna().reset_index()
    return out


def atr_map(m15: pd.DataFrame, rule: str, period: int) -> np.ndarray:
    """ATR dihitung di TF `rule`, dipetakan balik ke tiap bar M15 (bar TF
    terakhir yang sudah close saat itu — tanpa lookahead)."""
    htf = resample(m15, rule)
    h = htf["high"].to_numpy(); l = htf["low"].to_numpy(); c = htf["close"].to_numpy()
    tr = np.empty(len(htf))
    tr[0] = h[0] - l[0]
    for i in range(1, len(htf)):
        tr[i] = max(h[i], c[i - 1]) - min(l[i], c[i - 1])
    atr = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
    htf_times = htf["time"].to_numpy()
    idx = np.searchsorted(htf_times, m15["time"].to_numpy(), side="right") - 1
    idx = np.clip(idx, 0, len(atr) - 1)
    out = atr[idx]
    out[idx < 0] = np.nan
    return out
