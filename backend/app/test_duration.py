"""Durations in logs read as days, hours, minutes and seconds.

Every log this GUI writes used to end in a bare count of seconds, and a run
that took thirteen hours and one that took thirteen seconds looked alike at a
glance (Ames HPC, 2026-11-20: "# duration_seconds: 13.42" under a Step 2 that
had felt long). The shell half, used by the Step 1 batch script, has to agree
with the python half, and has to run under /bin/sh as well as bash.

Run directly:  python test_duration.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.duration import SHELL_HUMAN_DURATION, human_duration  # noqa: E402

CASES = [
    (0, "0.0 s"),
    (0.42, "0.4 s"),
    (13.42, "13.4 s"),
    (59.94, "59.9 s"),
    (60, "1 min 00 s"),
    (125, "2 min 05 s"),
    (599, "9 min 59 s"),
    (3600, "1 h 00 min 00 s"),
    (3723, "1 h 02 min 03 s"),
    (47283, "13 h 08 min 03 s"),
    (86399, "23 h 59 min 59 s"),
    (97200, "1 d 3 h 00 min"),
    (176461, "2 d 1 h 01 min"),
]


def check(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: expected {want!r}, got {got!r}")
    print(f"  OK  {what}")


def main() -> int:
    print("python")
    for seconds, want in CASES:
        check(human_duration(seconds), want, f"{seconds} s")
    check(human_duration(-5), "0.0 s", "a clock that ran backwards reads as none")
    check(human_duration(None), "unknown", "and a missing figure says so rather than raising")

    print("the shell half says the same, under sh and bash")
    body = "\n".join(SHELL_HUMAN_DURATION)
    whole = [(int(s), w) for s, w in CASES if float(s) >= 60]
    script = body + "\n" + "\n".join(f"human_duration {s}" for s, _w in whole)
    for shell in ("/bin/sh", "/bin/bash"):
        if not Path(shell).exists():
            print(f"  --  {shell} is not here, skipped")
            continue
        r = subprocess.run([shell, "-c", script], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(f"{shell}: exited {r.returncode}: {r.stderr.strip()}")
        check(r.stdout.strip().splitlines(), [w for _s, w in whole], shell)
    # Under a minute the shell keeps whole seconds (it has no float there);
    # that is the one deliberate difference.
    r = subprocess.run(["/bin/sh", "-c", body + "\nhuman_duration 13"], capture_output=True, text=True, timeout=60)
    check(r.stdout.strip(), "13 s", "a short run in the shell")
    print("test_duration: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
