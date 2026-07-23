"""Provider LLM — abstraksi supaya gampang ganti (Minimax, Anthropic, ...).

Minimax expose endpoint chat-completions yang OpenAI-compatible, jadi
implementasinya cuma request HTTP biasa (role/content messages in,
choices[0].message.content out). Anthropic pakai Messages API-nya sendiri.
"""
from dataclasses import dataclass
import json
import urllib.request
import urllib.error

from .config import BrainConfig


@dataclass
class ProviderError(Exception):
    message: str

    def __str__(self):
        return self.message


def _post_json(url: str, headers: dict, body: dict, timeout_s: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise ProviderError(f"HTTP {e.code}: {detail[:400]}")
    except OSError as e:
        # Mencakup urllib.error.URLError, TimeoutError/socket.timeout, koneksi putus, dst.
        # (semuanya turunan OSError) — satu setup gagal jangan sampai bikin crash
        # backtest berjam-jam; biarkan decide() mengembalikan SKIP dengan alasan jelas.
        raise ProviderError(
            f"request gagal/timeout setelah {timeout_s}s ({e}) — kalau ini sering terjadi "
            "setelah menaikkan max_tokens, naikkan juga PAF_LLM_TIMEOUT_S di .env")


class MinimaxProvider:
    """OpenAI-compatible chat completions endpoint.

    Verifikasi base_url/model terkini di console Minimax kamu (MINIMAX_BASE_URL /
    MINIMAX_MODEL di .env) — nama model & path endpoint provider LLM suka berubah.
    """

    def __init__(self, cfg: BrainConfig):
        self.cfg = cfg

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if not self.cfg.minimax_api_key:
            raise ProviderError("MINIMAX_API_KEY kosong di .env")
        url = self.cfg.minimax_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.minimax_api_key}",
        }
        body = {
            "model": self.cfg.minimax_model,
            "max_tokens": self.cfg.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        resp = _post_json(url, headers, body, self.cfg.timeout_s)
        try:
            return resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"respons tidak sesuai format: {json.dumps(resp)[:400]}")


class AnthropicProvider:
    def __init__(self, cfg: BrainConfig):
        self.cfg = cfg

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if not self.cfg.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY kosong di .env")
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.cfg.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.cfg.anthropic_model,
            "max_tokens": self.cfg.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        resp = _post_json(url, headers, body, self.cfg.timeout_s)
        try:
            return resp["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"respons tidak sesuai format: {json.dumps(resp)[:400]}")


def get_provider(cfg: BrainConfig):
    if cfg.provider == "minimax":
        return MinimaxProvider(cfg)
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg)
    raise ProviderError(f"provider tidak dikenal: {cfg.provider}")
