import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
for (let i = 0; i < 90; i++) {
  if (await fetch(origin).then(r => r.ok).catch(() => false)) break;
  if (i === 89) throw new Error('Website did not start');
  await new Promise(resolve => setTimeout(resolve, 1000));
}
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch();
const results = [];
async function check(name, run) {
  try { await run(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: String(error) }); }
}
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'ru-RU', reducedMotion: 'no-preference' });
const page = await context.newPage();
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(String(error)));
await page.goto(origin, { waitUntil: 'networkidle' });
await check('English is the default even in a Russian browser', async () => {
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  assert.equal((await page.locator('h1').innerText()).replace(/\s+/g, ' ').trim(), 'Ask anything');
  assert.equal(await page.locator('h1 em').count(), 0);
});
await check('Removed decorative hero copy', async () => {
  const hero = await page.locator('#home').innerText();
  for (const text of ['YOUR MIND. YOUR RULES.', '01 — EXPLORE', 'Начните с вопроса. Остальное', 'Меньше ограничений']) assert.ok(!hero.includes(text), text);
});
await check('Background video actually advances', async () => {
  await page.waitForFunction(() => { const v = document.querySelector('[data-hero-video]'); return v && !v.paused && v.currentTime > 0.15; }, null, { timeout: 15000 });
  const before = await page.locator('[data-hero-video]').evaluate(v => v.currentTime);
  await page.waitForTimeout(500);
  const after = await page.locator('[data-hero-video]').evaluate(v => v.currentTime);
  assert.notEqual(before, after);
});
await page.screenshot({ path: 'test-results/desktop-en.png' });
await check('Language menu, Russian translation, persistence and no prompt loss', async () => {
  await page.locator('#hero-prompt').fill('Keep this prompt');
  await page.getByRole('button', { name: 'Change language' }).click();
  assert.equal(await page.getByRole('menuitemradio').count(), 2);
  await page.getByRole('menuitemradio', { name: 'Русский' }).click();
  assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
  assert.equal((await page.locator('h1').innerText()).replace(/\s+/g, ' ').trim(), 'Спросите о чём угодно');
  assert.equal(await page.locator('#hero-prompt').inputValue(), 'Keep this prompt');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.documentElement.lang === 'ru');
  await page.getByRole('button', { name: 'Выбрать язык' }).click();
  await page.getByRole('menuitemradio', { name: 'English' }).click();
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
});
await check('Complete reference list; rows move in opposite directions while hovered', async () => {
  const strips = page.locator('[data-model-strips]');
  await strips.scrollIntoViewIfNeeded();
  await strips.hover();
  assert.equal(await strips.locator('[data-model-name]').count(), 20);
  const position = async direction => page.locator(`[data-marquee="${direction}"]`).evaluate(el => new DOMMatrix(getComputedStyle(el).transform).m41);
  const right = await position('right'); const left = await position('left');
  await page.waitForTimeout(600);
  assert.ok(await position('right') > right, 'Top row should move right');
  assert.ok(await position('left') < left, 'Bottom row should move left');
});
await check('Docked bar still blurs content', async () => {
  await page.locator('#models').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const panel = page.locator('[data-docked="true"]');
  assert.equal(await panel.count(), 1);
  assert.ok(await panel.evaluate(el => getComputedStyle(el).backdropFilter.includes('blur')));
});
await check('Guest route is English and does not send requests when disabled', async () => {
  const requests = [];
  page.on('request', req => { if (req.url().includes('/v1/web')) requests.push(req.url()); });
  await page.goto(origin, { waitUntil: 'networkidle' });
  await page.locator('#hero-prompt').fill('My local draft');
  await page.getByRole('button', { name: 'Send prompt', exact: true }).click();
  await page.waitForURL('**/app/');
  assert.ok((await page.locator('body').innerText()).includes('My local draft'));
  assert.ok((await page.locator('body').innerText()).includes('Demo unavailable'));
  assert.equal(requests.length, 0);
});
await check('No hydration or JavaScript errors', async () => assert.deepEqual(pageErrors, []));
await check('Mobile layout and language menu', async () => {
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 1, reducedMotion: 'no-preference' });
  const p = await mobile.newPage();
  await p.goto(origin, { waitUntil: 'networkidle' });
  assert.ok(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await p.getByRole('button', { name: 'Change language' }).click();
  assert.equal(await p.getByRole('menuitemradio').count(), 2);
  await p.keyboard.press('Escape');
  await p.screenshot({ path: 'test-results/mobile-en.png' });
  await mobile.close();
});
await check('Reduced-motion setting is respected, with explicit animation opt-in', async () => {
  const reduced = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
  const p = await reduced.newPage();
  await p.goto(origin, { waitUntil: 'networkidle' });
  assert.equal(await p.locator('[data-hero-video]').evaluate(v => v.paused), true);
  await p.getByRole('button', { name: 'Enable animation' }).click();
  await p.waitForFunction(() => { const v = document.querySelector('[data-hero-video]'); return v && !v.paused && v.currentTime > .15; });
  assert.equal(await p.locator('[data-marquee="right"]').evaluate(el => getComputedStyle(el).animationPlayState), 'running');
  await reduced.close();
});
await browser.close();
await writeFile('test-results/results.json', JSON.stringify(results, null, 2));
for (const result of results) console.log(`${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? ': ' + result.error : ''}`);
if (results.some(result => !result.passed)) process.exitCode = 1;
