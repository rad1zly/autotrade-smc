"""Strategi Pivot Bounce harian (fib pivot) + konfirmasi SMC di M15.

Rumus pivot (dari H/L/C hari sebelumnya):
  PP = (H + L + C) / 3
  R1/R2/R3 = PP + (H-L) x 0.382 / 0.618 / 1.000
  S1/S2/S3 = PP - (H-L) x 0.382 / 0.618 / 1.000

Aturan entry (BUY di S-level, SELL di R-level, mirror):
  1. Bar M15 menyentuh level (band = tol_atr x ATR H1).
  2. (Opsional, default ON) wick bar itu SWEEP: menembus low terendah
     `sweep_lookback` bar sebelumnya (ambil liquidity di bawah level).
  3. Close bar RECLAIM: balik ke sisi yang benar dari level.
  4. Reaction candle (rejection / engulfing) sesuai confirm_mode.
  SL  = wick sweep +- buffer ATR.
  TP  = level pivot berikutnya searah profit dengan RR >= min_rr.
  Partial 50% di partial_at_rr, lalu SL ke breakeven.
  Satu level hanya sekali per hari; satu posisi pada satu waktu.
"""
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd

from jdz_engine import Trade, resample


@dataclass
class PivotConfig:
    tol_atr: float = 0.15
    sweep_lookback: int = 12
    require_sweep: bool = True
    confirm_mode: str = "either"     # engulf | reject | either | close
    wick_ratio: float = 0.5
    min_rr: float = 1.5
    use_partial: bool = True
    partial_at_rr: float = 1.5
    partial_pct: float = 50.0
    sl_buf_atr: float = 0.25
    spread: float = 0.0
    atr_period: int = 14


LEVEL_MULT = [("3", 1.000), ("2", 0.618), ("1", 0.382)]


def build_pivots(m15: pd.DataFrame) -> dict:
    """dict: hari (normalized Timestamp) -> {nama level: harga}."""
    daily = resample(m15, "1D")
    out = {}
    for i in range(1, len(daily)):
        ph, pl, pc = daily.at[i - 1, "high"], daily.at[i - 1, "low"], \
            daily.at[i - 1, "close"]
        rng = ph - pl
        if rng <= 0:
            continue
        pp = (ph + pl + pc) / 3.0
        lv = {"PP": pp}
        for name, mult in LEVEL_MULT:
            lv["R" + name] = pp + rng * mult
            lv["S" + name] = pp - rng * mult
        out[daily.at[i, "time"].normalize()] = lv
    return out


def h1_atr_series(m15: pd.DataFrame, period: int) -> np.ndarray:
    """ATR H1 (hanya bar yang sudah close) dipetakan ke tiap bar M15."""
    h1 = resample(m15, "1h").copy()
    prev_c = h1["close"].shift(1)
    tr = pd.concat([h1["high"] - h1["low"],
                    (h1["high"] - prev_c).abs(),
                    (h1["low"] - prev_c).abs()], axis=1).max(axis=1)
    h1["atr"] = tr.rolling(period).mean().shift(1)   # shift: tanpa lookahead
    merged = pd.merge_asof(m15[["time"]], h1[["time", "atr"]],
                           on="time", direction="backward")
    return merged["atr"].to_numpy()


class PivotEngine:
    def __init__(self, cfg: PivotConfig):
        self.cfg = cfg
        self.trades: list = []
        self.open_trade: Optional[Trade] = None
        self.diag = dict(days=0, tap=0, sweep_fail=0, reclaim_fail=0,
                         confirm_fail=0, rr_block=0, entries=0)

    # ------------- konfirmasi reaction candle -------------
    def _confirm(self, o, h, l, c, po, pc, direction) -> Optional[str]:
        cfg = self.cfg
        body, pbody = abs(c - o), abs(pc - po)
        rng = h - l
        if direction > 0:
            eng = pc < po and c > o and c >= po and o <= pc and body > pbody
            lw = min(o, c) - l
            rej = rng > 0 and lw >= cfg.wick_ratio * rng and c > l + 0.5 * rng
            cls = c > o
        else:
            eng = pc > po and c < o and c <= po and o >= pc and body > pbody
            uw = h - max(o, c)
            rej = rng > 0 and uw >= cfg.wick_ratio * rng and c < h - 0.5 * rng
            cls = c < o
        if cfg.confirm_mode == "engulf":
            return "engulf" if eng else None
        if cfg.confirm_mode == "reject":
            return "reject" if rej else None
        if cfg.confirm_mode == "close":
            return "close" if cls else None
        if eng:
            return "engulf"
        if rej:
            return "reject"
        return None

    def _pick_tp(self, levels: dict, level_name: str, direction: int,
                 entry: float, risk: float) -> float:
        """Level pivot berikutnya searah profit dengan RR >= min_rr."""
        order = ["S3", "S2", "S1", "PP", "R1", "R2", "R3"]
        idx = order.index(level_name)
        cands = order[idx + 1:] if direction > 0 else list(reversed(order[:idx]))
        for nm in cands:
            tgt = levels[nm]
            rr = (tgt - entry) / risk if direction > 0 else (entry - tgt) / risk
            if rr >= self.cfg.min_rr:
                return tgt
        return 0.0

    # ------------- manajemen posisi (identik gaya JDZ) -------------
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
                tr.result_r = banked + (1 - frac) * tr.rr_target
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
                tr.result_r = tr.rr_target
                self._close(tr); return

    def _close(self, tr):
        self.trades.append(tr)
        self.open_trade = None


def run_pivot(m15: pd.DataFrame, cfg: PivotConfig) -> PivotEngine:
    eng = PivotEngine(cfg)
    pivots = build_pivots(m15)
    atr = h1_atr_series(m15, cfg.atr_period)

    times = m15["time"].tolist()
    O = m15["open"].to_numpy(); H = m15["high"].to_numpy()
    L = m15["low"].to_numpy(); C = m15["close"].to_numpy()

    cur_day = None
    levels = None
    day_start = None
    traded = set()
    look = cfg.sweep_lookback
    eng.diag["days"] = len(pivots)

    for m in range(len(times)):
        if eng.open_trade is not None:
            eng._manage_open(times, H, L, m)
            if eng.open_trade is not None:
                continue
        t = times[m]
        day = t.normalize()
        if day != cur_day:
            cur_day = day
            levels = pivots.get(day)
            traded = set()
            day_start = t
        if levels is None or m < look + 1:
            continue
        a = atr[m]
        if not np.isfinite(a) or a <= 0:
            continue
        band = cfg.tol_atr * a

        entered = False
        # BUY di S-level (terdalam dulu), SELL di R-level (tertinggi dulu)
        for name, direction in (("S3", 1), ("S2", 1), ("S1", 1),
                                ("R3", -1), ("R2", -1), ("R1", -1)):
            if entered or name in traded:
                continue
            lv = levels[name]
            if direction > 0:
                tapped = L[m] <= lv + band and H[m] > lv
            else:
                tapped = H[m] >= lv - band and L[m] < lv
            if not tapped:
                continue
            eng.diag["tap"] += 1
            traded.add(name)          # satu kesempatan per level per hari
            if cfg.require_sweep:
                if direction > 0:
                    swept = L[m] < L[m - look:m].min()
                else:
                    swept = H[m] > H[m - look:m].max()
                if not swept:
                    eng.diag["sweep_fail"] += 1
                    continue
            reclaim = C[m] > lv if direction > 0 else C[m] < lv
            if not reclaim:
                eng.diag["reclaim_fail"] += 1
                continue
            conf = eng._confirm(O[m], H[m], L[m], C[m], O[m - 1], C[m - 1],
                                direction)
            if conf is None:
                eng.diag["confirm_fail"] += 1
                continue
            close = C[m]
            entry = close + cfg.spread if direction > 0 else close
            if direction > 0:
                sl = L[m] - cfg.sl_buf_atr * a
            else:
                sl = H[m] + cfg.sl_buf_atr * a
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            tp = eng._pick_tp(levels, name, direction, entry, risk)
            if tp <= 0:
                eng.diag["rr_block"] += 1
                continue
            rr_target = abs(tp - entry) / risk
            eng.open_trade = Trade(
                zone_kind=name, zone_t0=day_start,
                zone_top=lv + band, zone_bottom=lv - band,
                direction=direction, entry_time=t, entry=entry,
                sl=sl, tp=tp, risk=risk, rr_target=rr_target, confirm=conf)
            eng.diag["entries"] += 1
            entered = True
    return eng
