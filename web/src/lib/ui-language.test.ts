import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { GatewayError, getGatewayMessage } from "./gateway.ts";

 test("English is the default for the document and guest error messages", () => {
  assert.equal(getGatewayMessage(new GatewayError("web_not_configured")).title, "Demo unavailable");
  assert.match(readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8"), /<html lang="en"/);
});

 test("Russian remains available for guest errors", () => {
  assert.equal(getGatewayMessage(new GatewayError("demo_exhausted"), "ru").title, "Демо исчерпано");
});

 test("Raw upstream error details are never displayed", () => {
  for (const locale of ["en", "ru"] as const) {
    const message = getGatewayMessage(new Error("private server exception"), locale);
    assert.doesNotMatch(message.message, /private server exception/);
  }
});


test("guest copy never exposes the numeric free-request allowance in either language", () => {
  const component = readFileSync(new URL("../components/GuestChat.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(component, /Requests remaining|Осталось запросов/);
});
