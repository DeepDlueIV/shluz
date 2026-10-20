// Настоящий @ai-sdk/openai-compatible@2.0.41 против изолированного HTTP Shluz.
import assert from "node:assert/strict";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
const baseURL = "http://127.0.0.1:18979/v1";
let postCount = 0;
const provider = createOpenAICompatible({
  name: "noclip", baseURL, apiKey: process.env.SDK_TEST_TOKEN, includeUsage: true,
  fetch: async (url, options) => {
    assert.equal(new URL(url).origin, "http://127.0.0.1:18979");
    assert.equal(new URL(url).pathname, "/v1/chat/completions");
    if (options.method === "POST") postCount++;
    return fetch(url, options);
  },
});
const model = provider.chatModel("mock-chat");
const tools = [{type: "function", name: "noclip_test_echo", description: "Echo text",
  inputSchema: {type: "object", properties: {text: {type: "string"}}, required: ["text"]}}];
const prompt = [{role: "user", content: [{type: "text", text: "Echo hello"}]}];
const first = await model.doStream({prompt, tools, toolChoice: {type: "auto"},
  maxOutputTokens: 64, providerOptions: {noclip: {reasoningEffort: "low"}}});
const events = [];
for await (const event of first.stream) {
  assert.notEqual(event.type, "error", "SDK returned an error event");
  events.push(event);
}
const call = events.find(e => e.type === "tool-call");
assert.ok(call);
assert.equal(call.toolName, "noclip_test_echo");
assert.equal(events.find(e => e.type === "finish").finishReason.unified, "tool-calls");
assert.ok(events.some(e => e.type === "tool-input-delta"));
// Локальный контролируемый инструмент исполняет тестовый клиент, не сервер.
const input = JSON.parse(call.input);
const result = String(input.text);
const final = await model.doGenerate({
  tools, toolChoice: {type: "auto"}, maxOutputTokens: 64,
  prompt: [...prompt,
    {role: "assistant", content: [{type: "tool-call", toolCallId: call.toolCallId,
      toolName: call.toolName, input}]},
    {role: "tool", content: [{type: "tool-result", toolCallId: call.toolCallId,
      toolName: call.toolName, output: {type: "text", value: result}}]},
  ],
});
assert.equal(final.finishReason.unified, "stop");
assert.ok(final.content.some(c => c.type === "text" && c.text === "Shluz mock tool result: hello"));
assert.equal(postCount, 2, "The agent round was replayed");
const usage = await fetch(baseURL + "/me/usage", {
  headers: {Authorization: "Bearer " + process.env.SDK_TEST_TOKEN},
}).then(r => r.json());
assert.equal(usage.data.length, 2);
assert.ok(usage.data.every(e => e.status === "success"));
console.log("Pinned JS SDK: streaming tool -> local echo -> JSON answer, two usage events: PASS");
