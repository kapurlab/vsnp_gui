// How long a wait has been waiting, rendered for humans.
//
// On an HPC Projects root a scan or a stats load can sit there for minutes,
// and a spinner with no number gives no way to tell "slow" from "hung" — so
// every busy indicator in this GUI carries one of these. See <Elapsed/>.
//
// Kept as plain JS (no JSX) so the formatting can be unit-tested with node.
// Named elapsedFormat, not elapsed, so it can never shadow Elapsed.jsx on a
// case-insensitive macOS filesystem — a resolution that would then differ on
// the Linux HPC this ships to.

/**
 * Seconds -> "9s" / "1m 05s" / "2h 03m 07s".
 *
 * Minutes and seconds are zero-padded once a larger unit is present so the
 * string keeps a steady width and doesn't jitter as it ticks. Negative input
 * (a server clock ahead of the browser's) reads as 0s rather than "-3s".
 */
export function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const pad = (n) => String(n).padStart(2, "0");
  if (h > 0) return `${h}h ${pad(m)}m ${pad(s)}s`;
  if (m > 0) return `${m}m ${pad(s)}s`;
  return `${s}s`;
}
