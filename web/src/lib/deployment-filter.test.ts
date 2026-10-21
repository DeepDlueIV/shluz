import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

test('Vercel builds web changes but skips unrelated repository changes', () => {
  const config = JSON.parse(readFileSync(new URL('../../vercel.json', import.meta.url), 'utf8'));
  assert.equal(config.ignoreCommand, 'git diff HEAD^ HEAD --quiet -- .');
  const root = mkdtempSync(join(tmpdir(), 'noclip-deploy-'));
  const git = (...args: string[]) => {
    const result = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  };
  const ignore = () => spawnSync(config.ignoreCommand, { cwd: join(root, 'web'), shell: true }).status;
  try {
    mkdirSync(join(root, 'web')); mkdirSync(join(root, 'telegram_bot'));
    writeFileSync(join(root, 'web', 'page.tsx'), 'first');
    writeFileSync(join(root, 'telegram_bot', 'bot.py'), 'first');
    git('init', '-q'); git('config', 'user.name', 'NoClip tests'); git('config', 'user.email', 'test@example.invalid');
    git('add', '.'); git('commit', '-qm', 'initial');
    writeFileSync(join(root, 'telegram_bot', 'bot.py'), 'second');
    git('add', '.'); git('commit', '-qm', 'backend only');
    assert.equal(ignore(), 0, 'Unchanged web must be skipped');
    writeFileSync(join(root, 'web', 'page.tsx'), 'second');
    git('add', '.'); git('commit', '-qm', 'web update');
    assert.equal(ignore(), 1, 'Web update must build');
  } finally { rmSync(root, { recursive: true, force: true }); }
});
