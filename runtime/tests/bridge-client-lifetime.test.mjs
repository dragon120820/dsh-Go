// Runs the complete project browser entry. Cordis, the sidebar, and the browser
// clock are unit seams; this does not run upstream packages or render a page.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../bridge/dsh-go-bridge/lib/client.js", import.meta.url), "utf8");

function page(hash = "") {
  let plugin;
  let now = 0;
  let wallOffset = 0;
  let nextTimer = 0;
  let disposed = false;
  let mounted = false;
  let active;
  const timers = new Map();
  const disposers = [];
  const opens = [];
  const warnings = [];
  const types = [{ id: "fixture-tab", kind: "fixture-kind", guide: [] }];
  // These providers outlive the bridge consumer. Cordis get() checks the
  // provider's state, and SidebarRightController has no caller-lifetime gate.
  const services = {
    sidebarRightTabs: { entries: () => types },
    sidebarRight: {
      openTab(kind, options) {
        if (!mounted) throw Error("sidebarRight: no session surface is mounted");
        opens.push({ kind, replaceTab: options.replaceTab === true, disposed });
        active = { id: "fixture-open-tab", kind };
      },
      isExpanded: () => true,
      active: () => active,
    },
  };
  const window = { __ModuleLoader__: { load: ({ factory }) => { plugin = factory(); } } };
  runInNewContext(source, {
    window, location: { hash },
    console: { warn: (...args) => warnings.push(args) },
    Date: class extends Date { static now() { return now + wallOffset; } },
    performance: { now: () => now },
    setTimeout(fn, ms) { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  plugin.apply({
    get: (name) => services[name],
    on() {},
    effect(run) { const dispose = run(); disposers.push(dispose); return dispose; },
  });
  return {
    api: window.__dshGoBridge, opens, warnings,
    mount() { mounted = true; },
    adjustWall(delta) { wallOffset += delta; },
    dispose() {
      disposed = true;
      for (const dispose of disposers.splice(0).reverse()) dispose();
      assert.equal(window.__dshGoBridge, undefined);
    },
    async poll() {
      const pending = [...timers].find(([, timer]) => timer.ms === 200);
      assert.ok(pending, "the real client scheduled its next poll");
      timers.delete(pending[0]);
      now += pending[1].ms;
      pending[1].fn();
      await Promise.resolve();
      await Promise.resolve();
    },
    pendingPolls: () => [...timers.values()].filter((timer) => timer.ms === 200).length,
  };
}

test("a live bridge still opens a conversation that mounts during its wait", async () => {
  const p = page();
  try {
    const result = p.api.openTab("fixture-tab", { replaceTab: true });
    p.mount();
    await p.poll();
    assert.deepEqual({ ...await result }, { ok: true, id: "fixture-tab", kind: "fixture-kind", tabId: "fixture-open-tab" });
    assert.deepEqual(p.opens, [{ kind: "fixture-kind", replaceTab: true, disposed: false }]);
    assert.equal(p.pendingPolls(), 0);
  } finally { p.dispose(); }
});

test("unloading the bridge stops a waiting API call before a later mount", async () => {
  const p = page();
  const result = p.api.openTab("fixture-tab");
  p.dispose();
  p.mount();
  await p.poll();
  assert.deepEqual({ ...await result }, { ok: false, reason: "disposed" });
  assert.deepEqual(p.opens, []);
  assert.equal(p.pendingPolls(), 0);
  assert.deepEqual({ ...await p.api.openTab("fixture-tab") }, { ok: false, reason: "disposed" });
  assert.equal(p.pendingPolls(), 0);
});

test("a pending hash tab cannot open after its bridge has unloaded", async () => {
  const p = page("#dsh-go-tab=fixture-tab");
  p.dispose();
  p.mount();
  await p.poll();
  assert.deepEqual(p.opens, []);
  assert.deepEqual(p.warnings, []);
  assert.equal(p.pendingPolls(), 0);
});

test("a live bridge keeps the existing no-session timeout result", async () => {
  const p = page();
  try {
    const result = p.api.openTab("fixture-tab", { timeoutMs: 400 });
    await p.poll();
    await p.poll();
    await p.poll();
    assert.deepEqual({ ...await result }, { ok: false, reason: "no-session" });
    assert.deepEqual(p.opens, []);
    assert.equal(p.pendingPolls(), 0);
  } finally { p.dispose(); }
});


test("wall clock rollback cannot extend the wait to a later conversation", async () => {
  const p = page();
  try {
    let settled = false;
    const result = p.api.openTab("fixture-tab", { timeoutMs: 400 });
    void result.then(() => { settled = true; });
    await p.poll();
    p.adjustWall(-60000);
    await p.poll();
    await p.poll();
    assert.equal(settled, true, "elapsed timeout must finish even if Date moved backward");
    assert.deepEqual({ ...await result }, { ok: false, reason: "no-session" });
    p.mount();
    assert.deepEqual(p.opens, []);
    assert.equal(p.pendingPolls(), 0);
  } finally {
    p.dispose();
    if (p.pendingPolls()) await p.poll();
  }
});

test("wall clock forward adjustment does not end the wait before its duration", async () => {
  const p = page();
  try {
    let settled = false;
    const result = p.api.openTab("fixture-tab", { timeoutMs: 400 });
    void result.then(() => { settled = true; });
    p.adjustWall(60000);
    await p.poll();
    assert.equal(settled, false, "only 200 ms of the requested duration has elapsed");
    p.mount();
    await p.poll();
    assert.deepEqual({ ...await result }, { ok: true, id: "fixture-tab", kind: "fixture-kind", tabId: "fixture-open-tab" });
    assert.deepEqual(p.opens, [{ kind: "fixture-kind", replaceTab: false, disposed: false }]);
    assert.equal(p.pendingPolls(), 0);
  } finally {
    p.dispose();
    if (p.pendingPolls()) await p.poll();
  }
});
