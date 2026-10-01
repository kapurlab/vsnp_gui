"""Same-origin guard for browser-initiated state changes, and response compression."""
import inspect

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware import gzip as _gzip_mod
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse

# Content types compression must NEVER touch, beyond starlette's own list
# (which keeps text/event-stream — the Step 1 SSE log stream — unbuffered):
#
#   * application/octet-stream — the IGV reads. The samtools window a
#     reads_data request answers with is BAM, already compressed, so gzip
#     saves nothing and doubled the time of every window the viewer asked for
#     (a 9.5 MB window: 149 ms -> 291 ms) while holding the GIL; the same goes
#     for the whole-file BAI / tabix fetches of the /serve path. (Byte-range
#     206 answers starlette never compresses on its own.)
#   * gzip / zip / pdf / xlsx — binary downloads: the same zero-gain CPU burn,
#     and losing Content-Length loses the browser's download progress bar.
#   * images.
#
# Starlette matches a response's type against this list exactly, or as
# "type/*", never by prefix, so each entry is a whole media type.
_EXTRA_UNCOMPRESSED = (
    "application/octet-stream",
    "application/gzip",
    "application/x-gzip",
    "application/zip",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/*",
)
_DEFAULT_UNCOMPRESSED = tuple(getattr(_gzip_mod, "DEFAULT_EXCLUDED_CONTENT_TYPES", ()))


def gzip_kwargs() -> dict:
    """GZipMiddleware's arguments, with the exclusions above in the form the
    installed starlette takes them.

    Starlette 1.x takes the list as the ``exclude_content_types`` constructor
    argument. Extending the module constant in place — the earlier way —
    stopped working once that constant became the argument's default, which
    is bound when the class is defined; the reads windows were gzipped for
    nothing until this was noticed. A starlette with neither still gets the
    module tuple and compresses more, never crashes.

    Level 6, not the default 9: on repetitive JSON the size difference is a
    few percent, the throughput difference is severalfold, and the work runs
    in the request path.
    """
    kwargs = {"minimum_size": 1024, "compresslevel": 6}
    excluded = _DEFAULT_UNCOMPRESSED + tuple(
        t for t in _EXTRA_UNCOMPRESSED if t not in _DEFAULT_UNCOMPRESSED)
    try:
        params = inspect.signature(GZipMiddleware.__init__).parameters
    except (TypeError, ValueError):
        params = {}
    if "exclude_content_types" in params:
        kwargs["exclude_content_types"] = excluded
    elif hasattr(_gzip_mod, "DEFAULT_EXCLUDED_CONTENT_TYPES"):
        _gzip_mod.DEFAULT_EXCLUDED_CONTENT_TYPES = excluded
    return kwargs


def install_request_safety(app):
    # Response compression. The GUI's biggest payloads are extremely
    # key-repetitive — the 8,179-sample QC summary is ~14.5 MB of JSON that
    # gzips to well under 1 MB, and SNP-table HTML is similar — and every byte
    # crosses the OnDemand proxy, whose ~60 s read timeout is the wall the
    # biggest panes kept hitting.
    app.add_middleware(GZipMiddleware, **gzip_kwargs())
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
