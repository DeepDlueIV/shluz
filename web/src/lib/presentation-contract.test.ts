import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const component = (name: string) => readFileSync(new URL(`../components/${name}`, import.meta.url), "utf8");

test("Language trigger uses a globe; flags remain inside language options", () => {
  const source = component("LanguageSwitcher.tsx");
  assert.match(source, /data-language-globe/);
  assert.match(source, /data-language-flag/);
  assert.doesNotMatch(source, /<Flag locale=\{locale\}/);
});
test("Background requests autoplay without an opt-in button", () => {
  const source = component("HeroVideo.tsx");
  assert.doesNotMatch(source, /Enable animation|enableAnimation|ambientMotion/);
  assert.match(source, /autoPlay/);
});
test("Docked composer has a distinct navigation toggle and prompt preview", () => {
  const source = component("HomePage.tsx");
  assert.match(source, /data-prompt-toggle/);
  assert.match(source, /data-panel-mode/);
  assert.match(source, /data-hero-logo/);
  assert.match(source, /PromptPreview/);
});
test("Model presentation does not disclose an upstream brand or source link", () => {
  assert.doesNotMatch(component("LandingSections.tsx"), /venice|modelCatalogSource/i);
  assert.match(component("LandingSections.tsx"), /modelIcons/);
});
