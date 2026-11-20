"""How long something took, written for a person.

Every log this GUI writes ended its run with a bare count of seconds:

    # duration_seconds: 13.42
    Duration: 47283s

A number of seconds is exact and unreadable. 13.42 and 47283 are a quarter of
a minute and thirteen hours, and nothing on the line says which — a Step 2 run
that felt long read as "13.42" and a user could not tell whether that was the
run or a fraction of it (Ames HPC, 2026-11-20).

So each of those lines now carries the same number in days, hours, minutes and
seconds, and keeps the raw seconds beside it for anything that reads the log
as data.

Stdlib only; imported by backend/app/jobs.py and the Step 1 batch script.
"""

from __future__ import annotations


def human_duration(seconds) -> str:
    """13.42 -> "13.4 s"; 3723 -> "1 h 02 min 03 s"; 97200 -> "1 d 3 h 00 min".

    The largest unit that applies leads, and smaller ones are zero-padded so a
    column of these lines stays readable. Seconds are dropped once the whole
    thing is more than a day, where they are noise.
    """
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if total < 0:
        total = 0.0
    if total < 60:
        # A short run keeps its tenth: "0.4 s" is a real answer, "0 s" is not.
        return f"{total:.1f} s"
    whole = int(round(total))
    d, rem = divmod(whole, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} d {h} h {m:02d} min"
    if h:
        return f"{h} h {m:02d} min {s:02d} s"
    return f"{m} min {s:02d} s"


# The same, as a POSIX shell function, for the scripts the GUI writes and runs
# (the Step 1 batch). Defining it there rather than calling back into python
# keeps the script runnable on its own, which is what it is for.
SHELL_HUMAN_DURATION = (
    'human_duration() {',
    '  _t=${1:-0}',
    '  if [ "$_t" -lt 60 ]; then echo "${_t} s"; return; fi',
    '  _d=$((_t/86400)); _h=$((_t%86400/3600)); _m=$((_t%3600/60)); _s=$((_t%60))',
    '  if [ "$_d" -gt 0 ]; then printf "%d d %d h %02d min\\n" "$_d" "$_h" "$_m"; return; fi',
    '  if [ "$_h" -gt 0 ]; then printf "%d h %02d min %02d s\\n" "$_h" "$_m" "$_s"; return; fi',
    '  printf "%d min %02d s\\n" "$_m" "$_s"',
    '}',
)
