import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(8000);
const results = [], errors = [], requests = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('request', req => { if (req.url().includes('/v1/web/')) requests.push(req.url()); });
const check = async (name, run) => { try { await run(); results.push({ name, passed: true }); } catch (e) { results.push({ name, passed: false, error: String(e) }); } };
const home = async () => { await page.goto(origin, { waitUntil: 'networkidle' }); };
await home();
await check('Four primary topics plus More; ten extra topics are initially collapsed', async () => {
  assert.equal(await page.locator('[data-primary-suggestions] button').count(), 5);
  assert.deepEqual(await page.locator('[data-primary-suggestions] [data-suggestion] span').allTextContents(), ['Bypass government surveillance', 'Crack DRM protection', 'Write explicit erotica', 'Debate cannibalism']);
  assert.equal(await page.locator('[data-more-prompts]').getAttribute('aria-expanded'), 'false');
  assert.equal(await page.locator('[data-additional-prompts]').count(), 0);
});
await check('More opens on hover with two five-item rows and mapped icons', async () => {
  await page.locator('[data-more-prompts]').hover(); await page.waitForTimeout(1000);
  assert.equal(await page.locator('[data-additional-prompts] button').count(), 10);
  assert.equal(await page.locator('[data-suggestion-icon]').count(), 14);
  const boxes = await page.locator('[data-additional-prompts] button').evaluateAll(nodes => nodes.map(n => ({ y: n.getBoundingClientRect().y, w: n.getBoundingClientRect().width })));
  assert.ok(boxes.slice(0, 5).every(b => Math.abs(b.y - boxes[0].y) < 2));
  assert.ok(boxes.slice(5).every(b => Math.abs(b.y - boxes[5].y) < 2));
  assert.ok(boxes[5].y > boxes[0].y + 20);
  await page.screenshot({ path: 'test-results/more-desktop.png' });
});
await check('Shared hover area retains the panel across gaps and closes outside', async () => {
  const top = await page.locator('[data-primary-suggestions]').boundingBox();
  const extras = await page.locator('[data-additional-prompts] button').first().boundingBox();
  await page.mouse.move(top.x + top.width / 2, (top.y + top.height + extras.y) / 2);
  await page.waitForTimeout(1000);
  assert.equal(await page.locator('[data-more-prompts]').getAttribute('aria-expanded'), 'true');
  await page.locator('[data-suggestion="horror"]').hover(); await page.waitForTimeout(1000);
  assert.match(await page.locator('[data-prompt-preview]').innerText(), /psychological horror/);
  assert.equal(await page.locator('#hero-prompt').inputValue(), '');
  await page.mouse.move(15, 150); await page.waitForTimeout(1000);
  assert.equal(await page.locator('[data-additional-prompts]').count(), 0);
  assert.equal((await page.locator('[data-prompt-preview]').innerText()).trim(), 'Ask me anything, privately…');
});
await check('Preview uses ellipsis; choosing keeps the entire prompt without submitting', async () => {
  await page.locator('[data-more-prompts]').hover(); await page.waitForTimeout(1000);
  const choice = page.locator('[data-suggestion="reverse"]');
  await choice.hover(); await page.waitForTimeout(1000);
  const text = await page.locator('[data-prompt-preview]').innerText();
  assert.ok(text.length > 120);
  const clip = await page.locator('[data-prompt-preview] span').evaluate(el => ({ width: el.clientWidth, content: el.scrollWidth, ellipsis: getComputedStyle(el).textOverflow, whitespace: getComputedStyle(el).whiteSpace }));
  assert.equal(clip.ellipsis, 'ellipsis'); assert.equal(clip.whitespace, 'nowrap'); assert.ok(clip.content > clip.width);
  await choice.click();
  assert.equal(await page.locator('#hero-prompt').inputValue(), text);
  assert.equal(new URL(page.url()).pathname, '/');
  assert.deepEqual(requests, []);
});
await check('Keyboard opens More, moves focus, and Escape closes without losing draft', async () => {
  await home();
  const more = page.locator('[data-more-prompts]');
  await more.focus(); await page.keyboard.press('ArrowDown'); await page.waitForTimeout(1000);
  assert.equal(await page.locator('[data-suggestion="passwords"]').evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Escape'); await page.waitForTimeout(1000);
  assert.equal(await more.getAttribute('aria-expanded'), 'false');
  assert.equal(await more.evaluate(el => el === document.activeElement), true);
});
await check('Mobile tap opens extras, fills prompt, and does not overflow', async () => {
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const p = await mobile.newPage(); p.setDefaultTimeout(8000);
  await p.goto(origin, { waitUntil: 'networkidle' });
  await p.locator('[data-more-prompts]').tap(); await p.waitForTimeout(1000);
  assert.equal(await p.locator('[data-additional-prompts] button').count(), 10);
  const mobileFilters = await p.locator('[data-primary-suggestions] button, [data-additional-prompts] button').evaluateAll(nodes => nodes.map(el => getComputedStyle(el).backdropFilter));
  assert.equal(mobileFilters.length, 15);
  assert.ok(mobileFilters.every(value => value === 'blur(32px) saturate(1.2)'));
  assert.ok(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await p.screenshot({ path: 'test-results/more-mobile.png', fullPage: false });
  assert.equal(await p.locator('a[href="#models"]').first().isVisible(), false);
  await p.locator('[data-suggestion="religion"]').tap();
  assert.match(await p.locator('#hero-prompt').inputValue(), /Critically examine religious taboos/);
  await mobile.close();
});
await check('Russian translations retain the same suggestion identities', async () => {
  await home(); await page.getByRole('button', { name: 'Change language' }).click();
  await page.getByRole('menuitemradio', { name: 'Русский' }).click();
  assert.equal((await page.locator('[data-more-prompts]').innerText()).trim(), 'Ещё');
  await page.locator('[data-more-prompts]').hover(); await page.waitForTimeout(1000);
  assert.equal(await page.locator('[data-additional-prompts] button').count(), 10);
  for (const label of await page.locator('[data-suggestion] span').allTextContents()) assert.match(label, /[а-яё]/i);
  await page.screenshot({ path: 'test-results/more-ru.png' });
});
await check('No runtime errors or unsolicited model requests', () => { assert.deepEqual(errors, []); assert.deepEqual(requests, []); });
await check('Glass chips: stronger blur without darker fill, white text and equal row widths', async () => {
  await page.evaluate(() => localStorage.removeItem('noclip.locale'));
  await home();
  const before = await page.locator('[data-primary-suggestions]').boundingBox();
  assert.ok(before.width < 1000, `The compact first row is still ${before.width}px`);
  await page.locator('[data-more-prompts]').hover(); await page.waitForTimeout(900);
  const after = await page.locator('[data-primary-suggestions]').boundingBox();
  const extra = await page.locator('[data-additional-prompts] > div').boundingBox();
  assert.ok(Math.abs(before.width - after.width) < 1, 'Opening More changes the block width');
  assert.ok(Math.abs(extra.width - after.width) < 1 && Math.abs(extra.x - after.x) < 1);
  const composerBlur = await page.locator('[data-prompt-mode="hero"]').evaluate(el => getComputedStyle(el).backdropFilter);
  assert.equal(composerBlur, 'blur(27px) saturate(1.2)');
  assert.equal(await page.locator('[data-more-prompts]').evaluate(el => getComputedStyle(el).backdropFilter), 'blur(32px) saturate(1.2)');
  const chips = await page.locator('[data-suggestion]').evaluateAll(nodes => nodes.map(el => {
    const s = getComputedStyle(el), label = getComputedStyle(el.querySelector('span'));
    return { color: s.color, weight: s.fontWeight, font: parseFloat(s.fontSize), blur: s.backdropFilter, background: s.backgroundColor, height: el.getBoundingClientRect().height, whiteSpace: label.whiteSpace };
  }));
  for (const chip of chips) {
    assert.equal(chip.color, 'rgb(255, 255, 255)');
    assert.ok(Number(chip.weight) >= 600 && chip.font >= 13);
    assert.equal(chip.blur, 'blur(32px) saturate(1.2)');
    assert.equal(chip.background, 'rgba(26, 27, 29, 0.208)');
    assert.equal(chip.whiteSpace, 'nowrap'); assert.equal(chip.height, chips[0].height);
  }
  await page.screenshot({ path: 'test-results/readable-desktop.png' });
});
await check('Full prompt fills the available width on desktop and mobile; selecting keeps all text', async () => {
  for (const width of [1440, 768, 390]) {
    const c = await browser.newContext({ viewport: { width, height: 1000 } });
    const p = await c.newPage(); await p.goto(origin, { waitUntil: 'networkidle' });
    await p.locator('[data-suggestion="surveillance"]').hover(); await p.waitForTimeout(900);
    const text = await p.locator('[data-prompt-preview]').innerText();
    assert.ok(text.length > 420);
    const size = await p.locator('[data-prompt-preview] span').evaluate(el => {
      const input = document.querySelector('#hero-prompt'), style = getComputedStyle(input);
      return { actual: el.clientWidth, available: input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight), content: el.scrollWidth };
    });
    assert.ok(Math.abs(size.actual - size.available) <= 2, JSON.stringify(size));
    assert.ok(size.content > size.actual);
    await p.locator('[data-suggestion="surveillance"]').click();
    assert.equal(await p.locator('#hero-prompt').inputValue(), text);
    await p.waitForTimeout(750);
    assert.equal(await p.locator('#hero-prompt').evaluate(el => el.scrollLeft), 0);
    assert.ok(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    if (width === 390) {
      await p.locator('#hero-prompt').fill('');
      await p.locator('[data-more-prompts]').click(); await p.waitForTimeout(800);
      await p.screenshot({ path: 'test-results/readable-mobile.png' });
    }
    await c.close();
  }
});
await check('Requested fade and slide remain visible with reduced-motion: text, More and language', async () => {
  const c = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
  const p = await c.newPage(); await p.goto(origin, { waitUntil: 'networkidle' });
  await p.evaluate(() => {
    window.motionSamples = [];
    const end = performance.now() + 1200;
    const sample = () => {
      const el = document.querySelector('[data-prompt-preview] span');
      if (el) { const s = getComputedStyle(el); window.motionSamples.push({ opacity: Number(s.opacity), x: new DOMMatrix(s.transform).m41 }); }
      if (performance.now() < end) requestAnimationFrame(sample);
    }; sample();
  });
  await p.locator('[data-suggestion="surveillance"]').hover(); await p.waitForTimeout(950);
  const samples = await p.evaluate(() => window.motionSamples);
  assert.ok(samples.filter(f => f.opacity > .05 && f.opacity < .95).length > 8, 'Fade is too brief or disabled');
  assert.ok(samples.some(f => f.x < -3), 'Missing left-to-right text slide');
  await p.locator('[data-more-prompts]').hover(); await p.waitForTimeout(120);
  const more = await p.locator('[data-additional-prompts]').evaluate(el => ({ opacity: Number(getComputedStyle(el).opacity), y: new DOMMatrix(getComputedStyle(el).transform).m42 }));
  assert.ok(more.opacity > 0 && more.opacity < 1 && more.y < 0, JSON.stringify(more));
  await p.waitForTimeout(650);
  await p.getByRole('button', { name: 'Change language' }).click(); await p.waitForTimeout(120);
  const menu = await p.locator('[data-language-menu]').evaluate(el => ({ opacity: Number(getComputedStyle(el).opacity), y: new DOMMatrix(getComputedStyle(el).transform).m42 }));
  assert.ok(menu.opacity > 0 && menu.opacity < 1 && menu.y < 0, JSON.stringify(menu));
  await p.waitForTimeout(500); await p.keyboard.press('Escape'); await p.waitForTimeout(120);
  assert.ok(await p.locator('[data-language-menu]').evaluate(el => Number(getComputedStyle(el).opacity) > 0));
  assert.equal(await p.locator('[data-language-menu]').getAttribute('inert'), '');
  await p.waitForTimeout(500); await c.close();
});
await check('Toolbar arrow travels slowly in both directions, including reduced-motion mode', async () => {
  const c = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
  const p = await c.newPage(); await p.goto(origin, { waitUntil: 'networkidle' });
  await p.evaluate(() => window.scrollTo({ top: 1100, behavior: 'instant' })); await p.waitForTimeout(250);
  const arrow = p.locator('[data-prompt-toggle] svg');
  const start = await arrow.boundingBox();
  await p.locator('[data-prompt-toggle]').click(); await p.waitForTimeout(250);
  const mid = await arrow.boundingBox();
  const opacity = await p.locator('[data-nav-area]').evaluate(el => Number(getComputedStyle(el).opacity));
  assert.ok(opacity > .05 && opacity < .95, `Toolbar finished too early: ${opacity}`);
  await p.waitForTimeout(800); const end = await arrow.boundingBox();
  assert.ok(start.x < mid.x && mid.x < end.x - 30);
  await p.locator('[data-prompt-toggle]').click(); await p.waitForTimeout(250);
  const back = await arrow.boundingBox();
  assert.ok(back.x < end.x && back.x > start.x + 30);
  await p.waitForTimeout(800); const restored = await arrow.boundingBox();
  assert.ok(Math.abs(restored.x - start.x) < 2);
  await c.close();
});
await browser.close();
await writeFile('test-results/suggestions.json', JSON.stringify(results, null, 2));
for (const r of results) console.log(`${r.passed ? 'PASS' : 'FAIL'} ${r.name}${r.error ? ': ' + r.error : ''}`);
if (results.some(r => !r.passed)) process.exitCode = 1;
