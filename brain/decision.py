"""Otak keputusan PAF-QIE — dipakai SAMA PERSIS oleh server live (dipanggil
EA MQL5 lewat bridge HTTP) dan oleh backtest Python, supaya perilaku yang
diuji di backtest = perilaku yang jalan live.

Alur: engine (MQL5 atau backtest) mendeteksi setup MEKANIS (sweep pool
liquidity -> MSS/CHoCH) lalu kirim konteks pasar ke sini. Modul ini yang
memutuskan BUY/SELL/SKIP + level SL/TP secara diskresioner (lewat LLM),
lalu memvalidasinya (arah harus sesuai bias, RR minimum, SL tidak kelewat
lebar) sebelum dianggap actionable.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .config import BrainConfig, load_config
from .providers import get_provider, ProviderError

SYSTEM_PROMPT = (
    "You are PAF-QIE, the decision brain of an institutional Smart Money Concept "
    "trading engine. You receive one confirmed setup: a liquidity pool was swept and "
    "a market structure shift (MSS) confirmed in the bias direction. Your job is "
    "discretionary judgment, not rule execution: assess candle context, momentum, "
    "sweep quality, and pool placement, then decide. Constraints: "
    "(1) action must be the given bias direction or SKIP — never counter-bias. "
    "(2) SL must sit beyond the sweep extreme with a sensible buffer, never inside "
    "recent range noise. "
    "(3) TP must target a real opposite-side liquidity pool from the list, at or "
    "before the nearest strong magnet. "
    "(4) If reward:risk from current price is below the stated minimum, SKIP. "
    "(5) If context is choppy, late, or unclear — SKIP; skipping is free, losing is not. "
    "Respond with ONLY one line of JSON, no markdown, no extra text: "
    '{"action":"BUY|SELL|SKIP","sl":<price>,"tp":<price>,"confidence":<0-100>,'
    '"reason":"<max 120 chars>"}'
)


def build_user_prompt(ctx: dict, min_rr: float) -> str:
    dg = ctx.get("digits", 2)

    def f(x):
        return f"{x:.{dg}f}"

    sweep, mss, fvg = ctx["sweep"], ctx["mss"], ctx.get("fvg")
    s = "SETUP CONTEXT\n"
    s += f"symbol={ctx['symbol']} tf={ctx['tf']}\n"
    s += (f"time={ctx['time']} bid={f(ctx['bid'])} ask={f(ctx['ask'])} "
          f"spread={f(ctx['ask']-ctx['bid'])} atr14={f(ctx['atr'])}\n")
    s += f"trend={ctx['trend']} bias={ctx['bias']}\n"
    s += (f"sweep: pool={sweep['origin']}@{f(sweep['poolPrice'])} "
          f"side={sweep['side']} extreme={f(sweep['extreme'])} time={sweep['time']}\n")
    s += f"mss: dir={mss['dir']} level={f(mss['level'])} time={mss['time']}\n"
    if fvg and fvg.get("valid"):
        s += f"fvg: {'bull' if fvg['bullish'] else 'bear'} {f(fvg['bottom'])}-{f(fvg['top'])}\n"

    s += "liquidity pools (TP magnets):\n"
    for p in ctx["pools"]:
        s += f"  {p['origin']} {'BSL' if p['buySide'] else 'SSL'} {f(p['price'])}\n"

    s += f"last {len(ctx['candles'])} candles (time,open,high,low,close), oldest first:\n"
    for c in ctx["candles"]:
        s += f"{c[0]},{f(c[1])},{f(c[2])},{f(c[3])},{f(c[4])}\n"

    s += f"Entry would be a MARKET order at current price. Minimum RR required: {min_rr:.1f}."
    return s


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_decision(text: str) -> dict:
    # Model reasoning (mis. MiniMax-M2) menaruh chain-of-thought di <think>...</think>
    # sebelum jawaban final — buang dulu supaya tidak ikut ke-scan cari JSON.
    stripped = _THINK_RE.sub("", text)
    m = _JSON_RE.search(stripped)
    if not m:
        if "<think>" in text.lower() and "</think>" not in text.lower():
            raise ValueError(
                "respons terpotong di tengah <think> (reasoning model butuh token lebih "
                f"banyak) — naikkan PAF_LLM_MAX_TOKENS di .env. Potongan: {text[:200]}")
        raise ValueError(f"tidak ada JSON di respons: {stripped[:200]}")
    obj = json.loads(m.group(0))
    action = str(obj.get("action", "")).upper()
    if action not in ("BUY", "SELL", "SKIP"):
        raise ValueError(f"action tidak valid: {obj.get('action')}")
    return {
        "action": action,
        "sl": float(obj.get("sl", 0) or 0),
        "tp": float(obj.get("tp", 0) or 0),
        "confidence": int(obj.get("confidence", 0) or 0),
        "reason": str(obj.get("reason", ""))[:200],
    }


def validate(decision: dict, ctx: dict, cfg: BrainConfig) -> dict:
    """Tambah 'valid' (bool) dan 'note' (alasan tolak) ke decision. Engine
    (MQL5/backtest) TETAP boleh melakukan pengecekan sendiri sebelum
    eksekusi order sungguhan — ini adalah lapisan pertama, bukan satu-satunya."""
    out = dict(decision)
    bias = ctx["bias"]
    entry = ctx["ask"] if bias == "BUY" else ctx["bid"]
    is_buy = decision["action"] == "BUY"

    if decision["action"] == "SKIP":
        out.update(valid=False, note="LLM skip", rr=0.0)
        return out
    if decision["action"] != bias:
        out.update(valid=False, note="counter-bias -> skip", rr=0.0)
        return out
    if decision["confidence"] < cfg.min_confidence:
        out.update(valid=False, note="confidence < min", rr=0.0)
        return out

    sane = (decision["sl"] < entry < decision["tp"]) if is_buy \
        else (decision["tp"] < entry < decision["sl"])
    if not sane:
        out.update(valid=False, note="level tidak sane vs entry", rr=0.0)
        return out

    risk = abs(entry - decision["sl"])
    rr = abs(decision["tp"] - entry) / risk if risk > 0 else 0.0
    min_rr = ctx.get("min_rr", cfg.min_rr)
    if rr < min_rr:
        out.update(valid=False, note="RR < min", rr=rr)
        return out
    atr = ctx.get("atr", 0)
    if atr > 0 and risk > cfg.max_sl_atr * atr:
        out.update(valid=False, note="SL terlalu lebar vs ATR", rr=rr)
        return out

    out.update(valid=True, note="", rr=rr, entry=entry)
    return out


def decide(ctx: dict, cfg: Optional[BrainConfig] = None) -> dict:
    """Panggil LLM sungguhan (biaya token nyata) + validasi."""
    cfg = cfg or load_config()
    provider = get_provider(cfg)
    prompt = build_user_prompt(ctx, ctx.get("min_rr", cfg.min_rr))
    try:
        text = provider.complete(SYSTEM_PROMPT, prompt)
        decision = parse_decision(text)
    except (ProviderError, ValueError) as e:
        return {"action": "SKIP", "sl": 0, "tp": 0, "confidence": 0,
                "reason": str(e), "valid": False, "note": f"error: {e}", "rr": 0.0}
    return validate(decision, ctx, cfg)


def decide_mock(ctx: dict, cfg: Optional[BrainConfig] = None) -> dict:
    """TANPA panggil LLM — stand-in rule-based (SL=ekstrem sweep+0.25 ATR,
    TP=pool seberang terdekat RR>=min). Hanya untuk uji cepat pipa/frekuensi
    setup selama iterasi; BUKAN pengganti evaluasi kualitas LLM yang sebenarnya."""
    cfg = cfg or load_config()
    bias = ctx["bias"]
    entry = ctx["ask"] if bias == "BUY" else ctx["bid"]
    atr = ctx.get("atr", 0)
    sweep = ctx["sweep"]
    is_buy = bias == "BUY"
    sl = sweep["extreme"] - 0.25 * atr if is_buy else sweep["extreme"] + 0.25 * atr
    risk = abs(entry - sl)
    min_rr = ctx.get("min_rr", cfg.min_rr)

    best_tp = 0.0
    for p in ctx["pools"]:
        if is_buy and p["buySide"] and p["price"] > entry:
            rr = (p["price"] - entry) / risk if risk > 0 else 0
            if rr >= min_rr and (best_tp == 0.0 or p["price"] < best_tp):
                best_tp = p["price"]
        elif not is_buy and not p["buySide"] and p["price"] < entry:
            rr = (entry - p["price"]) / risk if risk > 0 else 0
            if rr >= min_rr and (best_tp == 0.0 or p["price"] > best_tp):
                best_tp = p["price"]

    decision = {"action": bias if best_tp > 0 else "SKIP", "sl": sl, "tp": best_tp,
                "confidence": 100 if best_tp > 0 else 0, "reason": "mock rule-based"}
    return validate(decision, ctx, cfg)
