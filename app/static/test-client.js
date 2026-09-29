"use strict";
const form = document.getElementById("test-form");
const token = document.getElementById("test-token");
const message = document.getElementById("test-message");
const send = document.getElementById("test-send");
const heading = document.getElementById("test-status");
const output = document.getElementById("test-output");
const requestId = document.getElementById("test-request-id");
const errors = {
    request_limit_exceeded: "Лимит запросов исчерпан. Для третьего запроса при лимите 2 это ожидаемый результат.",
    subscription_required: "У пользователя нет действующего тарифа. Назначьте тариф в разделе «Пользователи».",
    credit_limit_exceeded: "Недостаточно внутренних кредитов по тарифу.",
    spend_limit_exceeded: "Достигнут лимит расходов по тарифу.",
    model_not_found: "Тестовая модель не найдена. Проверьте, что включён режим mock.",
};
// Не допускаем обычную отправку формы или токена в адресную строку.
form.addEventListener("submit", event => event.preventDefault());
window.addEventListener("pagehide", () => { token.value = ""; });
send.addEventListener("click", async () => {
    if (!form.reportValidity() || send.disabled) return;
    const key = token.value.trim();
    if (!key.startsWith("shluz_")) {
        heading.textContent = "Нужен личный токен";
        output.textContent = "Вставьте токен shluz_… из карточки пользователя, не пароль администратора.";
        return;
    }
    send.disabled = true;
    heading.textContent = "Отправляем запрос…";
    output.textContent = "";
    requestId.textContent = "";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
        const response = await fetch("/v1/chat/completions", {
            method: "POST", credentials: "omit", cache: "no-store", signal: controller.signal,
            headers: {"Authorization": `Bearer ${key}`, "Content-Type": "application/json"},
            body: JSON.stringify({model: "mock-chat", messages: [{role: "user", content: message.value}]}),
        });
        const data = await response.json();
        heading.textContent = response.ok ? "Запрос выполнен" : `Запрос отклонён (${response.status})`;
        const id = response.headers.get("X-Request-ID");
        requestId.textContent = id ? `Номер запроса для технического журнала: ${id}` : "";
        if (response.ok) {
            output.textContent = data.choices?.[0]?.message?.content || "Ответ без текста";
        } else if (response.status === 401) {
            output.textContent = "Токен неверный, отозван или пользователь заблокирован. Проверьте личный доступ.";
        } else {
            output.textContent = errors[data.error?.code] || "Не удалось выполнить запрос. Проверьте тариф и технический журнал.";
        }
    } catch (_) {
        heading.textContent = "Ответ не получен";
        output.textContent = "Проверьте, запущен ли Shluz. Запрос мог успеть выполниться: сначала посмотрите журнал расходов, затем повторяйте.";
    } finally {
        clearTimeout(timeout);
        send.disabled = false;
    }
});
