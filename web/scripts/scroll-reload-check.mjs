import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
const browser = await chromium.launch();
const results = [];
const traces = [];
await mkdir('test-results', { recursive: true });
async function check(name, run) {
  try { await run(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: String(error) }); }
}
async function atHero(page) {
  await page.waitForSelector('[data-ready="true"]');
  await page.waitForTimeout(200);
  assert.ok(await page.evaluate(() => Math.abs(scrollY) <= 1), 'Reload restored a scrolled position');
  assert.equal(await page.locator('[data-prompt-mode="hero"]').count(), 1);
  const heading = await page.locator('#hero-title').boundingBox();
  const input = await page.locator('#hero-prompt').boundingBox();
  assert.ok(heading && heading.y >= 0 && heading.y < page.viewportSize().height);
  assert.ok(input && input.y >= 0 && input.y + input.height <= page.viewportSize().height);
}

try {
  for (const mobile of [false, true]) {
    const label = mobile ? 'Mobile' : 'Desktop';
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
      isMobile: mobile, hasTouch: mobile, reducedMotion: 'no-preference',
    });
    await context.addInitScript(() => {
      window.__scrollTrace = [];
      const record = (event, detail) => window.__scrollTrace.push({ event, detail, t: performance.now(), y: scrollY, hash: location.hash, restoration: history.scrollRestoration, type: performance.getEntriesByType('navigation')[0]?.type });
      const original = window.scrollTo.bind(window);
      window.scrollTo = (...args) => { record('scrollTo', args); return original(...args); };
      for (const event of ['DOMContentLoaded', 'load', 'pageshow', 'scroll', 'wheel', 'pointerdown']) window.addEventListener(event, () => record(event), { passive: true });
      const descriptor = Object.getOwnPropertyDescriptor(History.prototype, 'scrollRestoration');
      if (descriptor?.set && descriptor.get) Object.defineProperty(history, 'scrollRestoration', {
        get() { return descriptor.get.call(history); },
        set(value) { record('restoration-set', value); descriptor.set.call(history, value); },
      });
      record('init');
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await check(`${label}: repeated reloads from the middle and footer start at the hero`, async () => {
      await page.goto(origin, { waitUntil: 'networkidle' });
      for (const top of [2400, 100000]) {
        await page.evaluate(top => window.scrollTo({ top, behavior: 'instant' }), top);
        await page.waitForTimeout(180);
        assert.ok(await page.evaluate(() => scrollY > 1000));
        await page.reload({ waitUntil: 'networkidle' });
        await page.waitForTimeout(350);
        traces.push({ label, from: top, trace: await page.evaluate(() => window.__scrollTrace) });
        await atHero(page);
      }
      await page.screenshot({ path: `test-results/reload-${label.toLowerCase()}.png` });
    });
    await check(`${label}: reload clears a section fragment but preserves query parameters`, async () => {
      await page.goto(`${origin}/?source=reload-test#features`, { waitUntil: 'networkidle' });
      await page.waitForFunction(() => scrollY > 300);
      assert.equal(new URL(page.url()).hash, '#features', 'A fresh deep link must still work');
      await page.reload({ waitUntil: 'networkidle' });
      await atHero(page);
      const url = new URL(page.url());
      assert.equal(url.hash, ''); assert.equal(url.search, '?source=reload-test');
    });
    await check(`${label}: scrolling and section navigation remain usable after reload`, async () => {
      await page.locator('a[href="#models"]').first().click();
      await page.waitForFunction(() => location.hash === '#models' && scrollY > 300);
      await page.waitForTimeout(1000);
      assert.ok(await page.evaluate(() => scrollY > 300), 'Unexpected delayed scroll reset');
      assert.equal(await page.evaluate(() => history.scrollRestoration), 'auto');
      assert.deepEqual(errors, []);
    });
    await context.close();
  }
  await check('Reloading /app does not redirect or apply the homepage policy', async () => {
    const page = await browser.newPage();
    await page.goto(`${origin}/app/`, { waitUntil: 'networkidle' });
    await page.reload({ waitUntil: 'networkidle' });
    assert.match(new URL(page.url()).pathname, /^\/app\/?$/);
    assert.equal(await page.locator('#hero-title').count(), 0);
    assert.equal(await page.evaluate(() => history.scrollRestoration), 'auto');
    await page.close();
  });
} finally {
  await browser.close();
  await writeFile('test-results/scroll-traces.json', JSON.stringify(traces, null, 2));
  await writeFile('test-results/scroll-reload.json', JSON.stringify(results, null, 2));
}
for (const result of results) console.log(`${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? ': ' + result.error : ''}`);
if (results.some(result => !result.passed)) process.exitCode = 1;
