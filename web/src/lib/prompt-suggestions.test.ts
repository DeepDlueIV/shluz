import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, readFileSync } from 'node:fs';

const catalogUrl = new URL('./prompt-suggestions.ts', import.meta.url);
const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('Suggestions: four primary choices and exactly ten More choices', async () => {
  assert.ok(existsSync(catalogUrl), 'Prompt catalog is not implemented');
  const { getPromptSuggestions } = await import('./prompt-suggestions.ts');
  const { primary, additional } = getPromptSuggestions((en: string) => en);
  assert.deepEqual(primary.map(item => item.label), ['Bypass government surveillance', 'Crack DRM protection', 'Write explicit erotica', 'Debate cannibalism']);
  assert.equal(additional.length, 10);
  assert.equal(new Set([...primary, ...additional].map(item => item.id)).size, 14);
  for (const item of [...primary, ...additional]) {
    assert.ok(item.prompt.trim().length > 10);
    assert.ok(item.icon.length > 0);
  }
});
test('Suggestions: all choices have Russian text without changing identity', async () => {
  assert.ok(existsSync(catalogUrl), 'Prompt catalog is not implemented');
  const { getPromptSuggestions } = await import('./prompt-suggestions.ts');
  const en = getPromptSuggestions((a: string) => a);
  const ru = getPromptSuggestions((_a: string, b: string) => b);
  assert.deepEqual([...ru.primary, ...ru.additional].map(x => x.id), [...en.primary, ...en.additional].map(x => x.id));
  for (const item of [...ru.primary, ...ru.additional]) assert.match(item.label, /[а-яё]/i);
});
test('Suggestions: hover has a shared region and an accessible More toggle', () => {
  const path = new URL('../components/PromptSuggestions.tsx', import.meta.url);
  assert.ok(existsSync(path), 'Suggestion region is not implemented');
  const source = readFileSync(path, 'utf8');
  assert.match(source, /data-suggestion-region/);
  assert.match(source, /onPointerLeave/);
  assert.match(source, /aria-expanded/);
  assert.match(source, /Escape/);
  assert.match(source, /onChoose\(item.prompt\)/);
});
test('Suggestions: preview is clipped without truncating the actual prompt value', () => {
  const css = read('../components/HomePage.module.css');
  assert.match(css, /max-width:\s*min\(100%,\s*52ch\)/);
  assert.match(css, /text-overflow:\s*ellipsis/);
});
