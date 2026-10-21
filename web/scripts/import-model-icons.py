"""Повторяемое получение статических SVG. Чужой код не выполняется."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
import hashlib
import xml.etree.ElementTree as ET
import re

revision = '21b2144f4ebe87ead9b7c0e10067759ae261b03e'
root = f'https://raw.githubusercontent.com/lobehub/lobe-icons/{revision}/'
sources = {
    'claude': ['claude'], 'google': ['google'], 'deepseek': ['deepseek'],
    'openai': ['openai'], 'mistral': ['mistral'], 'meta': ['meta'],
    'qwen': ['qwen'], 'grok': ['grok'], 'kimi': ['kimi'],
    'black-forest-labs': ['flux'], 'nvidia': ['nvidia'],
    'elevenlabs': ['elevenlabs'], 'runway': ['runway'],
    'bytedance': ['bytedance'], 'minimax': ['minimax'],
    'glm': ['zai', 'chatglm'], 'gemma': ['gemma'],
    'kling': ['kling'], 'pixverse': ['pixverse'], 'vidu': ['vidu'],
}
repo = Path(__file__).resolve().parents[2]
destination = repo / 'web/public/media/models'
destination.mkdir(parents=True, exist_ok=True)

def fetch(item):
    name, candidates = item
    data = None
    for candidate in candidates:
        path = f'packages/static-svg/icons/{candidate}.svg'
        try:
            data = urlopen(root + path, timeout=30).read()
            break
        except HTTPError as error:
            if error.code != 404:
                raise
    if data is None:
        raise RuntimeError('Missing asset: ' + name)
    if len(data) > 200000:
        raise RuntimeError('Oversized asset: ' + name)
    element = ET.fromstring(data)
    if element.tag.split('}')[-1] != 'svg':
        raise RuntimeError('Not SVG: ' + name)
    for node in element.iter():
        if node.tag.split('}')[-1].lower() in {'script', 'foreignobject', 'iframe', 'image', 'animate', 'set'}:
            raise RuntimeError('Unexpected active/external SVG content: ' + name)
        for key, value in node.attrib.items():
            local = key.split('}')[-1].lower()
            if local.startswith('on') or (local == 'href' and not value.startswith('#')):
                raise RuntimeError('Unexpected SVG attribute: ' + name)
            if re.search(r'url\(\s*[\"\x27]?\s*(?:https?:|//|data:)', value, re.I):
                raise RuntimeError('External SVG URL: ' + name)
    (destination / (name + '.svg')).write_bytes(data)
    print(name, path, len(data), flush=True)
    return f'| `{name}.svg` | [{path}]({root + path}) | `{hashlib.sha256(data).hexdigest()}` |'

with ThreadPoolExecutor(max_workers=6) as pool:
    rows = list(pool.map(fetch, sources.items()))
license_data = urlopen(root + 'LICENSE', timeout=30).read()
if b'MIT License' not in license_data:
    raise RuntimeError('Unexpected license')
(repo / 'web/public/licenses/lobe-icons-MIT.txt').write_bytes(license_data)
(repo / 'docs').mkdir(exist_ok=True)
(repo / 'docs/web-icon-sources.md').write_text(
    '# Источники логотипов моделей\n\n'
    'SVG: LobeHub Icons, commit `' + revision + '`. Лицензия: `web/public/licenses/lobe-icons-MIT.txt`.\n'
    'Знаки используются для идентификации, не означают партнёрства или доступности модели.\n'
    'Black Forest Labs: знак FLUX; GLM: знак разработчика Z.ai (при отсутствии — ChatGLM).\n'
    'Прежние пять inline SVG первой версии были рисованными заглушками, не этими файлами.\n\n'
    '| Файл | Оригинал | SHA-256 |\n|---|---|---|\n' + '\n'.join(rows) + '\n', encoding='utf-8')
