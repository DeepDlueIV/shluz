import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const scriptFile = new URL('./home-reload.ts', import.meta.url);
// Отсутствие политики соответствует старой странице: браузер восстанавливает позицию.
const policy = existsSync(scriptFile)
  ? readFileSync(scriptFile, 'utf8').match(/String\.raw`([\s\S]*)`;/)?.[1] ?? ''
  : '';

function browser({ type = 'reload', pathname = '/', hash = '#features', search = '?source=test', restoration = 'auto' } = {}) {
  const listeners = new Map<string, Set<() => void>>();
  const frames: (() => void)[] = [];
  const location = { pathname, hash, search };
  const state = { scroll: 2400, marker: 'retain-next-router-state' };
  const history = {
    state, scrollRestoration: restoration,
    replaceState(next: unknown, _title: string, url: string) {
      assert.equal(next, state); assert.equal(url, pathname + search); location.hash = '';
    },
  };
  const scrolls: unknown[] = [];
  const window = {
    scrollY: 2400,
    scrollTo(options: { top: number; left: number; behavior: string }) { scrolls.push(options); this.scrollY = options.top; },
    addEventListener(event: string, handler: () => void) { if (!listeners.has(event)) listeners.set(event, new Set()); listeners.get(event)!.add(handler); },
    removeEventListener(event: string, handler: () => void) { listeners.get(event)?.delete(handler); },
    requestAnimationFrame(handler: () => void) { frames.push(handler); },
  };
  runInNewContext(policy, { window, history, location, performance: { getEntriesByType: () => [{ type }] } });
  return { window, history, location, scrolls, fire(event: string) { for (const fn of [...listeners.get(event) ?? []]) fn(); }, frame() { frames.splice(0).forEach(fn => fn()); } };
}

test('Homepage reload resets immediately, disables restoration and clears only the fragment', () => {
  const b = browser();
  assert.equal(b.window.scrollY, 0);
  assert.equal(b.history.scrollRestoration, 'manual');
  assert.equal(b.location.hash, '');
  assert.equal(b.location.search, '?source=test');
  assert.equal((b.scrolls[0] as { behavior: string }).behavior, 'instant');
});
test('After pageshow restoration stays manual until the first user interaction', () => {
  const b = browser(); b.window.scrollY = 800; b.fire('pageshow');
  assert.equal(b.window.scrollY, 0);
  assert.equal(b.history.scrollRestoration, 'manual');
  b.frame(); assert.equal(b.history.scrollRestoration, 'manual');
  b.fire('pointerdown'); assert.equal(b.history.scrollRestoration, 'auto');
});
test('Navigation, history return and the guest page are unaffected', () => {
  for (const options of [{ type: 'navigate' }, { type: 'back_forward' }, { pathname: '/app' }, { pathname: '/app/' }]) {
    const b = browser(options);
    assert.equal(b.window.scrollY, 2400); assert.equal(b.history.scrollRestoration, 'auto'); assert.equal(b.location.hash, '#features');
    assert.equal(b.scrolls.length, 0);
  }
});
test('A user scroll before load completes is not pulled back to the hero', () => {
  const b = browser();
  b.fire('wheel'); b.window.scrollY = 700; b.fire('pageshow'); b.frame();
  assert.equal(b.window.scrollY, 700); assert.equal(b.history.scrollRestoration, 'manual');
  b.fire('wheel'); assert.equal(b.history.scrollRestoration, 'auto');
});
test('A new anchor chosen before pageshow is not overwritten', () => {
  const b = browser(); b.location.hash = '#pricing'; b.window.scrollY = 3000;
  b.fire('pageshow'); b.frame();
  assert.equal(b.window.scrollY, 3000); assert.equal(b.location.hash, '#pricing');
});
test('A pre-existing manual restoration setting is preserved', () => {
  const b = browser({ restoration: 'manual', hash: '' }); b.fire('pageshow'); b.frame();
  assert.equal(b.window.scrollY, 0); assert.equal(b.history.scrollRestoration, 'manual');
});
test('The reload policy is installed in head, before page hydration', () => {
  const layout = readFileSync(new URL('../app/layout.tsx', import.meta.url), 'utf8');
  assert.match(layout, /<head>[\s\S]*<script[^>]*id="noclip-home-reload"/);
  assert.match(layout, /__html:\s*homeReloadScript/);
});

test('Leaving the document or changing history releases the policy without resetting position', () => {
  for (const event of ['pagehide', 'popstate', 'hashchange']) {
    const b = browser(); b.fire('pageshow'); b.window.scrollY = 600;
    b.fire(event);
    assert.equal(b.history.scrollRestoration, 'auto'); assert.equal(b.window.scrollY, 600);
    b.fire('pageshow'); assert.equal(b.window.scrollY, 600);
  }
});

test('The homepage does not anchor the viewport to moving content during font layout', () => {
  const css = readFileSync(new URL('../components/HomePage.module.css', import.meta.url), 'utf8');
  const pageRule = css.match(/\.page\s*\{([^}]+)\}/)?.[1] ?? '';
  assert.match(pageRule, /overflow-anchor:\s*none/);
});
