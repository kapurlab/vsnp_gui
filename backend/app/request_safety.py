"""Same-origin guard for browser-initiated state changes."""
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware import gzip as _gzip_mod
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse

# Content types compression must NEVER touch, beyond starlette's own
# text/event-stream (which keeps the Step 1 SSE log stream unbuffered):
#
#   * application/octet-stream + application/gzip — the IGV byte-range path.
#     igv.js streams multi-MB BAM/CRAM/tabix slices as 206es; gzip on already-
#     compressed binary saves ~nothing, costs tens of ms per chunk ON THE
#     EVENT LOOP (compression happens in the ASGI send path, and this deploys
#     as single-process uvicorn), and strips Content-Length from the response.
#   * xlsx / zip / pdf / images — binary downloads: same zero-gain CPU burn,
#     and losing Content-Length loses the browser's download progress bar.
#
# The deployed starlette (1.3.1) exposes no constructor parameter for this —
# exclusion is the module tuple below, checked with str.startswith — so the
# tuple is extended in place. Guarded so a future starlette that renames the
# constant degrades to compressing more, never to crashing.
_EXTRA_UNCOMPRESSED = (
    "application/octet-stream",
    "application/gzip",
    "application/x-gzip",
    "application/zip",
    "application/pdf",
    "application/vnd.openxmlformats",   # prefix: covers the xlsx MIME
    "image/",
)
if hasattr(_gzip_mod, "DEFAULT_EXCLUDED_CONTENT_TYPES"):
    _gzip_mod.DEFAULT_EXCLUDED_CONTENT_TYPES = tuple(
        _gzip_mod.DEFAULT_EXCLUDED_CONTENT_TYPES
    ) + _EXTRA_UNCOMPRESSED


def install_request_safety(app):
    # Response compression. The GUI's biggest payloads are extremely
    # key-repetitive — the 8,179-sample QC summary is ~14.5 MB of JSON that
    # gzips to well under 1 MB, and SNP-table HTML is similar — and every byte
    # crosses the OnDemand proxy, whose ~60 s read timeout is the wall the
    # biggest panes kept hitting. Level 6, not the default 9: on repetitive
    # JSON the size difference is a few percent, the throughput difference is
    # severalfold, and the work runs on the event loop.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.middleware("http")
    async def reject_cross_site_mutations(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("sec-fetch-site", "").lower() == "cross-site"
        ):
            return PlainTextResponse("forbidden (cross-site request)", status_code=403)
        return await call_next(request)
