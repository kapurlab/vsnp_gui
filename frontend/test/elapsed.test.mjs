// Unit test for the elapsed-time formatter behind every busy indicator.
// Run: node test/elapsed.test.mjs
import assert from "node:assert/strict";
import { formatElapsed } from "../src/elapsedFormat.js";

assert.equal(formatElapsed(0), "0s");
assert.equal(formatElapsed(9), "9s");
assert.equal(formatElapsed(59), "59s");
assert.equal(formatElapsed(60), "1m 00s");
assert.equal(formatElapsed(65), "1m 05s");
assert.equal(formatElapsed(3599), "59m 59s");
assert.equal(formatElapsed(3600), "1h 00m 00s");
assert.equal(formatElapsed(7387), "2h 03m 07s");

// Sub-second fractions floor, so the counter never reads ahead of the clock.
assert.equal(formatElapsed(0.9), "0s");
assert.equal(formatElapsed(59.99), "59s");

// A server clock ahead of the browser's, or a garbled start time, must not
// render "-4s" or "NaNs" next to a spinner.
assert.equal(formatElapsed(-4), "0s");
assert.equal(formatElapsed(NaN), "0s");
assert.equal(formatElapsed(undefined), "0s");

// Width stays steady as the number ticks (zero-padding below the top unit),
// which is what keeps a button from twitching once a second.
assert.equal(formatElapsed(601).length, formatElapsed(609).length);
assert.equal(formatElapsed(3601).length, formatElapsed(3671).length);

console.log("elapsed: ok");
