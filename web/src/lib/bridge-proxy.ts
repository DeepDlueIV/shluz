/** Server-side only: same-origin guest API -> protected, temporary HTTPS tunnel. */
const MAX_BODY_BYTES = 32 * 1024;
const allowed = new Set([
  'POST /v1/web/session',
  'POST /v1/web/chat/completions',
  'GET /v1/web/me',
  'GET /v1/web/tokens',
  'POST /v1/web/tokens',
  'POST /v1/web/tokens/revoke',
  'POST /v1/web/authorize/cherry',
  'POST /v1/web/token',
]);

type Env = {
  NEXT_PUBLIC_GATEWAY_ENABLED?: string;
  SHLUZ_BRIDGE_ORIGIN?: string;
  SHLUZ_BRIDGE_USERNAME?: string;
  SHLUZ_BRIDGE_PASSWORD?: string;
};

function responseError(status: number, code: string): Response {
  return Response.json({ error: { code } }, {
    status,
    headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
  });
}

function secureOrigin(env: Env): string | null {
  if (env.NEXT_PUBLIC_GATEWAY_ENABLED !== 'true'
      || !env.SHLUZ_BRIDGE_ORIGIN || !env.SHLUZ_BRIDGE_USERNAME || !env.SHLUZ_BRIDGE_PASSWORD
      || env.SHLUZ_BRIDGE_PASSWORD.length < 24) return null;
  try {
    const url = new URL(env.SHLUZ_BRIDGE_ORIGIN);
    // Secret configuration, not user input. No localhost, raw IP, paths or insecure URLs.
    if (url.protocol !== 'https:' || url.username || url.password || url.port
        || url.pathname !== '/' || url.search || url.hash || !url.hostname.includes('.')
        || /^(?:localhost|\d+\.\d+\.\d+\.\d+)$/.test(url.hostname)) return null;
    return url.origin;
  } catch { return null; }
}

async function readLimitedBody(request: Request): Promise<Uint8Array<ArrayBuffer> | null> {
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  let length = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_BODY_BYTES) { await reader.cancel(); return null; }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let position = 0;
  for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.byteLength; }
  return bytes;
}

/** No authentication secrets, upstream URL, administrative routes or caller-defined hosts reach the browser. */
export async function forwardGuestRequest(
  request: Request,
  env: Env = process.env as Env,
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  const url = new URL(request.url);
  if (url.search || !allowed.has(request.method + ' ' + url.pathname)) return responseError(404, 'not_found');
  const origin = secureOrigin(env);
  if (!origin) return responseError(503, 'web_not_configured');
  if (request.method === 'POST' && request.headers.get('origin') !== url.origin) {
    return responseError(403, 'origin_rejected');
  }
  if (request.headers.get('sec-fetch-site') === 'cross-site') return responseError(403, 'origin_rejected');
  if (request.method === 'POST' && request.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'application/json') {
    return responseError(415, 'invalid_request');
  }
  const declaredLength = Number(request.headers.get('content-length'));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) return responseError(413, 'body_too_large');
  let body: Uint8Array<ArrayBuffer> | null = new Uint8Array(0);
  if (request.method === 'POST') {
    try { body = await readLimitedBody(request); } catch { return responseError(400, 'invalid_request'); }
    if (!body) return responseError(413, 'body_too_large');
  }

  const headers = new Headers({
    Accept: request.method === 'GET' || url.pathname.endsWith('/session') ? 'application/json' : 'text/event-stream',
    'Authorization': 'Basic ' + Buffer.from(env.SHLUZ_BRIDGE_USERNAME + ':' + env.SHLUZ_BRIDGE_PASSWORD).toString('base64'),
  });
  if (request.method === 'POST') {
    headers.set('Content-Type', 'application/json');
    headers.set('Origin', request.headers.get('origin')!);
    if (request.headers.has('x-csrf-token')) headers.set('X-CSRF-Token', request.headers.get('x-csrf-token')!);
  }
  if (request.headers.has('cookie')) headers.set('Cookie', request.headers.get('cookie')!);

  let upstream: Response;
  try {
    upstream = await fetcher(origin + url.pathname, {
      method: request.method,
      headers,
      body: request.method === 'POST' ? body : undefined,
      cache: 'no-store',
      redirect: 'manual',
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(110_000)]),
    });
  } catch {
    return responseError(503, 'demo_temporarily_unavailable');
  }
  if (upstream.status >= 300 && upstream.status < 400) return responseError(502, 'invalid_response');
  const kind = upstream.headers.get('content-type')?.toLowerCase() ?? '';
  if (!kind.includes('application/json') && !(url.pathname.endsWith('/chat/completions') && upstream.ok && kind.includes('text/event-stream'))) {
    // Includes ngrok free-tier HTML warning pages and authentication failures.
    return responseError(502, 'invalid_response');
  }
  const safeHeaders = new Headers({
    'Content-Type': kind,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  for (const cookie of upstream.headers.getSetCookie?.() ?? []) {
    // The gateway contract sets only host-bound secure cookies without Domain.
    if (cookie.startsWith('__Host-shluz_web=') && !/;\s*Domain=/i.test(cookie)) safeHeaders.append('Set-Cookie', cookie);
  }
  // Streaming response.body is passed through as-is: no buffering or automatic retry.
  return new Response(upstream.body, { status: upstream.status, headers: safeHeaders });
}
