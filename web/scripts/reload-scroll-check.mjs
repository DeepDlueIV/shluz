import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch();
const results = [];
const errors = [];
async function check(name, mobile, run) {
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, reducedMotion: 'no-preference' });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(String(error)));
  try { await run(page); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: String(error) }); }
  finally { await context.close(); }
}
async function expectHero(page) {
  await page.waitForTimeout(400);
  const state = await page.evaluate(() => ({ y: scrollY, hash: location.hash, mode: document.querySelector('[data-prompt-mode]')?.getAttribute('data-prompt-mode'), type: performance.getEntriesByType('navigation')[0].type }));
  assert.equal(state.type, 'reload');
  assert.ok(state.y < 2, `Reload restored scroll position ${state.y}`);
  assert.equal(state.hash, '');
  assert.equal(state.mode, 'hero');
  const input = await page.locator('#hero-prompt').boundingBox();
  assert.ok(input && input.y >= 0 && input.y + input.height < page.viewportSize().height);
}
for (const mobile of [false, true]) {
  const device = mobile ? 'mobile' : 'desktop';
  await check(`${device}: reload from media section starts at hero`, mobile, async page => {
    await page.goto(origin, { waitUntil: 'networkidle' });
    await page.evaluate(() => { localStorage.setItem('reload-check', 'keep'); window.scrollTo({ top: 2800, behavior: 'instant' }); });
    await page.waitForTimeout(250);
    assert.ok(await page.evaluate(() => scrollY) > 1000);
    await page.reload({ waitUntil: 'networkidle' });
    await expectHero(page);
    assert.equal(await page.evaluate(() => localStorage.getItem('reload-check')), 'keep');
    await page.screenshot({ path: `test-results/reload-${device}.png` });
  });
  await check(`${device}: reload clears section fragment but keeps query`, mobile, async page => {
    await page.goto(`${origin}/?source=reload-test#features`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    assert.ok(await page.evaluate(() => scrollY) > 500);
    await page.reload({ waitUntil: 'networkidle' });
    await expectHero(page);
    assert.equal(new URL(page.url()).search, '?source=reload-test');
  });
}
await check('Anchor links still work after a reload', false, async page => {
  await page.goto(origin, { waitUntil: 'networkidle' });
  await page.reload({ waitUntil: 'networkidle' });
  await expectHero(page);
  await page.locator('a[href="#models"]').first().click();
  await page.waitForTimeout(1100);
  assert.equal(new URL(page.url()).hash, '#models');
  assert.ok(await page.evaluate(() => scrollY) > 500);
  await page.getByRole('navigation', { name: 'Main navigation', exact: true }).getByRole('link', { name: 'Features', exact: true }).click();
  await page.waitForTimeout(1100);
  assert.equal(new URL(page.url()).hash, '#features');
});
await check('Scrolling after reload is not forced back to top by delayed resets', false, async page => {
  await page.goto(origin, { waitUntil: 'networkidle' });
  await page.reload({ waitUntil: 'networkidle' });
  await expectHero(page);
  assert.equal(await page.evaluate(() => history.scrollRestoration), 'auto');
  await page.evaluate(() => window.scrollTo({ top: 1800, behavior: 'instant' }));
  await page.waitForTimeout(1500);
  assert.ok(await page.evaluate(() => scrollY) > 1700);
});
await check('Direct section URLs still navigate to that section', false, async page => {
  await page.goto(`${origin}/#pricing`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  assert.equal(new URL(page.url()).hash, '#pricing');
  assert.ok(await page.evaluate(() => scrollY) > 1000);
  assert.equal(await page.evaluate(() => history.scrollRestoration), 'auto');
});
await check('Guest route reload does not lose its path, query or fragment', false, async page => {
  await page.goto(`${origin}/app?source=reload-test#chat`, { waitUntil: 'networkidle' });
  await page.reload({ waitUntil: 'networkidle' });
  const url = new URL(page.url());
  assert.match(url.pathname, /^\/app\/?$/);
  assert.equal(url.search, '?source=reload-test');
  assert.equal(url.hash, '#chat');
  assert.equal(await page.evaluate(() => history.scrollRestoration), 'auto');
});
await browser.close();
if (errors.length) results.push({ name: 'No browser runtime errors', passed: false, error: errors.join('\n') });
await writeFile('test-results/reload-scroll-results.json', JSON.stringify(results, null, 2));
for (const result of results) console.log(`${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? ': ' + result.error : ''}`);
if (results.some(result => !result.passed)) process.exitCode = 1;
