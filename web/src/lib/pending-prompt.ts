export type PendingPrompt = {
  readonly prompt: string;
  readonly requestId: string;
  start(work: (signal: AbortSignal) => Promise<void>): Promise<void>;
  cancel(): void;
};

/** Одна попытка на намеренный submit. Повтор effect возвращает тот же результат, включая ошибку. */
export function createPendingPrompt(prompt: string, requestId: string): PendingPrompt {
  const controller = new AbortController();
  let result: Promise<void> | undefined;
  return {
    prompt,
    requestId,
    start(work) {
      // Promise присваивается синхронно, до CAPTCHA, сети и повторного effect.
      result ??= Promise.resolve().then(() => {
        controller.signal.throwIfAborted();
        return work(controller.signal);
      });
      return result;
    },
    cancel() { controller.abort(); },
  };
}
