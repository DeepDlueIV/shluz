import { chromium } from 'playwright';
import { writeFile } from 'node:fs/promises';
const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
const browser = await chromium.launch();
const page = await browser.newPage();
await page.addInitScript(() => {
  const entries = performance.getEntriesByType.bind(performance);
  window.reloadTrace = [];
  const record = event => window.reloadTrace.push({ event, y: scrollY, type: entries('navigation')[0]?.type, legacyType: performance.navigation?.type, hash: location.hash, mode: history.scrollRestoration, ready: document.readyState });
  performance.getEntriesByType = function(type) { if (type === 'navigation') record('read-navigation'); return entries(type); };
  const scroll = window.scrollTo.bind(window);
  window.scrollTo = function(...args) { record('scrollTo:' + JSON.stringify(args)); return scroll(...args); };
  const descriptor = Object.getOwnPropertyDescriptor(History.prototype, 'scrollRestoration');
  Object.defineProperty(history, 'scrollRestoration', { get: () => descriptor.get.call(history), set: value => { record('restoration:' + value); descriptor.set.call(history, value); } });
  record('init');
  document.addEventListener('DOMContentLoaded', () => record('DOMContentLoaded'));
  addEventListener('pageshow', () => { record('pageshow'); setTimeout(() => record('after-pageshow'), 100); });
});
const output = [];
for (const hash of ['', '#features']) {
  await page.goto(origin + '/' + hash, { waitUntil: 'networkidle' });
  await page.evaluate(() => window.scrollTo({ top: 2800, behavior: 'instant' }));
  await page.waitForTimeout(300);
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  output.push(await page.evaluate(() => ({ trace: window.reloadTrace, y: scrollY, hash: location.hash, script: document.getElementById('homepage-reload-scroll')?.textContent })));
}
await writeFile('test-results/reload-scroll-diagnostics.json', JSON.stringify(output, null, 2));
await browser.close();
