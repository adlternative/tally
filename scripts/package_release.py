#!/usr/bin/env python3
"""Build a local, Node-free runtime archive from an explicit public-file allowlist."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def package(output=None):
    dist = ROOT / 'frontend' / 'dist'
    if not (dist / 'index.html').is_file():
        raise SystemExit('Run first: cd frontend && npm ci && npm run build')
    output = output or ROOT / 'artifacts' / 'tally-runtime.zip'
    files = [ROOT / name for name in ('tally.py', 'sources.py', 'sources.json',
                                     'sources.d/example-plugin.json', 'README.md', 'README.zh-CN.md',
                                     'LICENSE', 'THIRD_PARTY_NOTICES.md')]
    files += sorted(p for p in dist.rglob('*') if p.is_file())
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise SystemExit(f'Refusing non-public path: {path.name}')
            archive.write(path, Path('tally') / path.relative_to(ROOT))
    return output


if __name__ == '__main__':
    print(package())
