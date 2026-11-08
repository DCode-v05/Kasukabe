"""A stand-in for the world's ledger, so an eval can run with no world.

The broker asks the world for the caller's balance before every call
(`broker/ledger.py`'s `HttpLedgerClient`: `GET /ledger/payer/{id}`, then
`POST /ledger/debit`). An offline eval - `world.evals.verb_reach`, or a
replay - has no world running, so every call fails with a connection error
and the run spends nothing and proves nothing. Mock mode is not the answer
either: it mocks Bedrock too, so there is no model to measure.

This answers those two routes with a balance large enough that nothing is
refused for funds. The broker's own SPEND_CAP_USD still caps real spend,
which is the limit that matters.

    python -m world.evals.stub_ledger &          # on :8100, where WORLD_BASE_URL points

Kept in the repository rather than in a scratch directory because it was
rewritten from memory three times in two days, and each time the eval it
was blocking looked like a broker fault.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BALANCE_CENTS = 10_000_000
PORT = 8100


class Ledger(BaseHTTPRequestHandler):
    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        match = re.fullmatch(r"/ledger/payer/([^/?]+)", self.path.split("?", 1)[0])
        if not match:
            self.send_error(404)
            return
        agent = match.group(1)
        self._send({
            "agent_id": agent, "payer_agent_id": agent,
            "balance_cents": BALANCE_CENTS, "is_minor": False,
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/ledger/debit":
            self.send_error(404)
            return
        raw = self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}"
        body = json.loads(raw)
        self._send({
            "payer_agent_id": body.get("agent_id", "?"),
            "balance_cents_after": BALANCE_CENTS,
        })

    def log_message(self, *args) -> None:  # noqa: D102 - quiet by design
        pass


if __name__ == "__main__":
    print(f"stub ledger on :{PORT} - every agent holds ${BALANCE_CENTS / 100:,.0f}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Ledger).serve_forever()
