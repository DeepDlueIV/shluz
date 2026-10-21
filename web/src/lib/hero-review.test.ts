import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('Language trigger uses a globe; flags remain in the menu', () => {
  const code = source('../components/LanguageSwitcher.tsx');
  assert.ok(code.includes('data-language-globe'));
  assert.ok(!code.includes('<Flag locale={locale} />'));
  assert.ok(code.includes('<Flag locale={code} />'));
});
test('Background starts automatically without an enable button', () => {
  const code = source('../components/HeroVideo.tsx');
  assert.ok(!code.includes('Enable animation'));
  assert.ok(!code.includes('!ambientMotion'));
  assert.match(code, /autoPlay(?:\s|>)/);
});
test('The unwanted curved border layer is removed', () => {
  assert.ok(!source('../components/HomePage.module.css').includes('.glassPanel::before'));
});
test('Catalog has no provider attribution or outbound catalog link', () => {
  assert.doesNotMatch(source('../components/LandingSections.tsx'), /venice|modelCatalogSource/i);
});
test('Docked arrow is a mode toggle, not a disabled send button', () => {
  const code = source('../components/HomePage.tsx');
  assert.ok(code.includes('data-prompt-toggle'));
  assert.ok(code.includes('aria-expanded={expanded}'));
  assert.ok(code.includes('data-prompt-mode'));
});
