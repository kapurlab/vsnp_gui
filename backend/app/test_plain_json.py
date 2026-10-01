#!/usr/bin/env python3
"""_plain_json renders the per-sample lists byte for byte as FastAPI did.

The project-switch endpoints return their lists through main._plain_json so
FastAPI's jsonable_encoder — a pure-Python walk of every value — is skipped.
The body must be exactly what the encoder path produced for plain JSON data,
the fallback must still handle what only the encoder understands (a Path), and
the one documented difference (keys starting with "_sa", which the encoder
silently drops) must be the only one.

Run: cd backend/app && ../../env/bin/python test_plain_json.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app import main as m

FAILURES = []


def check(cond, label):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def fastapi_body(data) -> bytes:
    """What FastAPI renders for a dict returned from a plain endpoint."""
    return JSONResponse(content=jsonable_encoder(data)).body


def main() -> int:
    plain = {
        "job_status": "unknown", "job_id": "", "count": 3,
        "samples": [
            {"sample": "s1", "status": "complete", "has_log": True, "reason": "", "in_vcfs_folder": False, "n": None},
            {"sample": "ünïcödé", "status": "running", "ratio": 0.5, "nested": {"a": [1, 2, {"b": "c"}]}},
            {"sample": "s3", "list": [], "dict": {}, "big": 12345678901234567890, "neg": -1.5e-7},
        ],
    }
    resp = m._plain_json(plain)
    check(isinstance(resp, JSONResponse), "a JSONResponse comes back")
    check(resp.body == fastapi_body(plain), "plain data renders byte for byte as FastAPI's encoder path")
    check(resp.media_type == "application/json", "with the JSON media type")

    as_list = plain["samples"]
    check(m._plain_json(as_list).body == fastapi_body(as_list), "a bare list renders the same too")

    with_path = {"path": Path("/tmp/x"), "rows": [{"p": Path("a/b")}]}
    check(m._plain_json(with_path).body == fastapi_body(with_path),
          "a Path (not JSON) falls back to the encoder and renders as it always did")

    with_sa = {"rows": [{"sample": "s1", "_sample": "s1", "_file": "f"}]}
    body = m._plain_json(with_sa).body
    check(b"_sample" in body and b"_sample" not in fastapi_body(with_sa),
          "the documented difference: a _sa* key survives here where the encoder dropped it")

    try:
        m._plain_json({"x": math.nan})
        check(False, "NaN raises (allow_nan is False on both paths)")
    except ValueError:
        check(True, "NaN raises ValueError, as FastAPI's own path does")

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
