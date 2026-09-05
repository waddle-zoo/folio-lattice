from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .mcp_protocol import McpProtocol
from .service import FolioLattice


def make_protocol() -> McpProtocol:
    db_path = os.environ.get("FOLIO_DB_PATH", ".data/folio.db")
    blob_root = os.environ.get("FOLIO_BLOB_ROOT", ".data/blobs")
    return McpProtocol(FolioLattice(db_path, blob_root))


class Handler(BaseHTTPRequestHandler):
    protocol: McpProtocol

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/mcp":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            response = self.protocol.handle(request)
            if response is None:
                self.send_response(202)
                self.end_headers()
                return
            payload = json.dumps(response, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_error(400, str(exc))

    def log_message(self, format: str, *args: object) -> None:
        return


def run_http(host: str, port: int) -> None:
    protocol = make_protocol()
    Handler.protocol = protocol
    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()


def run_stdio() -> None:
    protocol = make_protocol()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = protocol.handle(json.loads(line))
            if response is not None:
                sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
                sys.stdout.flush()
        except (ValueError, json.JSONDecodeError) as exc:
            sys.stdout.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
                )
                + "\n"
            )
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("http", "stdio"), default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    else:
        run_http(args.host, args.port)


if __name__ == "__main__":
    main()
