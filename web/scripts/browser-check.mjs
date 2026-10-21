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
page.setDefaultTimeout(8000);
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
await check('Globe trigger and compact heading with logo', async () => {
  const trigger = page.getByRole('button', { name: 'Change language' });
  assert.equal(await trigger.locator('[data-language-globe]').count(), 1);
  assert.equal(await trigger.locator('[data-language-flag]').count(), 0);
  const heading = await page.locator('h1').boundingBox();
  const logo = await page.locator('[data-hero-logo]').boundingBox();
  assert.ok(logo && heading && logo.y + logo.height <= heading.y);
  assert.ok(await page.locator('h1').evaluate(el => parseFloat(getComputedStyle(el).fontSize) <= 50));
  assert.equal(await page.locator('[data-ready]').evaluate(el => getComputedStyle(el, '::before').content), 'none');
});
await check('Hover previews fade and slide without inserting or submitting text', async () => {
  await page.getByRole('button', { name: 'Write a story', exact: true }).hover();
  const preview = page.locator('[data-prompt-preview]');
  await page.waitForTimeout(160);
  assert.ok((await preview.innerText()).includes('noir'));
  assert.equal(await page.locator('#hero-prompt').inputValue(), '');
  await page.getByRole('button', { name: 'Write code', exact: true }).hover();
  await page.waitForTimeout(160);
  assert.ok((await preview.innerText()).includes('portfolio'));
  await page.mouse.move(20, 150);
  await page.waitForTimeout(160);
  assert.equal((await preview.innerText()).trim(), 'Ask anything…');
  assert.equal(new URL(page.url()).pathname, '/');
});
await page.screenshot({ path: 'test-results/desktop-en.png' });
await check('Language menu, Russian translation, persistence and no prompt loss', async () => {
  await page.locator('#hero-prompt').fill('Keep this prompt');
  await page.getByRole('button', { name: 'Change language' }).click();
  assert.equal(await page.getByRole('menuitemradio').count(), 2);
  assert.equal(await page.getByRole('menu').locator('[data-language-flag]').count(), 2);
  await page.getByRole('menuitemradio', { name: 'Русский' }).click();
  assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
  assert.equal((await page.locator('h1').innerText()).replace(/\s+/g, ' ').trim(), 'Спросите о чём угодно');
  assert.equal(await page.locator('#hero-prompt').inputValue(), 'Keep this prompt');
  await page.screenshot({ path: 'test-results/desktop-ru.png' });
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
  await page.waitForFunction(() => [...document.querySelectorAll('[data-model-name] img')].every(img => img.complete && img.naturalWidth > 0));
  assert.equal(await strips.locator('[data-model-name] img').count(), 20);
  assert.doesNotMatch(await page.locator('body').innerText(), /venice/i);
  const position = async direction => page.locator(`[data-marquee="${direction}"]`).evaluate(el => new DOMMatrix(getComputedStyle(el).transform).m41);
  const right = await position('right'); const left = await position('left');
  await page.waitForTimeout(600);
  assert.ok(await position('right') > right, 'Top row should move right');
  assert.ok(await position('left') < left, 'Bottom row should move left');
  await page.screenshot({ path: 'test-results/models.png' });
});
await check('Docked bar still blurs content', async () => {
  await page.locator('#models').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const panel = page.locator('[data-docked="true"]');
  assert.equal(await panel.count(), 1);
  assert.ok(await panel.evaluate(el => getComputedStyle(el).backdropFilter.includes('blur')));
});
await check('Docked button expands prompt and restores navigation without losing draft', async () => {
  const panel = page.locator('[data-panel-mode]');
  const toggle = page.locator('[data-prompt-toggle]');
  await page.locator('#models').scrollIntoViewIfNeeded();
  await page.waitForTimeout(200);
  assert.equal(await panel.getAttribute('data-panel-mode'), 'navigation');
  assert.ok((await toggle.innerText()).includes('Ask anything'));
  const left = await toggle.boundingBox();
  await toggle.click();
  await page.waitForTimeout(180);
  assert.equal(await panel.getAttribute('data-panel-mode'), 'prompt');
  assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
  const right = await toggle.boundingBox();
  assert.ok(right.x > left.x + 400, 'Toggle moves to the right');
  assert.ok(await page.locator('[data-nav-content]').evaluate(el => el.inert && getComputedStyle(el).opacity === '0'));
  await page.locator('#hero-prompt').fill('Persistent topbar draft');
  await page.screenshot({ path: 'test-results/topbar-expanded.png' });
  await toggle.click();
  await page.waitForTimeout(180);
  assert.equal(await panel.getAttribute('data-panel-mode'), 'navigation');
  assert.ok(Math.abs((await toggle.boundingBox()).x - left.x) < 2);
  assert.ok(await page.locator('[data-nav-content]').evaluate(el => !el.inert && getComputedStyle(el).opacity === '1'));
  assert.equal(await page.locator('#hero-prompt').inputValue(), 'Persistent topbar draft');
  assert.ok(await page.locator('[data-composer-form]').evaluate(el => el.inert));
  await page.screenshot({ path: 'test-results/topbar-navigation.png' });
  await toggle.click();
  await page.waitForTimeout(180);
  assert.equal(await page.locator('#hero-prompt').inputValue(), 'Persistent topbar draft');
  await page.locator('#hero-prompt').press('Escape');
  await page.waitForTimeout(180);
  assert.equal(await panel.getAttribute('data-panel-mode'), 'navigation');
  await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
  await page.waitForTimeout(200);
  assert.equal(await panel.getAttribute('data-panel-mode'), 'hero');
  assert.equal(await page.locator('#hero-prompt').inputValue(), 'Persistent topbar draft');
});
await check('Guest route is English and does not send requests when disabled', async () => {
  const requests = [];
  page.on('request', req => { if (req.url().includes('/v1/web')) requests.push(req.url()); });
  await page.goto(origin, { waitUntil: 'networkidle' });
  await page.locator('#hero-prompt').fill('My local draft');
  await page.getByRole('button', { name: 'Send prompt', exact: true }).click();
  await page.waitForURL(url => /^\/app\/?$/.test(url.pathname));
  assert.ok((await page.locator('body').innerText()).includes('My local draft'));
  assert.ok((await page.locator('body').innerText()).includes('Demo unavailable'));
  assert.equal(requests.length, 0);
  await page.screenshot({ path: 'test-results/chat-en.png' });
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
  await p.locator('#models').scrollIntoViewIfNeeded();
  await p.waitForTimeout(200);
  await p.locator('[data-prompt-toggle]').click();
  await p.waitForTimeout(180);
  await p.locator('#hero-prompt').fill('Mobile prompt');
  assert.equal(await p.locator('[data-panel-mode]').getAttribute('data-panel-mode'), 'prompt');
  assert.ok(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await p.screenshot({ path: 'test-results/mobile-topbar.png' });
  await p.locator('[data-prompt-toggle]').click();
  await p.waitForTimeout(180);
  assert.equal(await p.locator('#hero-prompt').inputValue(), 'Mobile prompt');
  await p.getByRole('button', { name: 'Open menu' }).click();
  assert.ok(await p.locator('#mobile-navigation').isVisible());
  await mobile.close();
});
await check('Background autoplays without an extra button under reduced-motion', async () => {
  const reduced = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
  const p = await reduced.newPage();
  await p.goto(origin, { waitUntil: 'networkidle' });
  assert.equal(await p.getByRole('button', { name: 'Enable animation' }).count(), 0);
  assert.equal(await p.locator('[data-hero-video]').evaluate(v => v.autoplay && v.muted && v.playsInline), true);
  await p.waitForFunction(() => { const v = document.querySelector('[data-hero-video]'); return v && !v.paused && v.currentTime > .15; });
  assert.equal(await p.locator('[data-marquee="right"]').evaluate(el => getComputedStyle(el).animationPlayState), 'running');
  await reduced.close();
});
await browser.close();
await writeFile('test-results/results.json', JSON.stringify(results, null, 2));
for (const result of results) console.log(`${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? ': ' + result.error : ''}`);
if (results.some(result => !result.passed)) process.exitCode = 1;
