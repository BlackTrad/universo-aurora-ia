"""Verify that a Vite frontend bundle was built from the exact current source.

The Vite config writes aurora-source.sha256 after a successful bundle. This
checker independently recomputes the same digest; it also checks the HTML
points at compiled assets instead of /src/main.tsx.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit,unquote

IGNORE = frozenset({'node_modules', '.git', 'dist', '__pycache__'})

class EntryAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths=[]

    def handle_starttag(self,tag,attrs):
        for key,value in attrs:
            if key in ('src','href') and isinstance(value,str) and value.startswith('/static/'):
                self.paths.append(value)


def fingerprint(source: Path) -> str:
    digest=sha256()
    files=sorted((f for f in source.rglob('*') if f.is_file() and
                 not any(part in IGNORE for part in f.relative_to(source).parts)),
                 key=lambda f: f.relative_to(source).as_posix())
    for filename in files:
        digest.update(filename.relative_to(source).as_posix().encode())
        digest.update(b'\0')
        digest.update(filename.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def verify(source: Path, bundle: Path) -> list[str]:
    problems=[]
    if not source.is_dir() or not bundle.is_dir():
        return ['Fonte ou bundle inexistente.']
    marker=bundle/'aurora-source.sha256'
    if not marker.is_file() or marker.read_text(encoding='utf-8').strip()!=fingerprint(source):
        problems.append('Bundle desatualizado ou sem marca de compilação válida.')
    index=bundle/'index.html'
    if not index.is_file():
        problems.append('index.html compilado ausente.')
    else:
        html=index.read_text(encoding='utf-8')
        if '/src/main.tsx' in html or not re.search(r'/static/assets/[^"\s]+\.js',html):
            problems.append('index.html não referencia JavaScript compilado em /static/assets/.')
        assets=EntryAssets();assets.feed(html)
        for value in assets.paths:
            path=(bundle/unquote(urlsplit(value).path).removeprefix('/static/')).resolve()
            if not path.is_relative_to(bundle.resolve()) or not path.is_file():
                problems.append('Recurso compilado ausente ou inválido: '+value)
    if (source/'public/login.css').is_file() and not (bundle/'login.css').is_file():
        problems.append('Estilo do login público ausente: login.css.')
    return problems


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('frontend-src'))
    parser.add_argument('--bundle',type=Path,default=Path('frontend'))
    args=parser.parse_args(argv)
    errors=verify(args.source,args.bundle)
    for error in errors:print('ERRO:',error)
    if errors:return 1
    print('Bundle frontend corresponde ao código atual e contém assets compilados.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
