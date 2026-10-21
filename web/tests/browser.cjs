const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
let browser;
before(async () => { browser = await chromium.launch(); fs.mkdirSync('test-results', { recursive: true }); });
after(async () => { await browser?.close(); });
const base = process.env.PREVIEW_URL || 'http://127.0.0.1:4173';
async function visit(options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ...options });
  const page = await context.newPage();
  await page.goto(base, { waitUntil: 'networkidle' });
  return { page, context };
}
test('English is the initial language; RU choice persists and changes the whole page', async () => {
  const { page, context } = await visit({ locale: 'ru-RU' });
  try {
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    assert.equal(await page.locator('h1').innerText(), 'Ask anything.');
    await page.getByRole('button', { name: 'Choose language' }).click();
    await page.getByRole('menuitemradio', { name: 'Русский' }).click();
    assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
    assert.match(await page.locator('h1').innerText(), /Спросите/);
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
    await page.getByRole('button', { name: 'Выбрать язык' }).click();
    await page.getByRole('menuitemradio', { name: 'English' }).click();
    const text = await page.locator('body').innerText();
    assert.doesNotMatch(text, /[А-Яа-яЁё]/);
  } finally { await page.screenshot({ path: 'test-results/language.png' }); await context.close(); }
});
test('Hero has no filler captions and uses one headline style', async () => {
  const { page, context } = await visit();
  try {
    const hero = page.locator('#home');
    assert.doesNotMatch(await hero.innerText(), /YOUR MIND|YOUR RULES|воображением|Меньше ограничений|EXPLORE/);
    assert.equal(await hero.locator('h1 em').count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Choose language' }).count(), 1);
  } finally { await context.close(); }
});
test('Background advances without a click and both model rows move in opposite directions even on hover', async () => {
  const { page, context } = await visit({ reducedMotion: 'no-preference' });
  try {
    const video = page.locator('#home video');
    await video.waitFor();
    await page.waitForFunction(() => document.querySelector('#home video')?.currentTime > 0.25);
    const first = await video.evaluate(v => v.currentTime);
    await page.waitForTimeout(600);
    assert.notEqual(await video.evaluate(v => v.currentTime), first);
    assert.equal(await video.evaluate(v => v.muted && v.loop && v.playsInline), true);
    await page.locator('#models').scrollIntoViewIfNeeded();
    await page.locator('[data-model-row="0"]').hover();
    const sample = () => page.locator('[data-model-track]').evaluateAll(nodes => nodes.map(n => new DOMMatrixReadOnly(getComputedStyle(n).transform).m41));
    const a = await sample(); await page.waitForTimeout(600); const b = await sample();
    assert.equal(a.length, 2);
    assert.ok(b[0] > a[0], 'top row must move right');
    assert.ok(b[1] < a[1], 'bottom row must move left');
    const catalog = JSON.parse(fs.readFileSync('src/data/venice-models.json', 'utf8'));
    assert.equal(await page.locator('[data-model-card]:not([aria-hidden="true"])').count(), catalog.models.length);
    assert.ok(catalog.models.length > 100);
  } finally { await page.screenshot({ path: 'test-results/models.png' }); await context.close(); }
});
test('Reduced motion is respected and can be explicitly enabled without changing OS settings', async () => {
  const { page, context } = await visit({ reducedMotion: 'reduce' });
  try {
    const button = page.getByRole('button', { name: 'Enable animations' });
    await button.click();
    await page.locator('#home').scrollIntoViewIfNeeded();
    await page.waitForFunction(() => document.querySelector('#home video')?.currentTime > 0.25);
    assert.equal(await page.locator('html').getAttribute('data-motion'), 'on');
  } finally { await context.close(); }
});
test('Mobile language menu works, no horizontal overflow, and prompt survives docking', async () => {
  const { page, context } = await visit({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    await page.getByRole('button', { name: 'Choose language' }).click();
    await page.getByRole('menuitemradio', { name: 'Русский' }).click();
    await page.locator('#hero-prompt').fill('Keep this draft');
    await page.locator('#models').scrollIntoViewIfNeeded();
    assert.equal(await page.locator('#hero-prompt').inputValue(), 'Keep this draft');
    assert.equal(await page.locator('[data-docked]').getAttribute('data-docked'), 'true');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.locator('#home').scrollIntoViewIfNeeded();
    await page.screenshot({ path: 'test-results/mobile.png' });
  } finally { await context.close(); }
});
test('English guest page preserves input and reports disabled gateway without a model request', async () => {
  const { page, context } = await visit();
  const requests = []; page.on('request', r => { if (r.url().includes('/v1/')) requests.push(r.url()); });
  try {
    await page.locator('#hero-prompt').fill('A retained test prompt');
    await page.locator('#hero-prompt').press('Enter');
    await page.waitForURL('**/app/');
    assert.match(await page.locator('body').innerText(), /A retained test prompt/);
    assert.doesNotMatch(await page.locator('body').innerText(), /[А-Яа-яЁё]/);
    assert.equal(requests.length, 0);
    await page.screenshot({ path: 'test-results/guest.png' });
  } finally { await context.close(); }
});
