import assert from "node:assert/strict";
import test from "node:test";
import { createPendingPrompt } from "./pending-prompt.ts";
import { streamGuestCompletion } from "./gateway.ts";

test("две подписки на одну попытку запускают только один chat POST", async () => {
  const attempt = createPendingPrompt("Вопрос", "bd8bbc45-521c-4658-a10c-69e8fb1c253b");
  let posts = 0;
  let answer = "";
  const fetcher: typeof fetch = async () => {
    posts += 1;
    return new Response('data: {"choices":[{"delta":{"content":"Ответ"}}]}\n\ndata: [DONE]\n\n', { headers: { "Content-Type": "text/event-stream" } });
  };
  const work = (signal: AbortSignal) => streamGuestCompletion({
    prompt: attempt.prompt, requestId: attempt.requestId, csrfToken: "csrf", turnstileToken: "captcha", signal,
    onText: (delta) => { answer += delta; },
  }, fetcher);
  const first = attempt.start(work);
  const replay = attempt.start(work);
  assert.equal(first, replay);
  await Promise.all([first, replay]);
  await attempt.start(work);
  assert.equal(posts, 1);
  assert.equal(answer, "Ответ");
});

test("ошибка попытки остаётся ошибкой при повторном effect, без повторной отправки", async () => {
  const attempt = createPendingPrompt("Вопрос", "request-id");
  let posts = 0;
  const work = (signal: AbortSignal) => streamGuestCompletion({
    prompt: attempt.prompt, requestId: attempt.requestId, csrfToken: "csrf", turnstileToken: "captcha", signal, onText: () => {},
  }, async () => { posts += 1; throw new TypeError("network unavailable"); });
  await assert.rejects(attempt.start(work));
  await assert.rejects(attempt.start(work));
  assert.equal(posts, 1);
});

test("отмена до запуска запрещает последующий POST", async () => {
  const attempt = createPendingPrompt("Вопрос", "request-id");
  let started = false;
  attempt.cancel();
  await assert.rejects(attempt.start(async () => { started = true; }), (error: unknown) => error instanceof Error && error.name === "AbortError");
  assert.equal(started, false);
});

test("отмена текущей попытки доставляет AbortSignal активному запросу", async () => {
  const attempt = createPendingPrompt("Вопрос", "request-id");
  let observedSignal: AbortSignal | undefined;
  const result = attempt.start(async (signal) => {
    observedSignal = signal;
    await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
  });
  await Promise.resolve();
  attempt.cancel();
  await result;
  assert.equal(observedSignal?.aborted, true);
});
