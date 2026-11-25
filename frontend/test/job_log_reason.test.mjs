// Unit test for the line a failed job's status quotes from its log.
// Run: node test/job_log_reason.test.mjs
import assert from "node:assert/strict";
import { failureLine, noteLine } from "../src/jobLogReason.js";

// The job manager wraps a job's output in its own stamps; the reason is the
// script's last line, not "# duration".
const log = [
  "# started_at_utc: 2026-11-24T15:02:11+00:00",
  "$ bash /refs/NC_003197.2/download_ref.sh",
  "",
  "NCBI requires a contact email address for E-utilities requests",
  "",
  "# finished_at_utc: 2026-11-24T15:02:11+00:00",
  "# duration: 0 seconds  (0.10 seconds)",
  "",
].join("\n");
assert.equal(failureLine(log), "NCBI requires a contact email address for E-utilities requests");

// A traceback ends on the exception, which is the line worth showing.
assert.equal(
  failureLine("Traceback (most recent call last):\n  File \"x.py\", line 1\nurllib.error.HTTPError: HTTP Error 400: Bad Request\n"),
  "urllib.error.HTTPError: HTTP Error 400: Bad Request",
);

// Nothing but stamps, or nothing at all: no reason to quote.
assert.equal(failureLine("# started_at_utc: x\n$ cmd\n\n# finished_at_utc: y\n"), "");
assert.equal(failureLine(""), "");
assert.equal(failureLine(undefined), "");

// One very long line does not flood the status note.
assert.equal(failureLine("x".repeat(1000)).length, 301);

// A job that succeeded with a caveat prints it as a NOTE line; the status
// quotes it without the prefix. No NOTE, nothing to add.
assert.equal(
  noteLine("Downloading X gbk\nNOTE: X was downloaded without its GFF annotation.\n\n# finished_at_utc: y\n"),
  "X was downloaded without its GFF annotation.",
);
assert.equal(noteLine(log), "");
assert.equal(noteLine(undefined), "");

console.log("job log reason: ok");
