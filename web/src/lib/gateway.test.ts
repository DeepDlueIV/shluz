import assert from "node:assert/strict";
import test from "node:test";
import {
  GatewayError,
  getGatewayMessage,
  readChatStream,
  startGuestSession,
  streamGuestCompletion,
} from "./gateway.ts";

const encoder = new TextEncoder();

function byteStream(value: string, oneByte = false): ReadableStream<Uint8Array> {
  const bytes = encoder.encode(value);
  return new ReadableStream({
    start(controller) {
      if (oneByte) {
        for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      } else {
        controller.enqueue(bytes);
      }
      controller.close();
    },
  });
}

function response(value: string, oneByte = false) {
  return new Response(byteStream(value, oneByte), {
    headers: { "Content-Type": "text/event-stream; charset=utf-8" },
  });
}

const validStream =
  ': keepalive\r\n\r\ndata: {"choices":[{"delta":{"content":"Привет 👋"}}]}\r\n\r\n' +
  'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}\n\n' +
  'data: {"choices":[],"usage":null}\n\n' +
  "data: [DONE]\n\n";

test("SSE: собирает UTF-8 и CRLF-события, разрезанные между любыми байтами", async () => {
  let text = "";
  await readChatStream(response(validStream, true), (delta) => { text += delta; });
  assert.equal(text, "Привет 👋!");
});

test("SSE: EOF без [DONE] сохраняет префикс, но не считается успехом", async () => {
  let text = "";
  await assert.rejects(
    readChatStream(response('data: {"choices":[{"delta":{"content":"Часть"}}]}\n\n'), (delta) => { text += delta; }),
    (error: unknown) => error instanceof GatewayError && error.code === "stream_interrupted",
  );
  assert.equal(text, "Часть");
});

test("SSE: error завершает чтение ошибкой даже перед последующим [DONE]", async () => {
  await assert.rejects(
    readChatStream(response('data: {"error":{"code":"demo_exhausted","message":"private upstream text"}}\n\ndata: [DONE]\n\n'), () => {}),
    (error: unknown) => error instanceof GatewayError && error.code === "demo_exhausted" && !error.message.includes("private"),
  );
});

test("SSE: повреждённый JSON не превращается в успешный пустой ответ", async () => {
  await assert.rejects(
    readChatStream(response("data: {broken}\n\ndata: [DONE]\n\n"), () => {}),
    (error: unknown) => error instanceof GatewayError && error.code === "stream_interrupted",
  );
});

test("SSE: Stop прерывает ожидание следующего фрагмента и закрывает reader", async () => {
  let cancelled = false;
  const controller = new AbortController();
  const stream = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } });
  const result = readChatStream(new Response(stream, { headers: { "Content-Type": "text/event-stream" } }), () => {}, controller.signal);
  controller.abort();
  await assert.rejects(result, (error: unknown) => error instanceof Error && error.name === "AbortError");
  assert.equal(cancelled, true);
});

test("chat: отправляет только гостевой payload, cookie и CSRF на правильный endpoint", async () => {
  let observed: { input: RequestInfo | URL; init?: RequestInit } | undefined;
  let text = "";
  const fetcher: typeof fetch = async (input, init) => {
    observed = { input, init };
    return response(validStream);
  };
  await streamGuestCompletion({
    prompt: "Вопрос", requestId: "bd8bbc45-521c-4658-a10c-69e8fb1c253b",
    csrfToken: "csrf", turnstileToken: "captcha", onText: (delta) => { text += delta; },
    signal: new AbortController().signal,
  }, fetcher);
  assert.equal(observed?.input, "/v1/web/chat/completions");
  assert.equal(observed?.init?.method, "POST");
  assert.equal(observed?.init?.credentials, "include");
  assert.equal(new Headers(observed?.init?.headers).get("X-CSRF-Token"), "csrf");
  assert.deepEqual(JSON.parse(String(observed?.init?.body)), {
    prompt: "Вопрос", request_id: "bd8bbc45-521c-4658-a10c-69e8fb1c253b", turnstile_token: "captcha",
  });
  assert.equal(text, "Привет 👋!");
});

test("chat: 503 web_not_configured даёт честное сообщение и не повторяет POST", async () => {
  let calls = 0;
  const fetcher: typeof fetch = async () => {
    calls += 1;
    return Response.json({ error: { code: "web_not_configured", message: "server implementation detail" } }, { status: 503 });
  };
  await assert.rejects(
    streamGuestCompletion({ prompt: "Вопрос", requestId: "id", csrfToken: "csrf", turnstileToken: "token", signal: new AbortController().signal, onText: () => {} }, fetcher),
    (error: unknown) => {
      const message = getGatewayMessage(error);
      return message.code === "web_not_configured" && message.message === "The demo is not connected yet. Your prompt remains in this tab.";
    },
  );
  assert.equal(calls, 1);
});

test("session: читает действующий DTO, cookie session не получает Bearer-ключ", async () => {
  const dto = { csrf_token: "csrf", authenticated: false, expires_at: 1792512000, captcha_sitekey: "public-sitekey", demo: { requests: 1, max_input_chars: 3000, max_output_tokens: 512 } };
  let request: RequestInit | undefined;
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(input, "/v1/web/session");
    request = init;
    return Response.json(dto);
  };
  assert.deepEqual(await startGuestSession(undefined, fetcher), dto);
  assert.equal(request?.credentials, "include");
  assert.equal(new Headers(request?.headers).has("Authorization"), false);
});

test("ошибки: исчерпание демо объясняет необходимость постоянного аккаунта", () => {
  const message = getGatewayMessage(new GatewayError("demo_exhausted", 429));
  assert.equal(message.title, "Demo limit reached");
  assert.match(message.message, /permanent account/);
  assert.doesNotMatch(message.message, /successfully registered/);
});
