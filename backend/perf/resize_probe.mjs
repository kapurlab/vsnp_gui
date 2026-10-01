#!/usr/bin/env node
// Open a project in headless Chrome (which expands its samples in the Projects
// panel), resize the window through each step and back to 1400x900, and
// report whether the page survived.
//   node resize_probe.mjs <base-url> <project> [WxH[,WxH...]] [runs]
// e.g. node resize_probe.mjs http://127.0.0.1:8771 owl 1000x900 10
//
// v0.4.114 to v0.4.118 could blank the whole app when the window was resized
// with a project open: VirtualRows' read of the view after every render kept
// asking React for a new band while a resize's own update waited, and React
// gave up ("Maximum update depth exceeded", minified error #185). Whether it
// happens depends on timing, so this repeats; v0.4.118 blanked 8 of 8 runs at
// 1000x900, on the owl fixture and on mtbc0_test01. Exits 1 if any run blanked.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, project, stepsArg = "1000x900", runsArg = "5"] = process.argv.slice(2);
if (!base || !project) {
  console.error("usage: node resize_probe.mjs <base-url> <project> [WxH[,WxH...]] [runs]");
  process.exit(2);
}
const steps = stepsArg.split(",").map((s) => s.split("x").map(Number));
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function once() {
  const profile = mkdtempSync(join(tmpdir(), "resize_probe_"));
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1400,900",
    "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  try {
    const wsUrl = await new Promise((resolve, reject) => {
      let buf = "";
      chrome.stderr.on("data", (d) => {
        buf += d;
        const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
        if (m) resolve(m[1]);
      });
      setTimeout(() => reject(new Error("no devtools url")), 15000);
    });
    const targets = await (await fetch(wsUrl.replace(/^ws:\/\/([^/]+)\/.*$/, "http://$1") + "/json")).json();
    const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => (ws.onopen = r));
    let nextId = 1;
    const pending = new Map();
    const errors = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
      if (msg.method === "Runtime.exceptionThrown") {
        errors.push((msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text).split("\n")[0].slice(0, 160));
      }
    };
    const send = (method, params = {}) => new Promise((resolve) => {
      const id = nextId++; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params }));
    });
    const ev = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: base });
    // The project row by its title, without the ▸ in front of it.
    const ROW = `[...document.querySelectorAll('.list-item[role=button]')].find(e =>
      (e.querySelector('.list-title')?.textContent || '').replace(/^[▸▾ ]*/, '').startsWith(${JSON.stringify(project)}))`;
    for (let i = 0; i < 120 && !(await ev(`!!(${ROW})`)); i++) await sleep(250);
    if (!(await ev(`!!(${ROW})`))) throw new Error(`no project row for ${project}`);
    await ev(`(${ROW}).click()`);
    // Until its samples are drawn: a large project takes a while to list.
    for (let i = 0; i < 360 && !(await ev(`!!document.querySelector('.list .s2-vrows [data-vi]')`)); i++) await sleep(250);
    await sleep(1500);
    const before = await ev(`document.body.innerText.length`);
    for (const [width, height] of steps) {
      await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
      await sleep(700);
    }
    // And back to the window's own size, as letting go of a dragged edge does:
    // this second resize is often the one that set the loop off.
    await send("Emulation.clearDeviceMetricsOverride");
    await sleep(1500);
    const after = await ev(`document.body.innerText.length`);
    return { blanked: !after || errors.some((e) => /#185|Maximum update depth/.test(e)), before, after, errors };
  } finally {
    chrome.kill();
    await sleep(300);
    rmSync(profile, { recursive: true, force: true });
  }
}

const runs = Number(runsArg);
let blanked = 0;
for (let i = 1; i <= runs; i++) {
  const r = await once();
  if (r.blanked) blanked++;
  console.log(`run ${i}: ${r.blanked ? "BLANKED" : "ok"}  page text ${r.before} -> ${r.after}${r.errors.length ? `  ${r.errors[0]}` : ""}`);
}
console.log(`${blanked}/${runs} runs blanked the page (1400x900 -> ${stepsArg} -> 1400x900)`);
process.exit(blanked ? 1 : 0);
