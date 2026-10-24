import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { createContext, runInContext } from 'node:vm';
import { GatewayError } from './gateway.ts';

// Выполняем настоящий private loader отдельно от JSX: DOM и внешний API заменены только в тесте.
function harness() {
  const source = readFileSync(new URL('../components/GuestJourney.tsx', import.meta.url), 'utf8');
  const start = source.indexOf('type TurnstileAPI =');
  const end = source.indexOf('async function verifyBrowser');
  assert.ok(start >= 0 && end > start, 'Turnstile loader must be present');
  const api = {
    render() { return 'test-widget'; },
    remove() {},
    ready() { throw Error('Remove async/defer before using turnstile.ready()'); },
  };
  const timers = new Map<number, () => void>();
  let timerId = 0;
  const window: {
    turnstile?: typeof api;
    setTimeout(callback: () => void, delay: number): number;
    clearTimeout(id: number): void;
  } = {
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  };
  const scripts: {
    src: string; async: boolean; removed: boolean;
    onload(): void; onerror(): void; remove(): void;
  }[] = [];
  const document = {
    createElement(name: string) {
      assert.equal(name, 'script');
      return { src: '', async: false, removed: false, onload() {}, onerror() {},
        remove() { this.removed = true; } };
    },
    head: { appendChild(script: typeof scripts[number]) { scripts.push(script); } },
  };
  const context = createContext({ window, document, GatewayError });
  const load = runInContext(stripTypeScriptTypes(source.slice(start, end)) + '\nloadTurnstile;', context);
  return { load, window, document, api, scripts, timers };
}

test('async explicit script completes without calling incompatible ready()', async () => {
  const h = harness();
  const pending = h.load();
  assert.equal(h.load(), pending, 'concurrent requests share one script');
  assert.equal(h.scripts.length, 1);
  assert.equal(h.scripts[0].src, 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit');
  h.window.turnstile = h.api;
  assert.doesNotThrow(() => h.scripts[0].onload());
  assert.equal(await pending, h.api);
  assert.equal(h.timers.size, 0);
});

test('missing API fails closed and a later manual attempt can reload', async () => {
  const h = harness();
  const first = h.load();
  h.scripts[0].onload();
  await assert.rejects(first, (error: unknown) => error instanceof GatewayError && error.code === 'verification_unavailable');
  assert.equal(h.scripts[0].removed, true);
  const next = h.load();
  assert.equal(h.scripts.length, 2);
  h.scripts[1].onerror();
  await assert.rejects(next, GatewayError);
});

test('script timeout rejects and clears the failed cached load', async () => {
  const h = harness();
  const first = h.load();
  const timeout = [...h.timers.values()][0];
  timeout();
  await assert.rejects(first, GatewayError);
  assert.equal(h.scripts[0].removed, true);
  assert.equal(h.timers.size, 0);
  const next = h.load();
  assert.equal(h.scripts.length, 2);
  h.scripts[1].onerror();
  await assert.rejects(next, GatewayError);
});
