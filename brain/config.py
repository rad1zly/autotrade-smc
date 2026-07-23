"""Konfigurasi otak PAF-QIE dari environment variable / .env.

Kunci API TIDAK PERNAH disimpan di MQL5 — hanya di sini, di sisi Python.
"""
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class BrainConfig:
    provider: str = "minimax"          # "minimax" | "anthropic"
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M2"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    min_confidence: int = 70
    min_rr: float = 2.0
    max_sl_atr: float = 5.0
    timeout_s: int = 60
    max_tokens: int = 3000       # model reasoning (mis. MiniMax-M2) pakai banyak token
                                 # di <think> sebelum jawaban JSON final


def load_config() -> BrainConfig:
    return BrainConfig(
        provider=os.getenv("LLM_PROVIDER", "minimax").lower(),
        minimax_api_key=os.getenv("MINIMAX_API_KEY", ""),
        minimax_base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
        minimax_model=os.getenv("MINIMAX_MODEL", "MiniMax-M2"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        min_confidence=int(os.getenv("PAF_MIN_CONFIDENCE", "70")),
        min_rr=float(os.getenv("PAF_MIN_RR", "2.0")),
        max_sl_atr=float(os.getenv("PAF_MAX_SL_ATR", "5.0")),
        timeout_s=int(os.getenv("PAF_LLM_TIMEOUT_S", "60")),
        max_tokens=int(os.getenv("PAF_LLM_MAX_TOKENS", "3000")),
    )
