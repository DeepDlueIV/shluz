import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch();
const results = [], recordings = {};
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(8000);
const check = async (name, run) => { try { await run(); results.push({ name, passed: true }); } catch (e) { results.push({ name, passed: false, error: String(e) }); } };
async function record(name, run) {
  await page.evaluate(() => {
    window.hoverFrames = []; window.hoverRecording = true;
    const nodes = new WeakMap(); let serial = 0;
    function frame(time) {
      if (!window.hoverRecording) return;
      const spans = [...document.querySelectorAll('[data-prompt-preview] span')];
      window.hoverFrames.push({ time, nodes: spans.map(el => {
        if (!nodes.has(el)) nodes.set(el, ++serial);
        const s = getComputedStyle(el);
        return { node: nodes.get(el), text: el.textContent, opacity: Number(s.opacity), x: new DOMMatrix(s.transform).m41 };
      }) });
      requestAnimationFrame(frame);
    } requestAnimationFrame(frame);
  });
  await run();
  const frames = await page.evaluate(() => { window.hoverRecording = false; return window.hoverFrames; });
  recordings[name] = frames;
  return frames;
}
const hover = id => page.locator(`[data-suggestion="${id}"]`).hover();
await page.goto(origin, { waitUntil: 'networkidle' });
await check('One stable preview layer survives rapid hover changes and re-entry', async () => {
  const frames = await record('rapid', async () => {
    await hover('surveillance'); await page.waitForTimeout(1000);
    await hover('drm'); await page.waitForTimeout(90);
    await hover('surveillance'); await page.waitForTimeout(130);
    await hover('cannibalism'); await page.waitForTimeout(130);
    await hover('drm'); await page.waitForTimeout(1100);
    await page.mouse.move(10, 200); await page.waitForTimeout(260);
    await hover('drm'); await page.waitForTimeout(1200);
  });
  assert.ok(frames.every(f => f.nodes.length === 1), 'The preview disappears or overlaps during a transition');
  let falling = false;
  for (let i = 1; i < frames.length; i++) {
    const a = frames[i - 1].nodes[0], b = frames[i].nodes[0];
    if (a.text !== b.text || b.opacity < .05) falling = false;
    else if (b.opacity < a.opacity - .002) falling = true;
    else if (falling && b.opacity > a.opacity + .002 && a.opacity > .1) {
      assert.fail('An outgoing preview becomes visible again before the fade finishes');
    }
  }
  const identities = new Set(frames.flatMap(f => f.nodes.map(n => n.node)));
  assert.equal(identities.size, 1, `Text was replaced ${identities.size - 1} times instead of reusing the animation layer`);
  const jumps = frames.slice(1).filter((f, i) => {
    const a = frames[i].nodes[0], b = f.nodes[0];
    return a && b && f.time - frames[i].time < 45 && a.text === b.text && Math.abs(a.opacity - b.opacity) > .45;
  });
  assert.equal(jumps.length, 0, 'Opacity jumps instead of continuing smoothly');
  assert.match(await page.locator('[data-prompt-preview]').innerText(), /DRM/i);
  assert.equal(await page.locator('#hero-prompt').inputValue(), '');
});
await check('Stationary hover does not replay the animation after it has settled', async () => {
  await hover('surveillance'); await page.waitForTimeout(1200);
  const box = await page.locator('[data-suggestion="surveillance"]').boundingBox();
  const frames = await record('stationary', async () => {
    for (let i = 0; i < 10; i++) { await page.mouse.move(box.x + box.width / 2 + i % 3, box.y + box.height / 2); await page.waitForTimeout(80); }
  });
  assert.ok(frames.every(f => f.nodes.length === 1 && f.nodes[0].opacity > .99 && Math.abs(f.nodes[0].x) < .1));
});
await browser.close();
await writeFile('test-results/hover-continuity.json', JSON.stringify({ results, recordings }, null, 2));
for (const r of results) console.log(`${r.passed ? 'PASS' : 'FAIL'} ${r.name}${r.error ? ': ' + r.error : ''}`);
if (results.some(r => !r.passed)) process.exitCode = 1;
