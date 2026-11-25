#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ${1:-} == --help && $# == 1 ]]; then
    printf '%s\n' 'Usage: ./snapshot.sh' 'Create a source snapshot in tests/output/snapshots (review before sharing).'
    exit 0
fi
if (( $# != 0 )); then
    printf '%s\n' 'Usage: ./snapshot.sh' >&2
    exit 1
fi
python3 - <<'PY'
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

root = Path.cwd()
# Explicit project areas and root files; do not archive arbitrary local files.
areas = {'src', 'plugin', 'scripts', 'tests', 'vencord-dist', 'docs'}
root_files = {'pyproject.toml', 'uv.lock', 'LICENSE', 'README.md', '.gitignore',
              'preview.png', 'setup.sh', 'validate.sh', 'stats.sh', 'snapshot.sh',
              'version.sh', 'install.sh', 'uninstall.sh', 'build.sh'}
excluded = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache',
            '.basedpyright', 'node_modules', 'build', 'dist', 'output', 'htmlcov',
            '.benchmarks', '.ssh', '.aws', 'secrets', 'credentials'}
suffixes = {'.py', '.pyi', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.css', '.html',
            '.json', '.toml', '.yaml', '.yml', '.md', '.txt', '.sh', '.svg', '.png',
            '.jpg', '.jpeg', '.webp', '.ico', '.lock'}
raw = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'])
paths = []
for name in sorted(set(os.fsdecode(p) for p in raw.split(b'\0') if p)):
    path = Path(name)
    parts = path.parts
    if not parts or path.is_absolute() or '..' in parts:
        continue
    if any(p.lower() in excluded or p.startswith('.') or p.endswith('.egg-info') for p in parts):
        if name != '.gitignore':
            continue
    if any(any(word in p.lower() for word in ('secret', 'credential', 'password', 'token', 'private_key')) for p in parts):
        continue
    if len(parts) == 1:
        if name not in root_files:
            continue
    elif parts[0] not in areas or (path.suffix.lower() not in suffixes and path.name != 'LICENSE'):
        continue
    if path.name.lower() in {'config.json', 'config.toml', 'settings.json', 'settings.toml', 'state.json'}:
        continue
    if any((root / Path(*parts[:i])).is_symlink() for i in range(1, len(parts)+1)):
        continue
    if path.is_file():
        paths.append(path)
if not paths or Path('pyproject.toml') not in paths:
    raise SystemExit('No project source found; run from the VencOverlay Git repository.')
out = root / 'tests/output/snapshots'
if any(p.is_symlink() for p in (root/'tests', root/'tests/output', out)):
    raise SystemExit('Refusing symlinked snapshot output directory.')
out.mkdir(parents=True, exist_ok=True)
final = out / f'vencoverlay-reference-current-{int(time.time())}.zip'
fd, tmp = tempfile.mkstemp(prefix='.snapshot-', suffix='.zip', dir=out)
os.close(fd)
try:
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            archive.write(path, path.as_posix())
    with zipfile.ZipFile(tmp) as archive:
        if archive.testzip() is not None or archive.namelist() != [p.as_posix() for p in paths]:
            raise RuntimeError('Snapshot integrity verification failed')
    os.link(tmp, final)  # Atomic publication; never overwrite an existing snapshot.
finally:
    os.unlink(tmp)
print(f'Snapshot: {final}')
print(f'Files: {len(paths)} | Bytes: {final.stat().st_size:,} | Integrity: OK')
print('Review archive contents and source for sensitive information before sharing.')
PY
