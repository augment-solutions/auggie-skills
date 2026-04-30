#!/usr/bin/env python3
"""Serve a generated deep-wiki output directory as a browsable webpage.

Usage:
    python3 scripts/preview.py <output_dir> [--port 8765] [--no-open]

Renders ``wiki.mdx`` (Markdown + Mermaid + native HTML5 tags) via a small
self-contained viewer at ``preview/index.html``. Uses only the Python standard
library; the viewer pulls ``marked`` and ``mermaid`` from a CDN.
"""
from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PREVIEW_DIR = SKILL_ROOT / "preview"
INDEX_HTML = PREVIEW_DIR / "index.html"


def make_handler(output_dir: Path):
    """Return a SimpleHTTPRequestHandler bound to ``output_dir`` that also
    serves ``index.html`` from the bundled ``preview/`` directory at ``/``."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output_dir), **kwargs)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                # Prefer a pre-bundled static index.html in the output dir
                # (produced by build_static.py) so users see the same view
                # they'd get from file://. Fall back to the skill template.
                bundled = output_dir / "index.html"
                src = bundled if bundled.is_file() else INDEX_HTML
                self._serve_file(src, "text/html; charset=utf-8")
                return
            return super().do_GET()

        def _serve_file(self, src: Path, content_type: str) -> None:
            try:
                body = src.read_bytes()
            except OSError as exc:
                self.send_error(500, f"could not read {src}: {exc}")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # noqa: D401 - matches stdlib API
            sys.stderr.write("[preview] %s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview a deep-wiki output directory in your browser."
    )
    parser.add_argument(
        "output_dir",
        help="Path to a deep-wiki output directory (must contain wiki.mdx).",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Preferred port (falls back to a free ephemeral port). Default 8765.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default 127.0.0.1.")
    parser.add_argument(
        "--no-open", action="store_true",
        help="Do not auto-open the browser.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    wiki = output_dir / "wiki.mdx"
    if not wiki.is_file():
        print(f"error: {wiki} not found", file=sys.stderr)
        return 1
    if not INDEX_HTML.is_file():
        print(f"error: viewer template missing at {INDEX_HTML}", file=sys.stderr)
        return 1

    handler = make_handler(output_dir)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.ThreadingTCPServer((args.host, args.port), handler)
    except OSError:
        print(
            f"[preview] port {args.port} unavailable, falling back to a free port",
            file=sys.stderr,
        )
        httpd = socketserver.ThreadingTCPServer((args.host, 0), handler)

    bound_host, bound_port = httpd.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print(f"[preview] serving {output_dir} at {url}")
    print("[preview] press Ctrl+C to stop")

    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[preview] shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
