import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { getPromptSuggestions } from './prompt-suggestions.ts';
const source = (file: string) => readFileSync(new URL(file, import.meta.url), 'utf8');

test('Suggestion chips have readable white semibold text and actual backdrop blur', () => {
  const css = source('../components/PromptSuggestions.module.css');
  assert.match(css, /color:\s*#fff(?:;|\s)/);
  assert.match(css, /font-weight:\s*600/);
  assert.match(css, /font-size:\s*13px/);
  assert.match(css, /backdrop-filter:\s*blur\(16px\)/);
  assert.match(css, /-webkit-backdrop-filter:\s*blur\(16px\)/);
});
test('Compact suggestion rows keep one-line labels and a shared intrinsic width', () => {
  const css = source('../components/PromptSuggestions.module.css');
  assert.match(css, /width:\s*max-content/);
  assert.match(css, /contain:\s*inline-size/);
  assert.match(css, /white-space:\s*nowrap/);
  assert.match(css, /text-overflow:\s*ellipsis/);
  assert.doesNotMatch(css, /overflow-wrap:\s*anywhere/);
});
test('Prompt preview fills the available field, without a character-width cap', () => {
  const css = source('../components/HomePage.module.css');
  assert.doesNotMatch(css, /52ch/);
  assert.match(css, /\.promptPreview span[^}]*width:\s*100%/);
  assert.match(css, /\.promptPreview span[^}]*text-overflow:\s*ellipsis/);
});
test('All display examples retain detailed full prompts in both languages', () => {
  for (const translate of [(en: string, _ru: string) => en, (_en: string, ru: string) => ru]) {
    const { primary, additional } = getPromptSuggestions(translate);
    for (const suggestion of [...primary, ...additional]) {
      assert.ok(suggestion.prompt.length >= 420, `${suggestion.id}: brief is too short`);
      assert.ok(suggestion.prompt.length < 3000, `${suggestion.id}: exceeds default demo input limit`);
      assert.ok(!suggestion.prompt.endsWith('…'), `${suggestion.id}: stored prompt was truncated`);
    }
  }
});
test('Interactive motion is noticeable even when the OS reduces ambient animation', () => {
  const home = source('../components/HomePage.tsx');
  const more = source('../components/PromptSuggestions.tsx');
  const language = source('../components/LanguageSwitcher.module.css');
  assert.match(home, /duration:\s*0\.8/);
  assert.match(home, /duration:\s*0\.5/);
  assert.match(home, /x:\s*-20/);
  assert.doesNotMatch(more, /useReducedMotion/);
  assert.match(more, /duration:\s*0\.5/);
  assert.match(language, /translateY\(-14px\)/);
  assert.match(language, /450ms/);
});
test('Choosing an example animates the input without truncating its value', () => {
  const home = source('../components/HomePage.tsx');
  assert.match(home, /setDraft\(prompt\)/);
  assert.match(home, /data-autofill/);
  assert.match(home, /setSelectionRange\(0,\s*0\)/);
});
