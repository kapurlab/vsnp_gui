"""Make many small filesystem calls at once.

On a network filesystem every stat, open and directory read is a round trip to
a server. Listing a project's samples, counting its reads or auditing its
comparison set costs one or more of those per sample — 8,000+ per request on
the Ames projects — and made one after another they add up to minutes, most of
it spent idle, waiting on the next reply. Made concurrently they overlap: the
server answers sixteen requests in flight in about the time it answers one.

``fan_out`` is the one place that decides how many run at once. Its pool is
shared by every request, so the total in flight stays bounded however many
requests arrive together (a project switch fires a dozen). A task running in
the pool never fans out again — a nested call runs its items in place instead
— so the pool can never deadlock waiting on itself.

VSNP_GUI_FS_WORKERS sets the width (default 16); 1 makes every call serial,
exactly as before.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def _width() -> int:
    try:
        return max(1, int(os.environ.get("VSNP_GUI_FS_WORKERS", "16")))
    except ValueError:
        return 16


WIDTH = _width()
_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()
_local = threading.local()


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=WIDTH, thread_name_prefix="fs-fanout")
        return _pool


def _run_part(fn: Callable[[T], R], part: List[T]) -> List[R]:
    _local.inside = True
    try:
        return [fn(x) for x in part]
    finally:
        _local.inside = False


def fan_out(fn: Callable[[T], R], items: Iterable[T]) -> List[R]:
    """``[fn(x) for x in items]``, with the calls made concurrently.

    Results keep the order of `items`. `fn` should handle its own OSError the
    way the serial loop it replaces did; anything it raises is re-raised here.
    Items are handed out in chunks, so thousands of tiny calls cost dozens of
    pool tasks rather than thousands.
    """
    items = list(items)
    if len(items) < 2 or WIDTH == 1 or getattr(_local, "inside", False):
        return [fn(x) for x in items]
    size = max(1, -(-len(items) // (WIDTH * 4)))
    parts = [items[i:i + size] for i in range(0, len(items), size)]
    out: List[R] = []
    for res in _get_pool().map(lambda part: _run_part(fn, part), parts):
        out.extend(res)
    return out
