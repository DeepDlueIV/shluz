import assert from 'node:assert/strict';
import test from 'node:test';
import { forwardGuestRequest } from './bridge-proxy.ts';

const base = 'https://noclip-web.vercel.app';
const env = {
  NEXT_PUBLIC_GATEWAY_ENABLED: 'true',
  SHLUZ_BRIDGE_ORIGIN: 'https://assigned.ngrok-free.app',
  SHLUZ_BRIDGE_USERNAME: 'noclip',
  SHLUZ_BRIDGE_PASSWORD: 'this-is-a-private-32-byte-value-not-an-api-token',
};
const request = (path: string, method='POST', body='{}', headers: Record<string,string> = {}) =>
  new Request(base + path, { method, ...(method === 'POST' ? {body} : {}), headers: { Origin: base, 'Content-Type':'application/json', ...headers } });

test('disabled or incomplete bridge is fail-closed and never contacts upstream', async () => {
  let count=0;
  const f=(async()=>{count++;return new Response('{}');}) as typeof fetch;
  for(const bad of [{ ...env, NEXT_PUBLIC_GATEWAY_ENABLED:'false' }, { ...env, SHLUZ_BRIDGE_ORIGIN:'' }, {...env, SHLUZ_BRIDGE_PASSWORD:''},{...env, SHLUZ_BRIDGE_ORIGIN:'http://127.0.0.1:8000'}]) {
    const r=await forwardGuestRequest(request('/v1/web/session'), bad, f);
    assert.equal(r.status,503);
  }
  assert.equal(count,0);
});

test('only precisely allowed guest routes and methods can pass', async () => {
  let called=0;
  const f=(async()=>{ called++; return new Response('{}',{headers:{'content-type':'application/json'}}); }) as typeof fetch;
  for (const [path,method] of [['/admin','GET'],['/v1/chat/completions','POST'],['/v1/web/models','GET'],['/v1/web/auth/otp/request','POST'],['/v1/web/me','POST'],['/v1/web/session','GET'],['/v1/web/me/usage','GET'],['/v1/web/session?debug=1','POST']]) {
    const result=await forwardGuestRequest(request(path,method),env,f);
    assert.equal(result.status,404,method + ' ' + path);
  }
  assert.equal(called,0);
});

test('rejects cross-origin posts and oversized bodies before contacting upstream', async () => {
  let count=0;
  const f=(async()=>{count++;return new Response('{}');}) as typeof fetch;
  assert.equal((await forwardGuestRequest(request('/v1/web/session','POST','{}',{Origin:'https://evil.example'}),env,f)).status,403);
  assert.equal((await forwardGuestRequest(request('/v1/web/chat/completions','POST','x'.repeat(32769)),env,f)).status,413);
  assert.equal(count,0);
});

test('forwards session request with server-side Basic auth, Origin, CSRF and client cookie', async () => {
  let received: Request | undefined;
  const f=(async(input: RequestInfo | URL, init?: RequestInit)=>{
    received=new Request(input,init);
    return new Response(JSON.stringify({csrf_token:'xxx'}),{status:200, headers:{'Content-Type':'application/json','Set-Cookie':'__Host-shluz_web=value; Secure; HttpOnly; Path=/; SameSite=Strict'}});
  }) as typeof fetch;
  const r=await forwardGuestRequest(request('/v1/web/session','POST','{}',{'Cookie':'__Host-shluz_web=old','X-CSRF-Token':'test'}), env, f);
  assert.equal(r.status,200);
  assert.equal(received?.url,'https://assigned.ngrok-free.app/v1/web/session');
  assert.equal(received?.headers.get('authorization'),'Basic '+Buffer.from(env.SHLUZ_BRIDGE_USERNAME+':'+env.SHLUZ_BRIDGE_PASSWORD).toString('base64'));
  assert.equal(received?.headers.get('origin'),base);
  assert.equal(received?.headers.get('cookie'),'__Host-shluz_web=old');
  assert.equal(received?.headers.get('x-csrf-token'),'test');
  assert.equal(r.headers.get('set-cookie'),'__Host-shluz_web=value; Secure; HttpOnly; Path=/; SameSite=Strict');
  assert.equal(r.headers.get('cache-control'),'no-store');
});

test('streams SSE chunks unchanged instead of buffering and preserves errors', async () => {
  const chunks=[new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'),new TextEncoder().encode('data: [DONE]\n\n')];
  let index=0;
  const source=new ReadableStream<Uint8Array>({pull(ctrl){if(index<chunks.length)ctrl.enqueue(chunks[index++]);else ctrl.close();}});
  const f=(async()=>new Response(source,{status:200,headers:{'content-type':'text/event-stream; charset=utf-8'}})) as typeof fetch;
  const result=await forwardGuestRequest(request('/v1/web/chat/completions'),env,f);
  assert.equal(result.status,200);
  assert.ok(result.body,'SSE body must be streamed');
  assert.equal(await result.text(),new TextDecoder().decode(chunks[0])+new TextDecoder().decode(chunks[1]));
});

test('rejects upstream redirects or injected HTML and masks connection failures', async () => {
  for (const response of [new Response('moved',{status:302,headers:{Location:'https://evil.example'}}),new Response('<html>interstitial</html>',{status:200,headers:{'content-type':'text/html'}})]) {
    const r=await forwardGuestRequest(request('/v1/web/session'),env,(async()=>response) as typeof fetch);
    assert.equal(r.status,502);
    assert.equal(r.headers.get('location'),null);
  }
  const result=await forwardGuestRequest(request('/v1/web/session'),env,(async()=>{throw Error('provider secret');}) as typeof fetch);
  assert.equal(result.status,503);
  assert.doesNotMatch(await result.text(),/provider secret/);
});
