import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { homepageReloadScrollScript } from "./reload-scroll.ts";

function run({ path = "/", type = "reload", hash = "", search = "", pendingFonts = false } = {}) {
  const listeners = new Map<string, () => void>();
  const frames: (() => void)[] = [];
  const fontCallbacks: (() => void)[] = [];
  const scrolls: { top: number; behavior: string }[] = [];
  const state = { __NA: true, tree: ["keep-router-state"] };
  const replacements: unknown[][] = [];
  const history = { scrollRestoration: "auto", state,
    replaceState(...args: unknown[]) { replacements.push(args); } };
  const env = { document: pendingFonts ? { fonts: { ready: { then: (handler: () => void) => fontCallbacks.push(handler) } } } : {}, location: { pathname: path, hash, search }, history,
    performance: { getEntriesByType: () => type ? [{ type }] : [] },
    scrollTo: (options: { top: number; behavior: string }) => scrolls.push(options),
    addEventListener: (name: string, handler: () => void) => listeners.set(name, handler),
    removeEventListener: (name: string) => listeners.delete(name),
    setTimeout: () => 0,
    requestAnimationFrame: (handler: () => void) => frames.push(handler) };
  runInNewContext(homepageReloadScrollScript, { ...env, window: env });
  return { history, state, replacements, scrolls, listeners, frames, fontCallbacks };
}

test("Homepage reload disables restoration before resetting scroll instantly", () => {
  const result = run();
  assert.equal(result.history.scrollRestoration, "manual");
  assert.equal(result.scrolls.length, 1);
  assert.equal(result.scrolls[0].top, 0);
  assert.equal(result.scrolls[0].behavior, "instant");
});
test("Reload drops only the fragment, preserving query and Next router state", () => {
  const result = run({ hash: "#features", search: "?source=demo" });
  assert.equal(result.replacements.length, 1);
  assert.equal(result.replacements[0][0], result.state);
  assert.equal(result.replacements[0][2], "/?source=demo");
});
test("Restoration resumes only after the first two rendered frames", () => {
  const result = run();
  result.listeners.get("pageshow")!();
  assert.equal(result.history.scrollRestoration, "manual");
  assert.equal(result.scrolls.length, 2);
  result.frames.shift()!();
  assert.equal(result.history.scrollRestoration, "manual");
  result.frames.shift()!();
  assert.equal(result.history.scrollRestoration, "auto");
  assert.equal(result.scrolls.length, 2);
});
test("Ordinary navigation, back/forward and absent timing leave anchors alone", () => {
  for (const type of ["navigate", "back_forward", ""]) {
    const result = run({ type, hash: "#pricing" });
    assert.equal(result.history.scrollRestoration, "auto");
    assert.equal(result.scrolls.length, 0);
    assert.equal(result.replacements.length, 0);
    assert.equal(result.listeners.size, 0);
  }
});
test("Reload policy is scoped to the homepage, not the guest route", () => {
  for (const path of ["/app", "/app/"]) {
    const result = run({ path, hash: "#chat" });
    assert.equal(result.history.scrollRestoration, "auto");
    assert.equal(result.scrolls.length, 0);
    assert.equal(result.replacements.length, 0);
  }
});

test("Font completion precedes the final reset and restoration", () => {
  const result = run({ pendingFonts: true });
  result.listeners.get("pageshow")!();
  assert.equal(result.scrolls.length, 1);
  assert.equal(result.history.scrollRestoration, "manual");
  assert.equal(result.frames.length, 0);
  result.fontCallbacks[0]();
  assert.equal(result.scrolls.length, 2);
  while (result.frames.length) result.frames.shift()!();
  assert.equal(result.history.scrollRestoration, "auto");
  assert.equal(result.listeners.has("wheel"), false);
});
test("Late font completion never resets a user interaction", () => {
  const result = run({ pendingFonts: true });
  result.listeners.get("pageshow")!();
  result.listeners.get("wheel")!();
  result.fontCallbacks[0]();
  while (result.frames.length) result.frames.shift()!();
  assert.equal(result.scrolls.length, 1);
  assert.equal(result.history.scrollRestoration, "auto");
});
