import React, { useEffect, useRef, useState } from "react";
import { formatElapsed } from "./elapsedFormat.js";

/**
 * A counter that ticks for as long as it is mounted.
 *
 * Drop it inside whatever a busy indicator already renders — "Scanning
 * projects…", a disabled "Loading..." button, a running-job pill — and it
 * starts at 0s the moment that indicator appears and stops existing when it
 * goes away. No wiring, no extra state in the caller.
 *
 * `since` anchors it to a start the server knows about (epoch ms, or any ISO
 * string Date can parse) so a job that began before this page loaded still
 * reports its true age; it may arrive a poll or two late, and the reading
 * corrects itself when it does. Without it the count runs from mount.
 */
export default function Elapsed({ since, className, style, title }) {
  const mountedAt = useRef(Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const anchored = since ? new Date(since).getTime() : NaN;
  const start = Number.isFinite(anchored) ? anchored : mountedAt.current;
  return (
    <span
      className={className ? `elapsed ${className}` : "elapsed"}
      style={style}
      title={title || "How long this has been working"}
    >
      {formatElapsed((now - start) / 1000)}
    </span>
  );
}
