import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
const source = () => readFileSync(new URL('../components/PromptSuggestions.tsx', import.meta.url), 'utf8');
const css = () => readFileSync(new URL('../components/PromptSuggestions.module.css', import.meta.url), 'utf8');

test('More keeps one persistent disclosure instead of remounting on hover', () => {
  assert.doesNotMatch(source(), /AnimatePresence|expanded\s*&&\s*<motion/);
  assert.match(source(), /data-open=\{expanded\}/);
  assert.match(source(), /aria-hidden=\{!expanded\}/);
  assert.match(source(), /inert=\{!expanded\}/);
});
test('More fades glass buttons, never their backdrop-root ancestor', () => {
  assert.match(css(), /\.disclosure\s+\.additional\s+button\s*\{[^}]*opacity:\s*0/);
  assert.match(css(), /\.disclosure\[data-open="true"\]\s+\.additional\s+button\s*\{[^}]*opacity:\s*1/);
  const container = css().match(/\.disclosure\s*\{([^}]+)\}/)?.[1] || '';
  assert.doesNotMatch(container, /opacity|will-change|filter/);
  assert.match(container, /grid-template-rows:\s*0fr/);
  assert.match(css(), /grid-template-rows:\s*1fr/);
  assert.match(css(), /blur\(32px\)/);
});
test('Closing suggestions removes hidden choices from keyboard interaction', () => {
  assert.match(source(), /tabIndex=\{enabled && \(!extra \|\| expanded\) \? 0 : -1\}/);
  assert.match(source(), /expanded && focusExtra\.current/);
});
test('Mouse click after hover does not reverse an opening disclosure', () => {
  assert.match(source(), /event\.detail > 0 && pointerInside\.current/);
  assert.match(source(), /hoverSession\.current/);
  assert.match(source(), /onTransitionEnd/);
});
