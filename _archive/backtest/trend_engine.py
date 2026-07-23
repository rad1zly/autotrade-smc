"""Strategi multi-timeframe: trend H4/Daily + pullback EMA H1 + trigger M15.

- Bias  : H4 EMA(fast) vs EMA(slow); Daily EMA(slow) sebagai saringan arah besar.
- Setup : harga pullback menyentuh EMA(pull) H1 (band = tol_atr x ATR H1),
          hanya pada jam sesi (default 07-20 waktu server = London+NY).
- Trigger: candle M15 reclaim searah trend (close di sisi trend dari EMA
          dan candle searah), dengan syarat risiko masuk akal (SL dari
          swing pullback, dibatasi min/max terhadap ATR).
- Exit  : partial partial_pct% di partial_at_rr, SL ke BE, sisa ke tp_rr.
Semua indikator dihitung dari bar HTF yang SUDAH close (shift 1, tanpa
lookahead), dipetakan ke M15 via merge_asof.
"""
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd

from jdz_engine import Trade, resample


@dataclass
class TrendConfig:
    ema_fast_h4: int = 20
    ema_slow_h4: int = 50
    ema_daily: int = 20          # saringan arah besar; 0 = nonaktif
    ema_pull_h1: int = 20
    tol_atr: float = 0.25        # band sentuh EMA pull (x ATR H1)
    sl_lookback: int = 8         # swing utk SL: ekstrem N bar M15 terakhir
    sl_buf_atr: float = 0.20
    min_risk_atr: float = 0.15   # SL terlalu sempit = noise, skip
    max_risk_atr: float = 1.60   # SL terlalu lebar, skip
    tp_rr: float = 2.0
    partial_at_rr: float = 0.8
    partial_pct: float = 60.0
    use_partial: bool = True
    spread: float = 0.0
    hour_start: int = 7
    hour_end: int = 20
    cooldown_bars: int = 8       # jeda M15 setelah trade ditutup
    atr_period: int = 14
    min_sep_atr: float = 0.0     # jarak minimal EMA fast-slow H4 (x ATR H1);
                                 # 0 = nonaktif. Menyaring kondisi H4 choppy.
    strong_trigger: bool = False # trigger harus close menembus high/low bar
                                 # sebelumnya (momentum resume), bukan cuma
                                 # candle searah
    vol_pct_min: float = 0.0     # gate adaptif: skip saat percentile ATR H1
                                 # (rolling ~1 bln) di bawah ambang ini; 0=off
    long_only: bool = False      # gold: short structurally berat


def _ema_map(m15: pd.DataFrame, rule: str, span: int, col="close") -> np.ndarray:
    htf = resample(m15, rule).copy()
    htf["ema"] = htf[col].ewm(span=span, adjust=False).mean().shift(1)
    merged = pd.merge_asof(m15[["time"]], htf[["time", "ema"]],
                           on="time", direction="backward")
    return merged["ema"].to_numpy()


def _atr_map(m15: pd.DataFrame, rule: str, period: int) -> np.ndarray:
    htf = resample(m15, rule).copy()
    prev_c = htf["close"].shift(1)
    tr = pd.concat([htf["high"] - htf["low"],
                    (htf["high"] - prev_c).abs(),
                    (htf["low"] - prev_c).abs()], axis=1).max(axis=1)
    htf["atr"] = tr.rolling(period).mean().shift(1)
    merged = pd.merge_asof(m15[["time"]], htf[["time", "atr"]],
                           on="time", direction="backward")
    return merged["atr"].to_numpy()


class TrendEngine:
    def __init__(self, cfg: TrendConfig):
        self.cfg = cfg
        self.trades: list = []
        self.open_trade: Optional[Trade] = None
        self.diag = dict(bias_long=0, bias_short=0, touch=0, trigger_fail=0,
                         risk_block=0, session_block=0, entries=0)

    def _manage_open(self, times, H, L, m):
        cfg = self.cfg
        tr = self.open_trade
        h, l, t = H[m], L[m], times[m]
        d = tr.direction
        sp = cfg.spread
        cur_sl = tr.entry if tr.partial_done and cfg.use_partial else tr.sl

        if d > 0:
            sl_hit = l <= cur_sl
            tp_hit = h >= tr.tp
            partial_hit = (not tr.partial_done) and cfg.use_partial and \
                h >= tr.entry + cfg.partial_at_rr * tr.risk
        else:
            sl_hit = h + sp >= cur_sl
            tp_hit = l + sp <= tr.tp
            partial_hit = (not tr.partial_done) and cfg.use_partial and \
                l + sp <= tr.entry - cfg.partial_at_rr * tr.risk

        if sl_hit and not tr.partial_done:
            tr.exit_time = t; tr.exit_reason = "sl"; tr.result_r = -1.0
            self._close(tr); return
        if partial_hit:
            tr.partial_done = True
        if tr.partial_done and cfg.use_partial:
            frac = cfg.partial_pct / 100.0
            banked = frac * cfg.partial_at_rr
            be_hit = (l <= tr.entry) if d > 0 else (h + sp >= tr.entry)
            if tp_hit:
                tr.exit_time = t; tr.exit_reason = "tp"
                tr.result_r = banked + (1 - frac) * cfg.tp_rr
                self._close(tr); return
            if be_hit and not partial_hit:
                tr.exit_time = t; tr.exit_reason = "be"; tr.result_r = banked
                self._close(tr); return
        else:
            if sl_hit:
                tr.exit_time = t; tr.exit_reason = "sl"; tr.result_r = -1.0
                self._close(tr); return
            if tp_hit:
                tr.exit_time = t; tr.exit_reason = "tp"
                tr.result_r = cfg.tp_rr
                self._close(tr); return

    def _close(self, tr):
        self.trades.append(tr)
        self.open_trade = None


def run_trend(m15: pd.DataFrame, cfg: TrendConfig) -> TrendEngine:
    eng = TrendEngine(cfg)
    ema_fast = _ema_map(m15, "4h", cfg.ema_fast_h4)
    ema_slow = _ema_map(m15, "4h", cfg.ema_slow_h4)
    ema_day = _ema_map(m15, "1D", cfg.ema_daily) if cfg.ema_daily > 0 else None
    ema_pull = _ema_map(m15, "1h", cfg.ema_pull_h1)
    atr = _atr_map(m15, "1h", cfg.atr_period)
    atr_pct = None
    if cfg.vol_pct_min > 0:
        atr_pct = pd.Series(atr).rolling(3000, min_periods=500) \
            .rank(pct=True).to_numpy()

    times = m15["time"].tolist()
    hours = m15["time"].dt.hour.to_numpy()
    O = m15["open"].to_numpy(); H = m15["high"].to_numpy()
    L = m15["low"].to_numpy(); C = m15["close"].to_numpy()

    look = cfg.sl_lookback
    last_close_m = -10_000

    for m in range(len(times)):
        if eng.open_trade is not None:
            eng._manage_open(times, H, L, m)
            if eng.open_trade is None:
                last_close_m = m
            continue
        if m < look + 1:
            continue
        a = atr[m]
        ef, es, ep = ema_fast[m], ema_slow[m], ema_pull[m]
        if not (np.isfinite(a) and np.isfinite(ef) and np.isfinite(es)
                and np.isfinite(ep)) or a <= 0:
            continue
        if m - last_close_m < cfg.cooldown_bars:
            continue

        bias = 0
        if ef > es:
            bias = 1
        elif ef < es:
            bias = -1
        if ema_day is not None and np.isfinite(ema_day[m]):
            if bias > 0 and C[m] < ema_day[m]:
                bias = 0
            if bias < 0 and C[m] > ema_day[m]:
                bias = 0
        if bias == 0:
            continue
        if cfg.long_only and bias < 0:
            continue
        if cfg.min_sep_atr > 0 and abs(ef - es) < cfg.min_sep_atr * a:
            continue                     # trend H4 kurang tegas, skip
        if atr_pct is not None and \
           (not np.isfinite(atr_pct[m]) or atr_pct[m] < cfg.vol_pct_min):
            continue                     # pasar sedang mati, skip
        if bias > 0:
            eng.diag["bias_long"] += 1
        else:
            eng.diag["bias_short"] += 1

        band = cfg.tol_atr * a
        if bias > 0:
            touch = L[m] <= ep + band
            trigger = C[m] > O[m] and C[m] > ep
            if cfg.strong_trigger:
                trigger = trigger and C[m] > H[m - 1]
        else:
            touch = H[m] >= ep - band
            trigger = C[m] < O[m] and C[m] < ep
            if cfg.strong_trigger:
                trigger = trigger and C[m] < L[m - 1]
        if not touch:
            continue
        eng.diag["touch"] += 1

        if not (cfg.hour_start <= hours[m] < cfg.hour_end):
            eng.diag["session_block"] += 1
            continue
        if not trigger:
            eng.diag["trigger_fail"] += 1
            continue

        close = C[m]
        entry = close + cfg.spread if bias > 0 else close
        if bias > 0:
            sl = L[m - look:m + 1].min() - cfg.sl_buf_atr * a
        else:
            sl = H[m - look:m + 1].max() + cfg.sl_buf_atr * a
        risk = abs(entry - sl)
        if risk < cfg.min_risk_atr * a or risk > cfg.max_risk_atr * a:
            eng.diag["risk_block"] += 1
            continue
        tp = entry + cfg.tp_rr * risk if bias > 0 else entry - cfg.tp_rr * risk

        eng.open_trade = Trade(
            zone_kind="TREND", zone_t0=times[m], zone_top=ep + band,
            zone_bottom=ep - band, direction=bias, entry_time=times[m],
            entry=entry, sl=sl, tp=tp, risk=risk, rr_target=cfg.tp_rr,
            confirm="pullback")
        eng.diag["entries"] += 1
    return eng
