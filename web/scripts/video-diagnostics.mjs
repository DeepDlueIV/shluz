import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' });
const origin = process.env.WEB_TEST_ORIGIN || 'http://127.0.0.1:3000';
for (let i=0;i<90;i++) { if(await fetch(origin).then(r=>r.ok).catch(()=>false))break; await new Promise(r=>setTimeout(r,1000)); }
const network=[];
page.on('response', r=>{ if(r.url().includes('.mp4')) network.push({url:r.url(),status:r.status(),headers:r.headers()}); });
await page.goto(origin,{waitUntil:'networkidle'});
await page.waitForTimeout(2000);
const inspect = () => page.evaluate(() => {
 const v=document.querySelector('[data-hero-video]');
 return {src:v.currentSrc,error:v.error && {code:v.error.code,message:v.error.message},time:v.currentTime,paused:v.paused,readyState:v.readyState,networkState:v.networkState,muted:v.muted,autoplay:v.autoplay,canPlay:v.canPlayType('video/mp4; codecs="avc1.640028"'),ambient:document.documentElement.dataset.ambientMotion,hidden:document.hidden,rect:v.getBoundingClientRect().toJSON()};
});
const before=await inspect();
const direct=await page.evaluate(async()=>{try{await document.querySelector('[data-hero-video]').play();return 'played';}catch(e){return String(e);}});
await page.waitForTimeout(1000);
const result={before,direct,after:await inspect(),network};
console.log(JSON.stringify(result,null,2));
await mkdir('test-results',{recursive:true});await writeFile('test-results/video-diagnostics.json',JSON.stringify(result,null,2));
await browser.close();
