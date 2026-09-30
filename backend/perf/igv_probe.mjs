#!/usr/bin/env node
// Load the IGV page in headless Chrome and report what it fetches and where it ends up.
//   node igv_probe.mjs <url> <out.png> [timeout_s] [colorScheme]
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const [url, outPng, timeoutArg, scheme, kbps] = process.argv.slice(2);
const timeoutS = Number(timeoutArg || 60);
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1400,900",
  "--remote-debugging-port=0", "--user-data-dir=/tmp/igv_probe_profile_" + process.pid, "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });
const wsUrl = await new Promise((resolve, reject) => {
  let buf = "";
  chrome.stderr.on("data", (d) => {
    buf += d.toString();
    const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
    if (m) resolve(m[1]);
  });
  chrome.on("exit", (c) => reject(new Error("chrome exited " + c)));
  setTimeout(() => reject(new Error("no devtools url\n" + buf)), 15000);
});
const httpBase = wsUrl.replace(/^ws:\/\/([^/]+)\/.*$/, "http://$1");
const targets = await (await fetch(httpBase + "/json")).json();
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let nextId = 1;
const pending = new Map();
const requests = new Map();   // requestId -> record
const events = [];
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
  const p = msg.params || {};
  const t = performance.now();
  switch (msg.method) {
    case "Network.requestWillBeSent": {
      const u = new URL(p.request.url);
      const path = u.searchParams.get("path");
      requests.set(p.requestId, { name: path ? path.split("/").pop() : u.pathname + (u.search.slice(0, 60)), range: p.request.headers.Range || p.request.headers.range || "", t0: t, status: 0, bytes: 0, done: false });
      break;
    }
    case "Network.responseReceived": { const r = requests.get(p.requestId); if (r) { r.status = p.response.status; r.mime = p.response.mimeType; r.cl = p.response.headers["content-length"] || p.response.headers["Content-Length"] || ""; } break; }
    case "Network.dataReceived": { const r = requests.get(p.requestId); if (r) r.bytes += p.dataLength; break; }
    case "Network.loadingFinished": { const r = requests.get(p.requestId); if (r) { r.done = true; r.t1 = t; r.enc = p.encodedDataLength; } break; }
    case "Network.loadingFailed": { const r = requests.get(p.requestId); if (r) { r.done = true; r.t1 = t; r.error = p.errorText; } break; }
    case "Runtime.consoleAPICalled": if (p.type === "error" || p.type === "warning") events.push(`console.${p.type}: ` + p.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300)); break;
    case "Runtime.exceptionThrown": events.push("exception: " + (p.exceptionDetails.exception?.description || p.exceptionDetails.text).slice(0, 300)); break;
  }
};
const send = (method, params = {}) => new Promise((resolve) => { const id = nextId++; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

await send("Network.enable");
await send("Runtime.enable");
await send("Page.enable");
if (kbps) await send("Network.emulateNetworkConditions", { offline: false, latency: 100, downloadThroughput: Number(kbps) * 1024 / 8, uploadThroughput: 1e6 });
if (scheme) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
const t0 = performance.now();
await send("Page.navigate", { url });
let last = "";
let outcome = "timeout";
while (performance.now() - t0 < timeoutS * 1000) {
  await new Promise((r) => setTimeout(r, 500));
  const state = await ev(`(() => {
    const hdr = document.querySelector('#root > div > div');
    const status = hdr ? hdr.querySelector('.igv-page-status')?.textContent || '' : '';
    const transfers = hdr ? hdr.querySelector('.igv-page-transfers')?.textContent || '' : '';
    const sr = document.querySelector('.igv-page-body')?.shadowRoot;
    const q = (sel) => sr ? [...sr.querySelectorAll(sel)] : [];
    const spinners = q('.igv-loading-spinner-container').filter(e => getComputedStyle(e).display !== 'none').length;
    const labels = q('.igv-track-label').map(e => e.textContent);
    const alerts = q('.igv-ui-alert-dialog-container, .igv-ui-dialog').filter(e => getComputedStyle(e).display !== 'none').map(e => e.textContent.trim().slice(0, 200));
    const msgs = q('.igv-viewport-message').filter(e => getComputedStyle(e).display !== 'none').map(e => e.textContent.trim().slice(0, 120));
    const nav = q('.igv-navbar')[0];
    return JSON.stringify({ status, transfers, spinners, labels, alerts, msgs, theme: document.documentElement.dataset.theme, hostTheme: document.querySelector('.igv-page-body')?.dataset.theme, navBg: nav ? getComputedStyle(nav).backgroundColor : '' });
  })()`);
  if (state !== last) { console.log(`${((performance.now() - t0) / 1000).toFixed(1)}s  ${state}`); last = state; }
  const s = JSON.parse(state);
  const inflight = [...requests.values()].filter((r) => !r.done).length;
  if (s.labels.length && s.spinners === 0 && inflight === 0 && !/Loading|…/.test(s.status)) { outcome = "settled"; break; }
  if (s.alerts.length || /failed/.test(s.status)) { outcome = "error"; break; }
}
console.log(`\noutcome: ${outcome} after ${((performance.now() - t0) / 1000).toFixed(1)} s`);
console.log("requests:");
for (const r of requests.values()) {
  const dur = r.done ? ((r.t1 - r.t0) / 1000).toFixed(2) + " s" : "PENDING " + ((performance.now() - r.t0) / 1000).toFixed(1) + " s";
  console.log(`  ${String(r.status).padEnd(4)} ${r.name.padEnd(46)} ${(r.range || "").padEnd(28)} ${String(r.bytes).padStart(10)} B  ${dur}${r.error ? "  " + r.error : ""}`);
}
for (const e of events) console.log("  " + e);
const shot = await send("Page.captureScreenshot", { format: "png" });
writeFileSync(outPng, Buffer.from(shot.result.data, "base64"));
console.log("screenshot:", outPng);
ws.close();
chrome.kill();
process.exit(outcome === "settled" ? 0 : 2);
