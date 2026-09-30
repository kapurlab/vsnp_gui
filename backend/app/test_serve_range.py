"""Byte ranges from the serve endpoint: what igv.js asks for, answered as HTTP
says (RFC 7233), including a range that runs past the end of the file.

Run directly:  python test_serve_range.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as app_main

FAILURES = []


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok   {label}")


class _Request:
    def __init__(self, range_header=None):
        self.headers = {"range": range_header} if range_header else {}


def body_of(resp) -> bytes:
    async def drain():
        chunks = []
        async for c in resp.body_iterator:
            chunks.append(c)
        return b"".join(chunks)
    return asyncio.run(drain())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="serve_range_"))
    try:
        target = tmp / "reads.bam"
        data = bytes(range(256)) * 4   # 1024 bytes, byte i == i % 256
        target.write_bytes(data)

        print("no Range header: the whole file")
        r = app_main._range_response(target, _Request(), "application/octet-stream")
        check(r.status_code, 200, "status")
        check(r.headers["content-length"], "1024", "content-length")
        check(body_of(r), data, "body")

        print("a range inside the file")
        r = app_main._range_response(target, _Request("bytes=10-20"), "application/octet-stream")
        check(r.status_code, 206, "status")
        check(r.headers["content-range"], "bytes 10-20/1024", "content-range")
        check(body_of(r), data[10:21], "body")

        print("a range that runs past the end: the tail, not a refusal")
        r = app_main._range_response(target, _Request("bytes=1000-70000"), "application/octet-stream")
        check(r.status_code, 206, "status")
        check(r.headers["content-range"], "bytes 1000-1023/1024", "content-range clamps the end")
        check(r.headers["content-length"], "24", "content-length matches")
        check(body_of(r), data[1000:], "body is the tail")

        print("an open-ended range")
        r = app_main._range_response(target, _Request("bytes=1020-"), "application/octet-stream")
        check(r.status_code, 206, "status")
        check(body_of(r), data[1020:], "body")

        print("a range starting past the end is unsatisfiable")
        r = app_main._range_response(target, _Request("bytes=1024-1030"), "application/octet-stream")
        check(r.status_code, 416, "status")
        check(r.headers["content-range"], "bytes */1024", "content-range names the size")

        print("the file size the caller already has is trusted")
        r = app_main._range_response(target, _Request("bytes=0-9"), "application/octet-stream", file_size=1024)
        check(r.status_code, 206, "status")
        check(body_of(r), data[:10], "body")
    finally:
        for p in tmp.glob("*"):
            os.unlink(p)
        os.rmdir(tmp)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        return 1
    print("\nAll serve-range tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
