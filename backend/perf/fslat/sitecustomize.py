"""Shared-storage latency model.

Every filesystem call whose path lies under FSLAT_ROOT costs FSLAT_MS
milliseconds and is counted. Rides PYTHONPATH, so the backend AND every
subprocess it starts (qc_scan.py) are modelled alike.

  FSLAT_ROOT       charge calls under this prefix (required to activate)
  FSLAT_MS         latency per call (default 0: count only)
  FSLAT_COUNT_DIR  each process writes <dir>/<pid>.count (updated ~4x/s
                   and at exit); the driver sums them.
"""
import atexit
import builtins
import io
import os
import threading
import time

_ROOT = os.environ.get("FSLAT_ROOT", "")
_MS = float(os.environ.get("FSLAT_MS", "0") or 0)
_COUNT_DIR = os.environ.get("FSLAT_COUNT_DIR", "")

_n = 0
_lock = threading.Lock()
_sleep = time.sleep


def count() -> int:
    return _n


if _ROOT:
    def _under(p) -> bool:
        if p is None or isinstance(p, int):
            return False
        try:
            s = os.fspath(p)
        except TypeError:
            return False
        if isinstance(s, bytes):
            try:
                s = s.decode()
            except Exception:
                return False
        return s.startswith(_ROOT)

    def _charge(p) -> None:
        global _n
        if _under(p):
            with _lock:
                _n += 1
            if _MS:
                _sleep(_MS / 1000.0)

    def _wrap(mod, name, argpos=0, kw=("path", "file")):
        real = getattr(mod, name)

        def w(*a, **k):
            p = a[argpos] if len(a) > argpos else next((k[x] for x in kw if x in k), None)
            _charge(p)
            return real(*a, **k)
        w.__name__ = name
        w.__wrapped__ = real
        setattr(mod, name, w)
        return real

    for _name in ("stat", "lstat", "listdir", "open", "mkdir", "rmdir", "unlink",
                  "remove", "rename", "replace", "chmod", "utime", "readlink", "access"):
        _wrap(os, _name)
    _wrap(os, "symlink", argpos=1, kw=("dst",))
    _wrap(builtins, "open")
    io.open = builtins.open

    _real_scandir = os.scandir

    class _Entry:
        __slots__ = ("_e",)

        def __init__(self, e):
            self._e = e

        @property
        def name(self):
            return self._e.name

        @property
        def path(self):
            return self._e.path

        def stat(self, *, follow_symlinks=True):
            _charge(self._e.path)
            return self._e.stat(follow_symlinks=follow_symlinks)

        def is_dir(self, *, follow_symlinks=True):
            if follow_symlinks and self._e.is_symlink():
                _charge(self._e.path)
            return self._e.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, *, follow_symlinks=True):
            if follow_symlinks and self._e.is_symlink():
                _charge(self._e.path)
            return self._e.is_file(follow_symlinks=follow_symlinks)

        def is_symlink(self):
            return self._e.is_symlink()

        def is_junction(self):
            return False

        def inode(self):
            return self._e.inode()

        def __fspath__(self):
            return self._e.path

        def __repr__(self):
            return f"<DirEntry {self._e.name!r}>"

    class _Scandir:
        __slots__ = ("_it",)

        def __init__(self, it):
            self._it = it

        def __iter__(self):
            return self

        def __next__(self):
            return _Entry(next(self._it))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._it.close()
            return False

        def close(self):
            self._it.close()

    def scandir(path=".", *a, **k):
        _charge(path)
        it = _real_scandir(path, *a, **k)
        return _Scandir(it) if _under(path) else it
    scandir.__wrapped__ = _real_scandir
    os.scandir = scandir

    # glob (and pathlib through it) captured os.lstat / os.scandir at import.
    try:
        import glob as _glob
        _glob._StringGlobber.lstat = staticmethod(os.lstat)
        _glob._StringGlobber.scandir = staticmethod(os.scandir)
    except Exception:
        pass

    if _COUNT_DIR:
        os.makedirs(_COUNT_DIR, exist_ok=True)
        _path = os.path.join(_COUNT_DIR, f"{os.getpid()}.count")
        _real_open = builtins.open.__wrapped__

        def _flush():
            try:
                with _real_open(_path, "w") as fh:
                    fh.write(str(_n))
            except OSError:
                pass

        def _ticker():
            last = -1
            while True:
                time.sleep(0.25)
                if _n != last:
                    last = _n
                    _flush()

        threading.Thread(target=_ticker, daemon=True, name="fslat-count").start()
        atexit.register(_flush)
