from pathlib import Path
import hashlib, html, json, os, re, shutil
from urllib.parse import quote
import markdown

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'website' / 'dist'
BASE = 'https://tools.minmin11538.cn/notehelper/'
ZIP = '笔记同步飞书接入包-v0.7.zip'
EXPECTED = 'b4af6d17b696778a2808a0925f8cf9e85545df25da606fdc84faf7d13424cd0f'
assert hashlib.sha256((ROOT / 'downloads' / ZIP).read_bytes()).hexdigest() == EXPECTED
OUT.mkdir(exist_ok=True)
for name in ['index.html', 'styles.css']:
    shutil.copyfile(ROOT / 'website' / name, OUT / name)
for name in ['downloads', 'notebook-feishu-onboarding']:
    shutil.copytree(ROOT / name, OUT / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
shutil.copyfile(ROOT / '发给Agent的启动说明.md', OUT / '发给Agent的启动说明.md')

def rewrite(source, destination, text, html_links=True):
    def link(m):
        target = m.group(1)
        if '://' in target or target.startswith('#'):
            return m.group(0)
        raw, sep, anchor = target.partition('#')
        resolved = (source.parent / raw).resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.exists():
            return m.group(0)
        copied = OUT / resolved.relative_to(ROOT)
        if html_links and copied.suffix == '.md':
            copied = copied.with_suffix('.html')
        relative = os.path.relpath(copied, destination.parent)
        return '](' + quote(relative, safe='/') + (sep + anchor if sep else '') + ')'
    return re.sub(r'\]\(([^)]+)\)', link, text)

def render(source, destination):
    source_text = source.read_text()
    source_text = re.sub(r'\A---\n.*?\n---\n', '', source_text, count=1, flags=re.S)
    body = markdown.markdown(rewrite(source, destination, source_text), extensions=['fenced_code', 'tables', 'sane_lists'])
    stylesheet = quote(os.path.relpath(OUT / 'styles.css', destination.parent), safe='/')
    home = quote(os.path.relpath(OUT / 'index.html', destination.parent), safe='/')
    title = next((line.lstrip('# ') for line in source_text.splitlines() if line.startswith('# ')), source.stem)
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}｜敏敏笔记同步</title><link rel="stylesheet" href="{stylesheet}"></head><body><header class="site-header"><a class="brand" href="{home}">敏敏 <span>/ 笔记同步</span></a><a href="{home}">返回下载页</a></header><main class="guide">{body}</main><footer>敏敏 · 笔记同步飞书接入包 v0.7 · 图片来源见说明书</footer></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page)

for source in (ROOT / 'notebook-feishu-onboarding').rglob('*.md'):
    render(source, (OUT / source.relative_to(ROOT)).with_suffix('.html'))
startup = ROOT / '发给Agent的启动说明.md'
render(startup, OUT / '发给Agent的启动说明.html')
manual = ROOT / 'notebook-feishu-onboarding/references/grouped-messages.md'
render(manual, OUT / 'guide.html')
render(ROOT / 'notebook-feishu-onboarding/references/sources.md', OUT / 'sources.html')
(OUT / 'guide.md').write_text(rewrite(manual, OUT / 'guide.md', manual.read_text(), html_links=False))
release = {'version': '0.7', 'download_url': BASE + 'downloads/' + quote(ZIP), 'bytes': (ROOT / 'downloads' / ZIP).stat().st_size, 'sha256': EXPECTED, 'archive_root': '笔记同步飞书接入包', 'skill_entry': 'notebook-feishu-onboarding/SKILL.md', 'guide_url': BASE + 'guide.html', 'instructions_url': BASE + 'install.md'}
(OUT / 'release.json').write_text(json.dumps(release, ensure_ascii=False, indent=2) + '\n')
shutil.copyfile(ROOT / 'website' / 'install.md', OUT / 'install.md')
manifest = {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'files.sha256.json'}
(OUT / 'files.sha256.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'directory': str(OUT), 'files': len(manifest)+1, 'zip_sha256': EXPECTED}, ensure_ascii=False))
