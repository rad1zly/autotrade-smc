"""JDI BARZ backtest engine - port setia dari logika EA MQL5 (Zones.mqh dkk).

Semua keputusan pakai candle CLOSE (tanpa lookahead):
- Zona (FVG/OB/Breaker/Flip) dibangun di bar H1 yang sudah close.
- Entry dikonfirmasi di bar M15 yang sudah close, eksekusi di close bar itu.
- Hasil dihitung dalam R-multiple (kelipatan risiko awal) supaya bebas
  dari urusan lot minimum broker.

Untuk kecepatan pada data multi-tahun, semua perhitungan H1 memakai window
bar terakhir (seperti EA yang CopyRates 650 bar), bukan seluruh histori.
"""
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd

ZK_FVG, ZK_OB, ZK_BREAKER, ZK_FLIP = 0, 1, 2, 3
KIND_NAME = {ZK_FVG: "FVG", ZK_OB: "OB", ZK_BREAKER: "BB", ZK_FLIP: "FLIP"}
TP_FRESH, TP_PRICE_IN, TP_TRADED, TP_INVALID = 0, 1, 2, 3
H1_WINDOW = 700


@dataclass
class Config:
    swing_bars: int = 3
    atr_period: int = 14
    disp_body_atr: float = 0.0
    min_react_atr: float = 0.3
    max_zone_age: int = 300
    flip_fib_max: float = 0.5
    confirm_mode: str = "either"      # engulf | reject | either | close
    wick_ratio: float = 0.5
    min_rr_liq: float = 5.0
    use_partial: bool = True
    partial_at_rr: float = 3.0
    partial_pct: float = 50.0
    sl_buf_atr: float = 0.25
    spread: float = 0.0               # dalam satuan harga (mis. 0.20 utk XAUUSD)
    trade_flip: bool = True
    trade_ob: bool = False
    trade_bb: bool = False
    trade_fvg: bool = False
    warmup_h1_bars: int = 100         # zona dibangun tapi belum boleh entry
    liq_lookback: int = 320


@dataclass
class Zone:
    kind: int
    direction: int                    # +1 demand, -1 supply
    top: float
    bottom: float
    t0: pd.Timestamp
    liq_level: float = 0.0
    phase_validated: bool = False
    touched: bool = False
    reacted: bool = False
    fib1: float = 0.0
    reaction_ext: float = 0.0
    post_ext: float = 0.0
    fib_ok: bool = False
    tphase: int = TP_FRESH
    tradable: bool = False
    alive: bool = True
    age: int = 0
    from_ob_t0: Optional[pd.Timestamp] = None


@dataclass
class Trade:
    zone_kind: str
    zone_t0: pd.Timestamp
    zone_top: float
    zone_bottom: float
    direction: int
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    risk: float
    rr_target: float
    confirm: str
    exit_time: Optional[pd.Timestamp] = None
    exit_reason: str = ""
    result_r: float = 0.0
    partial_done: bool = False
    fib1: float = 0.0
    post_ext: float = 0.0


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


def is_swing_high(h: np.ndarray, i: int, n: int) -> bool:
    if i - n < 0 or i + n >= len(h):
        return False
    for k in range(1, n + 1):
        if h[i - k] >= h[i] or h[i + k] >= h[i]:
            return False
    return True


def is_swing_low(l: np.ndarray, i: int, n: int) -> bool:
    if i - n < 0 or i + n >= len(l):
        return False
    for k in range(1, n + 1):
        if l[i - k] <= l[i] or l[i + k] <= l[i]:
            return False
    return True


def last_swing_high_before(h: np.ndarray, idx: int, n: int) -> float:
    for i in range(idx - n, max(idx - n - 200, n - 1), -1):
        if i + n <= idx and is_swing_high(h, i, n):
            return h[i]
    return 0.0


def last_swing_low_before(l: np.ndarray, idx: int, n: int) -> float:
    for i in range(idx - n, max(idx - n - 200, n - 1), -1):
        if i + n <= idx and is_swing_low(l, i, n):
            return l[i]
    return 0.0


def simple_atr(h, l, c, idx: int, period: int) -> float:
    s, cnt = 0.0, 0
    for i in range(idx, max(idx - period, 0), -1):
        if i - 1 < 0:
            break
        tr = max(h[i], c[i - 1]) - min(l[i], c[i - 1])
        s += tr
        cnt += 1
    return s / cnt if cnt else 0.0


class JdzEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.zones: list = []
        self.trades: list = []
        self.open_trade: Optional[Trade] = None
        self.atr_z = 0.0
        # window H1 (bar yang sudah close, maksimal H1_WINDOW terakhir)
        self.h1_t: list = []
        self.h1 = np.zeros((4, 0))    # rows: O,H,L,C
        self._buf = np.zeros((4, H1_WINDOW))
        self._n = 0
        self.diag = dict(fvg=0, ob=0, ob_valid=0, bb=0, flip=0, flip_fib_ok=0,
                         retest=0, confirm_fail=0, fib_block=0, tp_block=0,
                         entries=0)

    # ---------------- zona TF (H1) ----------------
    def on_h1_close(self, t, o, h, l, c):
        if self._n == H1_WINDOW:
            self._buf[:, :-1] = self._buf[:, 1:]
            self._buf[:, -1] = (o, h, l, c)
            self.h1_t.pop(0)
            self.h1_t.append(t)
        else:
            self._buf[:, self._n] = (o, h, l, c)
            self.h1_t.append(t)
            self._n += 1
        O = self._buf[0, :self._n]
        H = self._buf[1, :self._n]
        L = self._buf[2, :self._n]
        C = self._buf[3, :self._n]
        i = self._n - 1
        self.atr_z = simple_atr(H, L, C, i, self.cfg.atr_period)

        pending: list = []
        for z in self.zones:
            if z.alive:
                self._update_zone(z, i, H, L, C, O, pending)
        self.zones.extend(pending)
        self._detect_new(i, H, L, C, O)
        if len(self.zones) > 300:
            alive = [z for z in self.zones if z.alive]
            dead = [z for z in self.zones if not z.alive][-40:]
            self.zones = dead + alive

    def _update_zone(self, z, i, H, L, C, O, pending):
        cfg = self.cfg
        z.age += 1
        if z.age > cfg.max_zone_age:
            z.alive = False
            if z.tphase in (TP_FRESH, TP_PRICE_IN):
                z.tphase = TP_INVALID
            return
        b_h, b_l, b_c = H[i], L[i], C[i]

        if z.kind == ZK_OB:
            if z.direction > 0:
                self._update_demand_ob(z, i, H, L, C, O, pending)
            else:
                self._update_supply_ob(z, i, H, L, C, O, pending)
            if not z.alive or z.kind != ZK_OB:
                return

        if z.kind == ZK_FLIP and not z.touched:
            if z.direction > 0:
                z.post_ext = max(z.post_ext, b_h)
                if not z.fib_ok:
                    z.fib_ok = z.top <= z.fib1 + cfg.flip_fib_max * (z.post_ext - z.fib1)
            else:
                z.post_ext = min(z.post_ext, b_l)
                if not z.fib_ok:
                    z.fib_ok = z.bottom >= z.fib1 - cfg.flip_fib_max * (z.fib1 - z.post_ext)

        if z.kind != ZK_OB or z.phase_validated:
            if b_l <= z.top and b_h >= z.bottom:
                z.touched = True

        if z.kind != ZK_OB:
            if (z.direction > 0 and b_c < z.bottom) or \
               (z.direction < 0 and b_c > z.top):
                z.alive = False
                if z.tphase != TP_TRADED:
                    z.tphase = TP_INVALID

    def _update_demand_ob(self, z, i, H, L, C, O, pending):
        cfg = self.cfg
        b_h, b_l, b_c = H[i], L[i], C[i]
        if not z.touched:
            if b_l <= z.top:
                z.touched = True
                sw = last_swing_high_before(H, i, cfg.swing_bars)
                z.fib1 = sw if sw > 0 else z.liq_level
                z.reaction_ext = b_h
            else:
                z.liq_level = max(z.liq_level, b_h)
        else:
            z.reaction_ext = max(z.reaction_ext, b_h)
            if not z.reacted and self.atr_z > 0 and \
               z.reaction_ext >= z.top + cfg.min_react_atr * self.atr_z:
                z.reacted = True

        if z.reacted and not z.phase_validated and z.reaction_ext >= z.liq_level:
            z.phase_validated = True
            z.tradable = cfg.trade_ob
            self.diag["ob_valid"] += 1

        if b_c < z.bottom:
            if not z.reacted:
                z.kind = ZK_BREAKER
                z.direction = -1
                z.tradable = cfg.trade_bb
                z.tphase = TP_FRESH
                z.touched = False
                z.age = 0
                self.diag["bb"] += 1
            elif z.reaction_ext >= z.liq_level:
                z.alive = False
            else:
                self._spawn_flip(z, i, H, L, C, O, pending)
                z.alive = False

    def _update_supply_ob(self, z, i, H, L, C, O, pending):
        cfg = self.cfg
        b_h, b_l, b_c = H[i], L[i], C[i]
        if not z.touched:
            if b_h >= z.bottom:
                z.touched = True
                sw = last_swing_low_before(L, i, cfg.swing_bars)
                z.fib1 = sw if sw > 0 else z.liq_level
                z.reaction_ext = b_l
            else:
                z.liq_level = min(z.liq_level, b_l)
        else:
            z.reaction_ext = min(z.reaction_ext, b_l)
            if not z.reacted and self.atr_z > 0 and \
               z.reaction_ext <= z.bottom - cfg.min_react_atr * self.atr_z:
                z.reacted = True

        if z.reacted and not z.phase_validated and z.reaction_ext <= z.liq_level:
            z.phase_validated = True
            z.tradable = cfg.trade_ob
            self.diag["ob_valid"] += 1

        if b_c > z.top:
            if not z.reacted:
                z.kind = ZK_BREAKER
                z.direction = 1
                z.tradable = cfg.trade_bb
                z.tphase = TP_FRESH
                z.touched = False
                z.age = 0
                self.diag["bb"] += 1
            elif z.reaction_ext <= z.liq_level:
                z.alive = False
            else:
                self._spawn_flip(z, i, H, L, C, O, pending)
                z.alive = False

    def _spawn_flip(self, ob, i, H, L, C, O, pending):
        cfg = self.cfg
        found = -1
        for k in range(i - 1, max(i - 13, -1), -1):
            if ob.direction > 0 and C[k] > O[k]:
                found = k; break
            if ob.direction < 0 and C[k] < O[k]:
                found = k; break
        if found < 0:
            return
        f = Zone(kind=ZK_FLIP, direction=-ob.direction,
                 top=H[found], bottom=L[found], t0=self.h1_t[found],
                 tradable=cfg.trade_flip, fib1=ob.fib1, from_ob_t0=ob.t0)
        if f.direction > 0:
            f.post_ext = H[i]
            f.fib_ok = f.top <= f.fib1 + cfg.flip_fib_max * (f.post_ext - f.fib1)
        else:
            f.post_ext = L[i]
            f.fib_ok = f.bottom >= f.fib1 - cfg.flip_fib_max * (f.fib1 - f.post_ext)
        for z in self.zones:
            if z.kind == ZK_FLIP and z.t0 == f.t0 and z.direction == f.direction:
                return
        for z in pending:
            if z.t0 == f.t0 and z.direction == f.direction:
                return
        pending.append(f)
        self.diag["flip"] += 1
        if f.fib_ok:
            self.diag["flip_fib_ok"] += 1

    def _detect_new(self, i, H, L, C, O):
        cfg = self.cfg
        if i < 15:
            return
        # bullish FVG
        if L[i] > H[i - 2]:
            body = abs(C[i - 1] - O[i - 1])
            if self.atr_z <= 0 or body >= cfg.disp_body_atr * self.atr_z:
                self.diag["fvg"] += 1
                if cfg.trade_fvg:
                    self.zones.append(Zone(ZK_FVG, 1, L[i], H[i - 2],
                                           self.h1_t[i - 1], tradable=True))
                ob = -1
                for k in range(i - 2, max(i - 15, -1), -1):
                    if C[k] < O[k]:
                        ob = k; break
                if ob >= 0 and not any(
                        z.kind in (ZK_OB, ZK_BREAKER) and z.t0 == self.h1_t[ob]
                        for z in self.zones):
                    self.zones.append(Zone(ZK_OB, 1, H[ob], L[ob], self.h1_t[ob],
                                           liq_level=H[i]))
                    self.diag["ob"] += 1
        # bearish FVG
        if H[i] < L[i - 2]:
            body = abs(C[i - 1] - O[i - 1])
            if self.atr_z <= 0 or body >= cfg.disp_body_atr * self.atr_z:
                self.diag["fvg"] += 1
                if cfg.trade_fvg:
                    self.zones.append(Zone(ZK_FVG, -1, L[i - 2], H[i],
                                           self.h1_t[i - 1], tradable=True))
                ob = -1
                for k in range(i - 2, max(i - 15, -1), -1):
                    if C[k] > O[k]:
                        ob = k; break
                if ob >= 0 and not any(
                        z.kind in (ZK_OB, ZK_BREAKER) and z.t0 == self.h1_t[ob]
                        for z in self.zones):
                    self.zones.append(Zone(ZK_OB, -1, H[ob], L[ob], self.h1_t[ob],
                                           liq_level=L[i]))
                    self.diag["ob"] += 1

    # ---------------- konfirmasi & entry (M15, pakai array) ------------
    def _confirm(self, o, h, l, c, po, pc, direction, z_top, z_bottom):
        cfg = self.cfg
        if direction > 0 and c < z_bottom:
            return None
        if direction < 0 and c > z_top:
            return None
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

    def _find_liquidity_tp(self, direction, entry, risk):
        cfg = self.cfg
        if risk <= 0:
            return 0.0
        H = self._buf[1, :self._n]
        L = self._buf[2, :self._n]
        cur = self._n - 1
        n = cfg.swing_bars
        best = 0.0
        lo = max(cur - cfg.liq_lookback, n - 1)
        if direction < 0:
            run_min = np.inf
            for j in range(cur - 1, lo, -1):
                if j + n <= cur and is_swing_low(L, j, n):
                    v = L[j]
                    if v < run_min and v < entry:
                        rr = (entry - v) / risk
                        if rr >= cfg.min_rr_liq and (best == 0.0 or v > best):
                            best = v
                run_min = min(run_min, L[j])
        else:
            run_max = -np.inf
            for j in range(cur - 1, lo, -1):
                if j + n <= cur and is_swing_high(H, j, n):
                    v = H[j]
                    if v > run_max and v > entry:
                        rr = (v - entry) / risk
                        if rr >= cfg.min_rr_liq and (best == 0.0 or v < best):
                            best = v
                run_max = max(run_max, H[j])
        return best

    def on_m15_close(self, times, O, H, L, C, m, in_warmup):
        cfg = self.cfg
        if self.open_trade is not None:
            self._manage_open(times, H, L, m)
            if self.open_trade is not None:
                return
        if in_warmup or m < 1:
            return
        e_h, e_l = H[m], L[m]
        e_t = times[m]
        for z in self.zones:
            if not (z.alive and z.tradable):
                continue
            if z.tphase not in (TP_FRESH, TP_PRICE_IN):
                continue
            if z.kind == ZK_OB and not z.phase_validated:
                continue
            if e_t <= z.t0:
                continue
            if not (e_l <= z.top and e_h >= z.bottom):
                continue
            if z.tphase == TP_FRESH:
                self.diag["retest"] += 1
            z.tphase = TP_PRICE_IN
            z.touched = True
            if z.kind == ZK_FLIP and not z.fib_ok:
                self.diag["fib_block"] += 1
                continue
            conf = self._confirm(O[m], H[m], L[m], C[m], O[m - 1], C[m - 1],
                                 z.direction, z.top, z.bottom)
            if conf is None:
                self.diag["confirm_fail"] += 1
                continue
            close = C[m]
            entry = close + cfg.spread if z.direction > 0 else close
            if z.direction > 0:
                sl = min(z.bottom, e_l) - cfg.sl_buf_atr * self.atr_z
            else:
                sl = max(z.top, e_h) + cfg.sl_buf_atr * self.atr_z
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            tp = self._find_liquidity_tp(z.direction, entry, risk)
            if tp <= 0:
                self.diag["tp_block"] += 1
                continue
            rr_target = abs(tp - entry) / risk
            self.open_trade = Trade(
                zone_kind=KIND_NAME[z.kind], zone_t0=z.t0,
                zone_top=z.top, zone_bottom=z.bottom,
                direction=z.direction, entry_time=e_t, entry=entry,
                sl=sl, tp=tp, risk=risk, rr_target=rr_target, confirm=conf,
                fib1=z.fib1, post_ext=z.post_ext)
            z.tphase = TP_TRADED
            self.diag["entries"] += 1
            return   # satu posisi pada satu waktu

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
            partial_lvl = tr.entry + cfg.partial_at_rr * tr.risk
            partial_hit = (not tr.partial_done) and cfg.use_partial and h >= partial_lvl
        else:
            sl_hit = h + sp >= cur_sl
            tp_hit = l + sp <= tr.tp
            partial_lvl = tr.entry - cfg.partial_at_rr * tr.risk
            partial_hit = (not tr.partial_done) and cfg.use_partial and l + sp <= partial_lvl

        # konservatif: SL dicek duluan pada bar yang sama
        if sl_hit and not tr.partial_done:
            tr.exit_time = t
            tr.exit_reason = "sl"
            tr.result_r = -1.0
            self._close(tr)
            return
        if partial_hit:
            tr.partial_done = True
        if tr.partial_done and cfg.use_partial:
            frac = cfg.partial_pct / 100.0
            banked = frac * cfg.partial_at_rr
            be_hit = (l <= tr.entry) if d > 0 else (h + sp >= tr.entry)
            if tp_hit:
                tr.exit_time = t
                tr.exit_reason = "tp"
                tr.result_r = banked + (1 - frac) * tr.rr_target
                self._close(tr)
                return
            if be_hit and not partial_hit:
                tr.exit_time = t
                tr.exit_reason = "be"
                tr.result_r = banked
                self._close(tr)
                return
        else:
            if sl_hit:
                tr.exit_time = t
                tr.exit_reason = "sl"
                tr.result_r = -1.0
                self._close(tr)
                return
            if tp_hit:
                tr.exit_time = t
                tr.exit_reason = "tp"
                tr.result_r = tr.rr_target
                self._close(tr)
                return

    def _close(self, tr):
        self.trades.append(tr)
        self.open_trade = None


def run(m15: pd.DataFrame, cfg: Config) -> JdzEngine:
    eng = JdzEngine(cfg)
    h1 = resample(m15, "1h")
    h1_times = h1["time"].tolist()
    h1_o = h1["open"].to_numpy(); h1_h = h1["high"].to_numpy()
    h1_l = h1["low"].to_numpy(); h1_c = h1["close"].to_numpy()

    times = m15["time"].tolist()
    O = m15["open"].to_numpy(); H = m15["high"].to_numpy()
    L = m15["low"].to_numpy(); C = m15["close"].to_numpy()

    h1_idx = 0
    warmup_end_idx = min(cfg.warmup_h1_bars, len(h1) - 1)
    warmup_end_time = h1_times[warmup_end_idx] if h1_times else None

    for m in range(len(times)):
        t = times[m]
        # H1 bar dianggap close ketika bar M15 pertama JAM BERIKUTNYA muncul
        while h1_idx < len(h1_times) - 1 and t >= h1_times[h1_idx + 1]:
            eng.on_h1_close(h1_times[h1_idx], h1_o[h1_idx], h1_h[h1_idx],
                            h1_l[h1_idx], h1_c[h1_idx])
            h1_idx += 1
        in_warmup = warmup_end_time is not None and t < warmup_end_time
        eng.on_m15_close(times, O, H, L, C, m, in_warmup)
    return eng
