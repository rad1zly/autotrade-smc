"""Bridge HTTP lokal — EA MQL5 panggil ke sini (WebRequest) lewat
http://127.0.0.1:8787/decide, di sini yang manggil LLM (Minimax/Anthropic)
dan memvalidasi keputusan sebelum dibalikin ke EA.

Jalankan: python3 -m brain.server
MT5 harus whitelist http://127.0.0.1:8787 di Tools > Options > Expert Advisors
> Allow WebRequest for listed URL.
"""
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import load_config
from .decision import decide

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("paf-qie-brain")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.client_address[0], fmt % args)

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/decide":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            ctx = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, {"error": f"body tidak valid: {e}"})
            return

        cfg = load_config()
        log.info("setup masuk: %s %s bias=%s", ctx.get("symbol"), ctx.get("tf"), ctx.get("bias"))
        result = decide(ctx, cfg)
        log.info("keputusan: %s conf=%s valid=%s note=%s",
                 result.get("action"), result.get("confidence"),
                 result.get("valid"), result.get("note"))
        self._send(200, result)


def main(host: str = "127.0.0.1", port: int = 8787):
    server = ThreadingHTTPServer((host, port), Handler)
    log.info("PAF-QIE brain bridge jalan di http://%s:%d (provider=%s)",
             host, port, load_config().provider)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
