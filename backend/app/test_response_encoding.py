#!/usr/bin/env python3
"""Compression reaches the responses it should and skips the ones it must not.

request_safety.install_request_safety adds starlette's GZipMiddleware with an
exclusion list. Until v0.4.125 that list was applied by extending a module
constant that the middleware had already bound as a default argument, so it
never took effect: the samtools reads windows IGV asks for (application/
octet-stream, already-compressed BAM) were gzipped for nothing and took twice
as long. This drives a small app through a real uvicorn, the only way the
middleware's behaviour is observable, and checks each kind of body.

Run: cd backend/app && ../../env/bin/python test_response_encoding.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from fastapi import FastAPI, Response

from app import request_safety

FAILURES = []


def check(cond, label):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def main() -> int:
    app = FastAPI()
    request_safety.install_request_safety(app)
    blob = os.urandom(200_000)
    big_json = {"rows": [{"sample": f"s{i}", "status": "complete", "n": i} for i in range(3000)]}

    @app.get("/reads")
    def reads():
        return Response(content=blob, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})

    @app.get("/xlsx")
    def xlsx():
        return Response(content=blob,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/json")
    def json_body():
        return big_json

    @app.get("/text")
    def text():
        return Response(content=("ACGT" * 50_000), media_type="text/plain")

    @app.get("/small")
    def small():
        return {"ok": True}

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    check(server.started, "the test server came up")
    try:
        def get(path):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.headers.get("Content-Encoding"), r.read(), r.headers.get("Content-Length")

        enc, body, length = get("/reads")
        check(enc is None, f"a reads window (application/octet-stream) is not compressed (Content-Encoding {enc!r})")
        check(body == blob and length == str(len(blob)), "and arrives whole, with its Content-Length")
        enc, body, length = get("/xlsx")
        check(enc is None, f"an xlsx download is not compressed (Content-Encoding {enc!r})")
        enc, body, _ = get("/json")
        check(enc == "gzip", f"a large JSON body is compressed (Content-Encoding {enc!r})")
        import gzip
        check(json.loads(gzip.decompress(body)) == big_json, "and decompresses to the same JSON")
        enc, body, _ = get("/text")
        check(enc == "gzip", f"a large text body (a VCF through /serve) is compressed (Content-Encoding {enc!r})")
        enc, body, _ = get("/small")
        check(enc is None, "a body under the minimum size is left alone")
    finally:
        server.should_exit = True
        time.sleep(0.3)
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
