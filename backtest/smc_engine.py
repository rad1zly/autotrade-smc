"""PAF-QIE backtest engine — SMC liquidity-to-liquidity dengan otak LLM.

Alur (sama dengan EA MQL5 live, supaya backtest berarti):
  1. POOL   : PDH/PDL, Asia high/low (00-07 server), swing H1 (fractal-n)
              yang belum "dikonsumsi" (di-close-through).
  2. SWEEP  : bar M15 menembus pool dengan wick lalu CLOSE balik di sisi
              dalam -> liquidity itu dianggap diambil.
  3. MSS    : dalam `mss_window` bar, close menembus swing M15 terakhir
              yang berlawanan (change of character).
  4. BRAIN  : begitu MSS confirmed, konteks pasar dikirim ke brain.decision
              (LLM sungguhan atau mock rule-based) — brain yang menentukan
              BUY/SELL/SKIP + SL/TP, BUKAN rumus tetap.
  5. ENTRY  : market order di OPEN bar berikutnya (tanpa lookahead) kalau
              brain valid & lolos gate (RR min, confidence min, dst).
  Manajemen: partial di +`partial_at_rr`, SL ke BE, sisa lari ke TP brain.
"""
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional
import numpy as np
import pandas as pd

from common import Trade, resample, atr_map


@dataclass
class SmcConfig:
    swing_n_pool: int = 3
    swing_n_struct: int = 2
    pool_max_age_h1: int = 400
    mss_window: int = 16
    ctx_bars: int = 40
    sl_buf_atr: float = 0.25          # dipakai mock brain saja
    min_rr: float = 2.0
    use_partial: bool = True
    partial_at_rr: float = 1.5
    partial_pct: float = 50.0
    spread: float = 0.0
    cooldown_bars: int = 12
    atr_period: int = 14
    exclude_pools: tuple = ()
    long_only: bool = False
    digits: int = 2


@dataclass
class Pool:
    level: float
    kind: str
    born_h1: int = 0
    consumed: bool = False


@dataclass
class Setup:
    direction: int               # +1 long (sweep bawah -> bias buy), -1 short
    sweep_idx: int
    sweep_ext: float
    pool_kind: str
    choch_level: float
    choch_deadline: int


class SmcEngine:
    def __init__(self, cfg: SmcConfig, decide_fn: Callable[[dict], dict]):
        self.cfg = cfg
        self.decide_fn = decide_fn
        self.trades: list = []
        self.open_trade: Optional[Trade] = None
        self.pools: list = []
        self.setup: Optional[Setup] = None
        self.diag = Counter()
        self.reject_reasons = Counter()
        self.setups: list = []   # log LENGKAP tiap setup MSS-confirmed (traded ATAU skip),
                                 # termasuk alasan asli LLM — bukan cuma tally count

    def _add_pool(self, level, kind, born_h1=0):
        self.pools.append(Pool(level, kind, born_h1))
        self.diag["pools"] += 1
        if len(self.pools) > 150:
            self.pools = [p for p in self.pools if not p.consumed][-120:]

    def _consume_through(self, close, h1_idx):
        cfg = self.cfg
        for p in self.pools:
            if p.consumed:
                continue
            if p.kind in ("SWH", "SWL") and h1_idx - p.born_h1 > cfg.pool_max_age_h1:
                p.consumed = True
                continue
            if p.kind in ("PDH", "ASIAH", "SWH") and close > p.level:
                p.consumed = True
            elif p.kind in ("PDL", "ASIAL", "SWL") and close < p.level:
                p.consumed = True

    def _pool_ctx(self):
        return [{"origin": p.kind, "buySide": p.kind in ("PDH", "ASIAH", "SWH"),
                 "price": p.level} for p in self.pools if not p.consumed]

    def _last_struct_level(self, H, L, m, direction):
        n = self.cfg.swing_n_struct
        c_now = None
        for j in range(m - n, max(m - 60, n - 1), -1):
            if direction > 0:
                ok = all(H[j] > H[j - k] and H[j] > H[j + k] for k in range(1, n + 1))
                if ok and (c_now is None or H[j] > c_now):
                    return H[j]
            else:
                ok = all(L[j] < L[j - k] and L[j] < L[j + k] for k in range(1, n + 1))
                if ok and (c_now is None or L[j] < c_now):
                    return L[j]
        return None

    def _find_fvg(self, H, L, lo, hi, direction):
        for k in range(hi, lo + 1, -1):
            if direction > 0 and L[k] > H[k - 2]:
                return H[k - 2], L[k]
            if direction < 0 and H[k] < L[k - 2]:
                return H[k], L[k - 2]
        return None

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


def _fmt_candles(times, O, H, L, C, m, n):
    lo = max(0, m - n + 1)
    return [[str(times[i]), float(O[i]), float(H[i]), float(L[i]), float(C[i])]
            for i in range(lo, m + 1)]


def run_smc(m15: pd.DataFrame, cfg: SmcConfig, decide_fn: Callable[[dict], dict]) -> SmcEngine:
    eng = SmcEngine(cfg, decide_fn)
    atr = atr_map(m15, "1h", cfg.atr_period)

    times = m15["time"].tolist()
    hours = m15["time"].dt.hour.to_numpy()
    days = m15["time"].dt.normalize()
    O = m15["open"].to_numpy(); H = m15["high"].to_numpy()
    L = m15["low"].to_numpy(); C = m15["close"].to_numpy()

    h1 = resample(m15, "1h")
    h1_times = h1["time"].tolist()
    h1H = h1["high"].to_numpy(); h1L = h1["low"].to_numpy()
    n_pool = cfg.swing_n_pool

    daily = resample(m15, "1D")
    daily_map = {daily.at[i, "time"].normalize():
                 (daily.at[i - 1, "high"], daily.at[i - 1, "low"])
                 for i in range(1, len(daily))}

    h1_idx = 0
    cur_day = None
    asia_done = False
    day_start_m = 0
    last_close_m = -10_000

    for m in range(len(times) - 1):  # -1: entry butuh bar m+1 (open, no lookahead)
        t = times[m]

        d = days[m]
        if d != cur_day:
            cur_day = d
            asia_done = False
            day_start_m = m
            pdhl = daily_map.get(d)
            if pdhl:
                eng._add_pool(pdhl[0], "PDH")
                eng._add_pool(pdhl[1], "PDL")
        if not asia_done and hours[m] >= 7:
            seg = slice(day_start_m, m)
            if seg.stop > seg.start:
                eng._add_pool(H[seg].max(), "ASIAH")
                eng._add_pool(L[seg].min(), "ASIAL")
            asia_done = True

        while h1_idx < len(h1_times) - 1 and t >= h1_times[h1_idx + 1]:
            i = h1_idx
            j = i - n_pool
            if j >= n_pool:
                if all(h1H[j] > h1H[j - k] and h1H[j] > h1H[j + k] for k in range(1, n_pool + 1)):
                    eng._add_pool(h1H[j], "SWH", born_h1=i)
                if all(h1L[j] < h1L[j - k] and h1L[j] < h1L[j + k] for k in range(1, n_pool + 1)):
                    eng._add_pool(h1L[j], "SWL", born_h1=i)
            h1_idx += 1

        if eng.open_trade is not None:
            eng._manage_open(times, H, L, m)
            if eng.open_trade is None:
                last_close_m = m
            eng._consume_through(C[m], h1_idx)
            continue

        a = atr[m]
        if m < 30 or not np.isfinite(a) or a <= 0:
            eng._consume_through(C[m], h1_idx)
            continue

        s = eng.setup
        if s is not None:
            if m > s.choch_deadline:
                eng.diag["mss_fail"] += 1
                eng.setup = None
            elif (s.direction > 0 and C[m] > s.choch_level) or \
                 (s.direction < 0 and C[m] < s.choch_level):
                eng.diag["mss_ok"] += 1
                if m - last_close_m < cfg.cooldown_bars:
                    eng.diag["cooldown_block"] += 1
                else:
                    fvg = eng._find_fvg(H, L, s.sweep_idx, m, s.direction)
                    ctx = {
                        "symbol": "BACKTEST", "tf": "M15", "time": str(t),
                        "digits": cfg.digits,
                        "bid": float(C[m]), "ask": float(C[m] + cfg.spread),
                        "atr": float(a),
                        "trend": "BULLISH" if s.direction > 0 else "BEARISH",
                        "bias": "BUY" if s.direction > 0 else "SELL",
                        "sweep": {"origin": s.pool_kind, "side": "SSL" if s.direction > 0 else "BSL",
                                  "extreme": float(s.sweep_ext), "poolPrice": float(s.sweep_ext),
                                  "time": str(times[s.sweep_idx])},
                        "mss": {"dir": "BULLISH" if s.direction > 0 else "BEARISH",
                                "level": float(s.choch_level), "time": str(t)},
                        "fvg": ({"valid": True, "bullish": s.direction > 0,
                                 "bottom": float(fvg[0]), "top": float(fvg[1])} if fvg else None),
                        "pools": eng._pool_ctx(),
                        "candles": _fmt_candles(times, O, H, L, C, m, cfg.ctx_bars),
                        "min_rr": cfg.min_rr,
                    }
                    result = decide_fn(ctx)
                    log_row = {
                        "mss_time": t, "bias": ctx["bias"], "sweep_pool": s.pool_kind,
                        "sweep_extreme": round(float(s.sweep_ext), cfg.digits),
                        "mss_level": round(float(s.choch_level), cfg.digits),
                        "llm_action": result.get("action", "?"),
                        "confidence": result.get("confidence", 0),
                        "sl": result.get("sl", 0), "tp": result.get("tp", 0),
                        "rr": round(result.get("rr", 0.0), 2),
                        "valid": result.get("valid", False),
                        "note": result.get("note", ""),
                        "reason": result.get("reason", ""),
                        "traded": False,
                    }
                    if not result.get("valid"):
                        eng.diag["brain_reject"] += 1
                        eng.reject_reasons[result.get("note", "?")] += 1
                        eng.setups.append(log_row)
                    else:
                        entry = float(O[m + 1]) + (cfg.spread if s.direction > 0 else 0.0)
                        risk = abs(entry - result["sl"])
                        if risk <= 0:
                            eng.diag["brain_reject"] += 1
                            eng.reject_reasons["risk<=0 di entry aktual"] += 1
                            log_row["note"] = "risk<=0 di entry aktual"
                            eng.setups.append(log_row)
                        else:
                            rr_target = abs(result["tp"] - entry) / risk
                            eng.open_trade = Trade(
                                zone_kind=s.pool_kind + "-sweep", zone_t0=times[s.sweep_idx],
                                zone_top=result["tp"], zone_bottom=result["sl"],
                                direction=s.direction, entry_time=times[m + 1], entry=entry,
                                sl=result["sl"], tp=result["tp"], risk=risk,
                                rr_target=rr_target, confirm="llm",
                                confidence=result.get("confidence", 0),
                                reason=result.get("reason", ""))
                            log_row["traded"] = True
                            eng.setups.append(log_row)
                            eng.diag["entries"] += 1
                eng.setup = None

        if eng.setup is None and eng.open_trade is None:
            best = None
            for p in eng.pools:
                if p.consumed or p.kind in cfg.exclude_pools:
                    continue
                if p.kind in ("PDL", "ASIAL", "SWL"):
                    if L[m] < p.level and C[m] > p.level:
                        if best is None or p.level < best[0].level:
                            best = (p, 1)
                elif p.kind in ("PDH", "ASIAH", "SWH"):
                    if cfg.long_only:
                        continue
                    if H[m] > p.level and C[m] < p.level:
                        if best is None or p.level > best[0].level:
                            best = (p, -1)
            if best is not None:
                p, direction = best
                lvl = eng._last_struct_level(H, L, m, direction)
                if lvl is not None:
                    p.consumed = True
                    eng.setup = Setup(direction=direction, sweep_idx=m,
                                      sweep_ext=L[m] if direction > 0 else H[m],
                                      pool_kind=p.kind, choch_level=lvl,
                                      choch_deadline=m + cfg.mss_window)
                    eng.diag["sweep"] += 1

        eng._consume_through(C[m], h1_idx)

    return eng
