import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const home = readFileSync(new URL("../components/HomePage.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../components/HomePage.module.css", import.meta.url), "utf8");
const language = readFileSync(new URL("../components/LanguageSwitcher.tsx", import.meta.url), "utf8");

test("Language trigger is icon-only; menu keeps flags and locale choices", () => {
  assert.doesNotMatch(language, /locale\.toUpperCase\(\)/);
  assert.match(language, /data-language-globe/);
  assert.match(language, /<Flag locale=\{code\}/);
});

test("Prompt is logo-free with a private invitation in both locales", () => {
  const composer = home.split("<motion.form")[1].split("</motion.form>")[0];
  assert.doesNotMatch(composer, /<img|composerMark/);
  assert.match(home, /Ask me anything, privately…/);
  assert.match(home, /Спросите о чём угодно — приватно…/);
});

test("Hint transitions are sequential and slower; pointer gaps do not reset them", () => {
  assert.match(home, /mode="wait"/);
  assert.match(home, /duration: reduceMotion \? 0 : 0\.24/);
  assert.match(home, /onMouseLeave=\{\(\) => setPreview\(""\)\}/);
  const buttons = home.split("{examples.map")[1].split("</button>)}")[0];
  assert.doesNotMatch(buttons, /onMouseLeave/);
});

test("Toolbar animation and button share a calm 400ms transition", () => {
  assert.match(home, /duration: reduceMotion \? 0\.05 : 0\.4/);
  assert.match(css, /width 400ms cubic-bezier\(\.4,0,\.2,1\)/);
});
