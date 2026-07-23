"""SMC liquidity-to-liquidity engine.

Alur setup (long; short mirror):
  1. POOL   : peta liquidity = PDH/PDL, Asia high/low (00-07), swing H1
              (fractal n=3) yang belum dikonsumsi.
  2. SWEEP  : bar M15 menembus pool bawah dengan wick lalu CLOSE balik di
              atasnya -> sell-side liquidity diambil.
  3. CHOCH  : dalam `choch_window` bar, close menembus swing high M15
              terakhir -> change of character.
  4. ENTRY  : limit di tepi proximal FVG bullish yang terbentuk pada leg
              displacement (sweep -> choch); hangus setelah `entry_window`.
  5. SL     : di bawah ekstrem sweep - buffer ATR.
  6. TP     : pool liquidity terdekat DI ATAS entry dengan RR >= min_rr
              (liquidity to liquidity).
  Manajemen: partial `partial_pct`% di `partial_at_rr`, SL ke BE, sisanya
  lari ke TP. Semua keputusan pada bar yang sudah close (tanpa lookahead).
"""
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

from jdz_engine import Trade, resample
from trend_engine import _atr_map


@dataclass
class SmcConfig:
    swing_n_pool: int = 3        # fractal H1 untuk pool liquidity
    swing_n_struct: int = 2      # fractal M15 untuk level CHoCH
    pool_max_age_h1: int = 400   # pool swing kadaluarsa setelah ini (bar H1)
    choch_window: int = 16       # bar M15 menunggu CHoCH setelah sweep
    entry_window: int = 24       # bar M15 menunggu fill limit FVG
    fvg_entry: str = "top"       # "top" = tepi proximal, "mid" = tengah FVG
    sl_buf_atr: float = 0.25
    min_rr: float = 2.0
    use_partial: bool = True
    partial_at_rr: float = 1.0
    partial_pct: float = 50.0
    spread: float = 0.0
    session_start: int = 7       # jam server: window entry (manajemen 24 jam)
    session_end: int = 20
    cooldown_bars: int = 8
    atr_period: int = 14
    fallback_ote: bool = False   # tanpa FVG: entry di 50% retrace leg
    exclude_pools: tuple = ()    # jenis pool yang tidak boleh jadi trigger
                                 # sweep, mis. ("ASIAH",)
    long_only: bool = False


@dataclass
class Pool:
    level: float
    kind: str                    # PDH/PDL/ASIAH/ASIAL/SWH/SWL
    born_h1: int = 0
    consumed: bool = False


@dataclass
class Setup:
    direction: int               # +1 long (sweep bawah), -1 short
    sweep_idx: int
    sweep_ext: float             # ekstrem wick sweep
    pool_kind: str
    choch_level: float
    choch_deadline: int
    state: str = "SWEPT"         # SWEPT -> ARMED (choch ok, nunggu fill)
    entry_level: float = 0.0
    fvg_top: float = 0.0
    fvg_bottom: float = 0.0
    fill_deadline: int = 0


class SmcEngine:
    def __init__(self, cfg: SmcConfig):
        self.cfg = cfg
        self.trades: list = []
        self.open_trade: Optional[Trade] = None
        self.pools: list = []
        self.setup: Optional[Setup] = None
        self.diag = dict(pools=0, sweep=0, choch_fail=0, choch_ok=0,
                         no_fvg=0, rr_block=0, expired=0, sess_cancel=0,
                         entries=0)

    # ---------------- pools ----------------
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
            if p.kind in ("SWH", "SWL") and \
               h1_idx - p.born_h1 > cfg.pool_max_age_h1:
                p.consumed = True
                continue
            # close decisively menembus pool = liquidity itu sudah diambil
            if p.kind in ("PDH", "ASIAH", "SWH") and close > p.level:
                p.consumed = True
            elif p.kind in ("PDL", "ASIAL", "SWL") and close < p.level:
                p.consumed = True

    def _nearest_target(self, direction, entry, risk):
        best = 0.0
        for p in self.pools:
            if p.consumed:
                continue
            if direction > 0 and p.kind in ("PDH", "ASIAH", "SWH") \
               and p.level > entry:
                rr = (p.level - entry) / risk
                if rr >= self.cfg.min_rr and (best == 0.0 or p.level < best):
                    best = p.level
            elif direction < 0 and p.kind in ("PDL", "ASIAL", "SWL") \
                    and p.level < entry:
                rr = (entry - p.level) / risk
                if rr >= self.cfg.min_rr and (best == 0.0 or p.level > best):
                    best = p.level
        return best

    # ---------------- struktur M15 ----------------
    def _last_struct_level(self, H, L, m, direction):
        """Swing M15 terakhir yang jadi level CHoCH (di atas utk long)."""
        n = self.cfg.swing_n_struct
        c_now = None
        for j in range(m - n, max(m - 60, n - 1), -1):
            if direction > 0:
                ok = all(H[j] > H[j - k] and H[j] > H[j + k]
                         for k in range(1, n + 1))
                if ok and (c_now is None or H[j] > c_now):
                    return H[j]
            else:
                ok = all(L[j] < L[j - k] and L[j] < L[j + k]
                         for k in range(1, n + 1))
                if ok and (c_now is None or L[j] < c_now):
                    return L[j]
        return None

    def _find_fvg(self, H, L, lo, hi, direction):
        """FVG terakhir (paling dekat harga) dalam leg lo..hi (index M15)."""
        for k in range(hi, lo + 1, -1):
            if direction > 0 and L[k] > H[k - 2]:
                return H[k - 2], L[k]        # bottom, top
            if direction < 0 and H[k] < L[k - 2]:
                return H[k], L[k - 2]
        return None

    # ---------------- manajemen posisi ----------------
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


def run_smc(m15: pd.DataFrame, cfg: SmcConfig) -> SmcEngine:
    eng = SmcEngine(cfg)
    atr = _atr_map(m15, "1h", cfg.atr_period)

    times = m15["time"].tolist()
    hours = m15["time"].dt.hour.to_numpy()
    days = m15["time"].dt.normalize()
    O = m15["open"].to_numpy(); H = m15["high"].to_numpy()
    L = m15["low"].to_numpy(); C = m15["close"].to_numpy()

    # H1 utk pool swing
    h1 = resample(m15, "1h")
    h1_times = h1["time"].tolist()
    h1H = h1["high"].to_numpy(); h1L = h1["low"].to_numpy()
    n_pool = cfg.swing_n_pool

    # daily utk PDH/PDL
    daily = resample(m15, "1D")
    daily_map = {daily.at[i, "time"].normalize():
                 (daily.at[i - 1, "high"], daily.at[i - 1, "low"])
                 for i in range(1, len(daily))}

    h1_idx = 0
    cur_day = None
    asia_done = False
    day_start_m = 0
    last_close_m = -10_000

    for m in range(len(times)):
        t = times[m]

        # --- pool harian ---
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

        # --- pool swing H1 (bar H1 yang sudah close) ---
        while h1_idx < len(h1_times) - 1 and t >= h1_times[h1_idx + 1]:
            i = h1_idx
            j = i - n_pool
            if j >= n_pool:
                if all(h1H[j] > h1H[j - k] and h1H[j] > h1H[j + k]
                       for k in range(1, n_pool + 1)):
                    eng._add_pool(h1H[j], "SWH", born_h1=i)
                if all(h1L[j] < h1L[j - k] and h1L[j] < h1L[j + k]
                       for k in range(1, n_pool + 1)):
                    eng._add_pool(h1L[j], "SWL", born_h1=i)
            h1_idx += 1

        # --- manajemen posisi ---
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

        # --- state machine setup ---
        s = eng.setup
        if s is not None:
            if s.state == "SWEPT":
                if m > s.choch_deadline:
                    eng.diag["choch_fail"] += 1
                    eng.setup = None
                elif (s.direction > 0 and C[m] > s.choch_level) or \
                     (s.direction < 0 and C[m] < s.choch_level):
                    fvg = eng._find_fvg(H, L, s.sweep_idx, m, s.direction)
                    if fvg is None and cfg.fallback_ote:
                        # entry cadangan: 50% retrace leg sweep->choch
                        if s.direction > 0:
                            ext = H[s.sweep_idx:m + 1].max()
                            lvl = ext - 0.5 * (ext - s.sweep_ext)
                        else:
                            ext = L[s.sweep_idx:m + 1].min()
                            lvl = ext + 0.5 * (s.sweep_ext - ext)
                        fvg = (lvl - 0.1 * a, lvl + 0.1 * a)
                    if fvg is None:
                        eng.diag["no_fvg"] += 1
                        eng.setup = None
                    else:
                        s.fvg_bottom, s.fvg_top = fvg
                        if s.direction > 0:
                            s.entry_level = s.fvg_top if cfg.fvg_entry == "top" \
                                else (s.fvg_top + s.fvg_bottom) / 2
                        else:
                            s.entry_level = s.fvg_bottom if cfg.fvg_entry == "top" \
                                else (s.fvg_top + s.fvg_bottom) / 2
                        s.state = "ARMED"
                        s.fill_deadline = m + cfg.entry_window
                        eng.diag["choch_ok"] += 1
            elif s.state == "ARMED":
                if m > s.fill_deadline:
                    eng.diag["expired"] += 1
                    eng.setup = None
                else:
                    filled = (L[m] <= s.entry_level) if s.direction > 0 \
                        else (H[m] >= s.entry_level)
                    in_session = cfg.session_start <= hours[m] < cfg.session_end
                    if filled and in_session and \
                       m - last_close_m >= cfg.cooldown_bars:
                        entry = s.entry_level + (cfg.spread if s.direction > 0
                                                 else 0.0)
                        if s.direction > 0:
                            sl = s.sweep_ext - cfg.sl_buf_atr * a
                        else:
                            sl = s.sweep_ext + cfg.sl_buf_atr * a
                        risk = abs(entry - sl)
                        tp = eng._nearest_target(s.direction, entry, risk) \
                            if risk > 0 else 0.0
                        if tp <= 0:
                            eng.diag["rr_block"] += 1
                            eng.setup = None
                        else:
                            rr_target = abs(tp - entry) / risk
                            eng.open_trade = Trade(
                                zone_kind=s.pool_kind + "-sweep",
                                zone_t0=times[s.sweep_idx],
                                zone_top=s.fvg_top, zone_bottom=s.fvg_bottom,
                                direction=s.direction, entry_time=t,
                                entry=entry, sl=sl, tp=tp, risk=risk,
                                rr_target=rr_target, confirm="choch+fvg")
                            eng.diag["entries"] += 1
                            eng.setup = None
                    elif filled:
                        eng.diag["sess_cancel"] += 1
                        eng.setup = None   # tersentuh di luar sesi: hangus

        # --- deteksi sweep baru (hanya jika tidak ada setup aktif) ---
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
                    eng.setup = Setup(
                        direction=direction, sweep_idx=m,
                        sweep_ext=L[m] if direction > 0 else H[m],
                        pool_kind=p.kind, choch_level=lvl,
                        choch_deadline=m + cfg.choch_window)
                    eng.diag["sweep"] += 1

        eng._consume_through(C[m], h1_idx)
    return eng
