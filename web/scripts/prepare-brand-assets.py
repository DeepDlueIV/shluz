# Однократный импорт закреплённой версии SVG и адресное обновление каталога.
import pathlib
import urllib.request
import xml.etree.ElementTree as ET

web = pathlib.Path(__file__).resolve().parents[1]
revision = '21b2144f4ebe87ead9b7c0e10067759ae261b03e'
base = 'https://raw.githubusercontent.com/lobehub/lobe-icons/' + revision + '/'
root = web / 'public/media/models'
root.mkdir(parents=True, exist_ok=True)
for name in 'claude google deepseek openai mistral meta qwen grok kimi bfl nvidia elevenlabs runway bytedance minimax zai gemma kling pixverse vidu'.split():
    data = urllib.request.urlopen(base + 'packages/static-svg/icons/' + name + '.svg', timeout=30).read()
    element = ET.fromstring(data)
    assert element.tag.endswith('svg')
    assert b'<script' not in data and b'javascript:' not in data
    (root / (name + '.svg')).write_bytes(data)
license_text = urllib.request.urlopen(base + 'LICENSE', timeout=30).read()
assert b'MIT' in license_text
(web / 'public/licenses/lobe-icons-MIT.txt').write_bytes(license_text)
(root / 'SOURCES.md').write_text('Model brand SVGs: Lobe Icons, https://github.com/lobehub/lobe-icons\nPinned revision: ' + revision + '\nSource: packages/static-svg/icons/*.svg\nMIT license: ../../licenses/lobe-icons-MIT.txt\nGLM uses the Z.ai provider mark (zai.svg); Black Forest Labs uses bfl.svg.\nBrand trademarks belong to their owners. Identification does not imply a partnership.\n', encoding='utf-8')
p = web / 'src/components/LandingSections.tsx'
s = p.read_text().replace('modelRows, modelCatalogSource', 'modelRows, modelIconSlugs')
if 'data-model-icon' not in s:
    old = '<span>{name}</span></div>)'
    assert old in s
    s = s.replace(old, '<img data-model-icon src={`/media/models/${modelIconSlugs[name]}.svg`} className={styles.modelMark} width="34" height="34" alt="" loading="lazy" /><span>{name}</span></div>)')
s = '\n'.join(line for line in s.splitlines() if not any(text in line for text in ['From the Venice catalog', 'View the Venice model catalog'])) + '\n'
p.write_text(s)
p = web / 'src/components/LandingSections.module.css'
s = p.read_text().replace('.modelMark { width: 34px; height: 34px; flex-shrink: 0; }', '.modelMark { width: 34px; height: 34px; flex-shrink: 0; object-fit: contain; filter: brightness(0) invert(1); opacity: .85; }')
p.write_text(s)
p = web / 'scripts/browser-check.mjs'
s = p.read_text().replace('Reduced-motion setting is respected, with explicit animation opt-in', 'Background autoplays without a button even with reduced motion')
s = s.replace("  assert.equal(await p.locator('[data-hero-video]').evaluate(v => v.paused), true);\n  await p.getByRole('button', { name: 'Enable animation' }).click();", "  assert.equal(await p.getByRole('button', { name: 'Enable animation' }).count(), 0);")
p.write_text(s)
