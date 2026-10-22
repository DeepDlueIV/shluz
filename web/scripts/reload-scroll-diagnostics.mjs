import { chromium } from 'playwright';
import { writeFile } from 'node:fs/promises';
const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
const browser = await chromium.launch();
const output = [];
for (const variant of ['trace', 'no-smooth', 'no-anchor', 'keep-manual', 'layout']) {
  const context = await browser.newContext({viewport:{width:1440,height:1000}, reducedMotion:'no-preference'});
  const page = await context.newPage();
  await page.addInitScript(variant => {
    const nativeEntries = performance.getEntriesByType.bind(performance);
    const trace = [];
    window.reloadTrace = trace;
    const record = (event, extra={}) => trace.push({event, t:performance.now(), ready:document.readyState, ...extra});
    const scroll = window.scrollTo.bind(window);
    window.scrollTo = function(...args) { record('scrollTo', {args, stack:new Error().stack}); return scroll(...args); };
    const descriptor = Object.getOwnPropertyDescriptor(History.prototype, 'scrollRestoration');
    Object.defineProperty(history, 'scrollRestoration', {get:()=>descriptor.get.call(history), set:value=>{
      record('restoration:'+value);
      if (variant !== 'keep-manual' || value !== 'auto') descriptor.set.call(history,value);
    }});
    const isReload = () => nativeEntries('navigation')[0]?.type === 'reload';
    document.addEventListener('DOMContentLoaded', () => {
      record('DOMContentLoaded');
      if (!isReload()) return;
      if (variant === 'no-smooth') document.documentElement.style.scrollBehavior = 'auto';
      if (variant === 'no-anchor') document.documentElement.style.overflowAnchor = 'none';
      if (variant === 'layout') document.documentElement.getBoundingClientRect();
    });
    addEventListener('scroll', () => record('scroll', {y:scrollY}), {passive:true});
    addEventListener('pageshow', () => {
      record('pageshow');
      document.fonts.ready.then(() => record('fonts-ready'));
      let count=0;
      const frame = () => { record('frame', {y:scrollY, mode:descriptor.get.call(history)}); if (++count<90) requestAnimationFrame(frame); };
      requestAnimationFrame(frame);
    });
  }, variant);
  await page.goto(origin, {waitUntil:'networkidle'});
  await page.evaluate(()=>window.scrollTo({top:2800,behavior:'instant'}));
  await page.waitForTimeout(250);
  await page.reload({waitUntil:'networkidle'});
  await page.waitForTimeout(1200);
  const result = await page.evaluate(()=>({trace:window.reloadTrace,y:scrollY,fonts:document.fonts.status, active:document.activeElement?.tagName}));
  output.push({variant,...result});
  console.log(variant, result.y);
  await context.close();
}
await writeFile('test-results/reload-scroll-variants.json',JSON.stringify(output,null,2));
await browser.close();
