export type GuestSession = {
  csrf_token: string;
  authenticated: boolean;
  expires_at: number;
  captcha_sitekey: string;
  demo: { requests: number; max_input_chars: number; max_output_tokens: number };
};

export type GatewayMessage = { code: string; title: string; message: string };

const unavailable = {
  title: "Демо пока недоступно",
  message: "Демонстрация временно недоступна. Ваш запрос сохранён.",
};
const sessionExpired = {
  title: "Обновите страницу",
  message: "Сессия завершилась. Скопируйте вопрос перед обновлением страницы и отправьте его снова, когда будете готовы.",
};
const verificationFailed = {
  title: "Проверка не завершена",
  message: "Не удалось подтвердить проверку. Вопрос сохранён. Вы можете отправить его ещё раз вручную.",
};
const messages: Record<string, Omit<GatewayMessage, "code">> = {
  web_not_configured: unavailable,
  demo_closed: unavailable,
  model_not_allowed: unavailable,
  model_selection_unavailable: unavailable,
  demo_temporarily_unavailable: unavailable,
  invalid_response: unavailable,
  demo_exhausted: {
    title: "Демо исчерпано",
    message: "Лимит пробного доступа исчерпан. Для продолжения понадобится постоянный аккаунт.",
  },
  session_required: sessionExpired,
  session_expired: sessionExpired,
  session_changed: sessionExpired,
  csrf_rejected: sessionExpired,
  origin_rejected: unavailable,
  cross_site_rejected: unavailable,
  access_unavailable: { title: "Доступ ограничен", message: "Сейчас этот аккаунт не может отправлять запросы. Ваш вопрос сохранён." },
  verification_required: verificationFailed,
  verification_failed: verificationFailed,
  verification_reused: verificationFailed,
  verification_unavailable: { title: "Не удалось загрузить проверку", message: "Проверьте подключение к интернету и разрешите проверку браузера. Ваш вопрос сохранён." },
  input_too_large: { title: "Слишком длинный вопрос", message: "Сократите вопрос до указанного числа символов и отправьте его снова." },
  body_too_large: { title: "Слишком длинный вопрос", message: "Сократите вопрос: его размер превышает допустимый для демонстрации." },
  empty_prompt: { title: "Добавьте вопрос", message: "Напишите, с чем вам помочь." },
  invalid_request: { title: "Не удалось отправить вопрос", message: "Проверьте текст вопроса и попробуйте отправить его вручную." },
  too_many_requests: { title: "Нужна небольшая пауза", message: "Сейчас слишком много попыток. Подождите немного перед новой отправкой." },
  demo_busy: { title: "Все места временно заняты", message: "Попробуйте отправить вопрос немного позже. Он сохранён здесь." },
  request_already_used: { title: "Запрос уже отправлен", message: "Эта попытка уже использована. Повторно она не отправлялась." },
  stream_interrupted: { title: "Ответ прервался", message: "Связь прервалась до завершения ответа. Полученная часть сохранена. Попытка могла быть использована; автоматической повторной отправки не было." },
  network_error: { title: "Не удалось связаться с сервисом", message: "Проверьте подключение к интернету. Вопрос сохранён; запрос не будет повторён автоматически." },
};

export class GatewayError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, status = 0) {
    super((messages[code] ?? unavailable).message);
    this.name = "GatewayError";
    this.code = code;
    this.status = status;
  }
}

export function getGatewayMessage(error: unknown): GatewayMessage {
  const code = error instanceof GatewayError ? error.code : "network_error";
  return { code, ...(messages[code] ?? unavailable) };
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

async function requireSuccess(response: Response): Promise<void> {
  if (response.ok) return;
  const body: unknown = await response.json().catch(() => null);
  const code = record(body) && record(body.error) && typeof body.error.code === "string"
    ? body.error.code
    : "invalid_response";
  // Текст сервера может содержать внутренние детали; показываем только свою копию.
  throw new GatewayError(code, response.status);
}

export async function startGuestSession(
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<GuestSession> {
  const response = await fetcher("/v1/web/session", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: "{}",
    cache: "no-store",
    signal,
  });
  await requireSuccess(response);
  const data: unknown = await response.json().catch(() => null);
  if (!record(data) || typeof data.csrf_token !== "string" || !data.csrf_token
    || typeof data.authenticated !== "boolean" || typeof data.expires_at !== "number"
    || typeof data.captcha_sitekey !== "string" || !record(data.demo)
    || !Number.isSafeInteger(data.demo.requests) || Number(data.demo.requests) < 0
    || !Number.isSafeInteger(data.demo.max_input_chars) || Number(data.demo.max_input_chars) < 1
    || !Number.isSafeInteger(data.demo.max_output_tokens) || Number(data.demo.max_output_tokens) < 1) {
    throw new GatewayError("invalid_response");
  }
  return data as GuestSession;
}

export async function readGuestRemaining(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<number | null> {
  const response = await fetcher("/v1/web/me", { credentials: "include", cache: "no-store", signal });
  await requireSuccess(response);
  const data: unknown = await response.json().catch(() => null);
  if (record(data) && record(data.allowance) && record(data.allowance.requests)) {
    const remaining = data.allowance.requests.remaining;
    if (typeof remaining === "number" && Number.isSafeInteger(remaining) && remaining >= 0) return remaining;
  }
  return null;
}

export type GuestCompletionInput = {
  prompt: string;
  requestId: string;
  csrfToken: string;
  turnstileToken: string;
  signal: AbortSignal;
  onText: (text: string) => void;
};

export async function streamGuestCompletion(input: GuestCompletionInput, fetcher: typeof fetch = fetch): Promise<void> {
  const body = JSON.stringify({
    prompt: input.prompt,
    request_id: input.requestId,
    turnstile_token: input.turnstileToken,
  });
  if (new TextEncoder().encode(body).byteLength > 32768) throw new GatewayError("body_too_large");
  const response = await fetcher("/v1/web/chat/completions", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "X-CSRF-Token": input.csrfToken,
    },
    body,
    signal: input.signal,
    cache: "no-store",
  });
  await requireSuccess(response);
  await readChatStream(response, input.onText, input.signal);
}

/** Чтение по строкам сохраняет UTF-8, CRLF и SSE-кадры между сетевыми фрагментами. */
export async function readChatStream(
  response: Response,
  onText: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body || !response.headers.get("Content-Type")?.toLowerCase().includes("text/event-stream")) {
    throw new GatewayError("invalid_response");
  }
  signal?.throwIfAborted();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let dataLines: string[] = [];
  let completed = false;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener("abort", cancel, { once: true });

  const dispatch = () => {
    if (dataLines.length === 0) return;
    const data = dataLines.join("\n");
    dataLines = [];
    if (data === "[DONE]") { completed = true; return; }
    let event: unknown;
    try { event = JSON.parse(data); }
    catch { throw new GatewayError("stream_interrupted"); }
    if (!record(event)) throw new GatewayError("stream_interrupted");
    if (record(event.error)) {
      throw new GatewayError(typeof event.error.code === "string" ? event.error.code : "stream_interrupted");
    }
    if (!Array.isArray(event.choices)) throw new GatewayError("stream_interrupted");
    for (const choice of event.choices) {
      if (record(choice) && record(choice.delta) && typeof choice.delta.content === "string") onText(choice.delta.content);
    }
  };

  const consumeLines = (eof = false) => {
    while (!completed) {
      const index = buffer.search(/[\r\n]/);
      if (index < 0 || (!eof && buffer[index] === "\r" && index === buffer.length - 1)) return;
      const line = buffer.slice(0, index);
      const delimiterLength = buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
      buffer = buffer.slice(index + delimiterLength);
      if (line === "") dispatch();
      else if (line === "data" || line.startsWith("data:")) {
        const value = line === "data" ? "" : line.slice(5);
        dataLines.push(value.startsWith(" ") ? value.slice(1) : value);
      }
    }
  };

  try {
    while (!completed) {
      const { value, done } = await reader.read();
      signal?.throwIfAborted();
      if (done) {
        buffer += decoder.decode();
        consumeLines(true);
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      consumeLines();
    }
    if (!completed) throw new GatewayError("stream_interrupted");
  } catch (error) {
    signal?.throwIfAborted();
    if (error instanceof GatewayError) throw error;
    throw new GatewayError("stream_interrupted");
  } finally {
    signal?.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
