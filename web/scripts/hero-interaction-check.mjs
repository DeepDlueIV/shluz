import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch();
const results = [];
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' });
const page = await context.newPage();
page.setDefaultTimeout(8000);
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
const check = async (name, run) => { try { await run(); results.push({ name, passed: true }); } catch (e) { results.push({ name, passed: false, error: String(e) }); } };
const home = () => page.goto(origin, { waitUntil: 'networkidle' });
await home();
await check('Globe trigger, dropdown flags and compact heading with logo', async () => {
  assert.equal(await page.locator('[data-language-globe]').count(), 1);
  assert.equal((await page.getByRole('button', { name: 'Change language' }).innerText()).trim(), '');
  assert.equal(await page.locator('[data-prompt-mode] form img').count(), 0);
  assert.equal((await page.locator('[data-prompt-preview]').innerText()).trim(), 'Ask me anything, privately…');
  const font = await page.locator('h1').evaluate(el => parseFloat(getComputedStyle(el).fontSize));
  assert.ok(font >= 30 && font <= 52, `Heading size ${font}`);
  assert.equal(await page.locator('[data-hero-logo]').count(), 1);
  await page.getByRole('button', { name: 'Change language' }).click();
  assert.equal(await page.locator('[role="menuitemradio"] svg').count(), 2);
  await page.keyboard.press('Escape');
  assert.doesNotMatch(await page.locator('body').innerText(), /venice/i);
  assert.equal(await page.locator('a[href*="venice"]').count(), 0);
  assert.equal(await page.locator('[data-prompt-mode]').evaluate(el => getComputedStyle(el, '::before').content), 'none');
  await page.screenshot({ path: 'test-results/hero-refined.png' });
});
await check('Suggestion preview fades and slides; native value stays untouched', async () => {
  await page.evaluate(() => {
    window.hintFrames = [];
    const end = performance.now() + 900;
    function sample() {
      for (const el of document.querySelectorAll('[data-prompt-preview] span')) {
        const s = getComputedStyle(el); window.hintFrames.push({ opacity: Number(s.opacity), x: s.transform });
      }
      if (performance.now() < end) requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  });
  await page.getByRole('button', { name: 'Bypass government surveillance', exact: true }).hover();
  await page.waitForTimeout(1000);
  assert.match(await page.locator('[data-prompt-preview]').innerText(), /digital privacy/);
  assert.equal(await page.locator('#hero-prompt').inputValue(), '');
  const frames = await page.evaluate(() => window.hintFrames);
  assert.ok(frames.some(f => f.opacity > 0 && f.opacity < 1), 'No fade frames');
  assert.ok(frames.some(f => f.x !== 'none' && f.x !== 'matrix(1, 0, 0, 1, 0, 0)'), 'No slide frames');
  await page.mouse.move(10, 200); await page.waitForTimeout(1000);
  assert.equal((await page.locator('[data-prompt-preview]').innerText()).trim(), 'Ask me anything, privately…');
});
await check('Rapid hint changes do not overlap or flash the default in chip gaps', async () => {
  const story = page.getByRole('button', { name: 'Bypass government surveillance', exact: true });
  const code = page.getByRole('button', { name: 'Crack DRM protection', exact: true });
  await story.hover(); await page.waitForTimeout(1000);
  const a = await story.boundingBox(); const b = await code.boundingBox();
  await page.mouse.move((a.x + a.width + b.x) / 2, a.y + a.height / 2);
  await page.waitForTimeout(100);
  assert.match(await page.locator('[data-prompt-preview]').innerText(), /digital privacy/);
  await page.evaluate(() => {
    window.hintCounts = [];
    const end = performance.now() + 1500;
    function sample() {
      window.hintCounts.push(document.querySelectorAll('[data-prompt-preview] span').length);
      if (performance.now() < end) requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  });
  await code.hover(); await page.waitForTimeout(70);
  await story.hover(); await page.waitForTimeout(70);
  await page.getByRole('button', { name: 'Debate cannibalism', exact: true }).hover();
  await page.waitForTimeout(1000);
  assert.match(await page.locator('[data-prompt-preview]').innerText(), /ethical and cultural taboo/);
  assert.ok((await page.evaluate(() => window.hintCounts)).every(n => n <= 1), 'Overlapping prompt hints');
  assert.equal(await page.locator('#hero-prompt').inputValue(), '');
  await page.mouse.move(10, 200); await page.waitForTimeout(1000);
});
await check('Unified toolbar button expands and returns without losing text', async () => {
  await page.locator('#hero-prompt').fill('Preserve this draft');
  await page.evaluate(() => window.scrollTo({ top: 1050, behavior: 'instant' })); await page.waitForTimeout(250);
  const panel = page.locator('[data-prompt-mode]'); const toggle = page.locator('[data-prompt-toggle]');
  assert.equal(await panel.getAttribute('data-prompt-mode'), 'navigation');
  assert.equal(await toggle.getAttribute('type'), 'button');
  assert.equal(await toggle.evaluate(el => getComputedStyle(el).borderTopWidth), '1px');
  const collapsed = await toggle.boundingBox();
  await toggle.click(); await page.waitForTimeout(120);
  const midway = await page.locator('[data-nav-area]').evaluate(el => Number(getComputedStyle(el).opacity));
  assert.ok(midway > .01 && midway < .99, `Transition finished too early: ${midway}`);
  await page.waitForTimeout(1000);
  assert.equal(await panel.getAttribute('data-prompt-mode'), 'prompt');
  assert.equal(await page.locator('#hero-prompt').inputValue(), 'Preserve this draft');
  assert.equal(await page.locator('#hero-prompt').evaluate(el => el === document.activeElement), true);
  assert.equal(await page.locator('[data-nav-area]').getAttribute('inert'), '');
  assert.ok(await page.locator('[data-nav-area]').evaluate(el => Number(getComputedStyle(el).opacity) < .01));
  const open = await toggle.boundingBox(); const bounds = await panel.boundingBox();
  assert.ok(open.x > collapsed.x + 400, 'Arrow did not move right');
  assert.ok(Math.abs(bounds.x + bounds.width - open.x - open.width) < 18);
  await page.screenshot({ path: 'test-results/toolbar-expanded.png' });
  await toggle.click(); await page.waitForTimeout(1000);
  assert.equal(await panel.getAttribute('data-prompt-mode'), 'navigation');
  assert.ok(await page.locator('[data-nav-area]').evaluate(el => Number(getComputedStyle(el).opacity) > .99));
  assert.equal(await page.locator('#hero-prompt').inputValue(), 'Preserve this draft');
  await page.screenshot({ path: 'test-results/toolbar-navigation.png' });
  await toggle.evaluate(el => { for (let i=0;i<7;i++) el.click(); }); await page.waitForTimeout(1000);
  assert.equal(await panel.getAttribute('data-prompt-mode'), 'prompt');
  await page.locator('#hero-prompt').press('Escape'); await page.waitForTimeout(1000);
  assert.equal(await panel.getAttribute('data-prompt-mode'), 'navigation');
  await toggle.click(); await page.waitForTimeout(1000);
  await page.locator('#hero-prompt').press('Enter');
  await page.waitForURL(url => /^\/app\/?$/.test(url.pathname));
  assert.match(await page.locator('body').innerText(), /Preserve this draft/);
});
await check('All twenty provider names have loaded local brand marks', async () => {
  await home(); await page.locator('[data-model-strips]').scrollIntoViewIfNeeded();
  const icons = page.locator('[data-model-name] [data-model-icon]');
  assert.equal(await icons.count(), 20);
  await icons.evaluateAll(images => images.forEach(img => img.loading = 'eager'));
  await page.waitForFunction(() => [...document.querySelectorAll('[data-model-name] [data-model-icon]')].every(img => img.complete && img.naturalWidth > 0), null, { timeout: 8000 });
  const urls = await icons.evaluateAll(images => images.map(img => img.getAttribute('src')));
  assert.equal(new Set(urls).size, 20);
  assert.ok(urls.every(url => url.startsWith('/media/models/')));
  await page.screenshot({ path: 'test-results/model-icons.png' });
});
await check('Mobile toolbar preserves draft and fits screen', async () => {
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const p = await mobile.newPage(); p.setDefaultTimeout(8000);
  await p.goto(origin, { waitUntil: 'networkidle' });
  assert.ok(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await p.screenshot({ path: 'test-results/mobile-refined.png' });
  await p.evaluate(() => window.scrollTo({ top: 1000, behavior: 'instant' })); await p.waitForTimeout(250);
  await p.locator('[data-prompt-toggle]').click(); await p.waitForTimeout(1000);
  await p.locator('#hero-prompt').fill('Mobile draft');
  await p.locator('[data-prompt-toggle]').click(); await p.waitForTimeout(1000);
  await p.locator('[data-prompt-toggle]').click(); await p.waitForTimeout(1000);
  assert.equal(await p.locator('#hero-prompt').inputValue(), 'Mobile draft');
  assert.ok(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await p.screenshot({ path: 'test-results/mobile-toolbar.png' }); await mobile.close();
});
await check('No runtime or hydration errors', () => assert.deepEqual(errors, []));
await browser.close();
await writeFile('test-results/hero-interactions.json', JSON.stringify(results, null, 2));
for (const r of results) console.log(`${r.passed ? 'PASS' : 'FAIL'} ${r.name}${r.error ? ': ' + r.error : ''}`);
if (results.some(r => !r.passed)) process.exitCode = 1;
