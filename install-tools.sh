#!/bin/bash
set +x
set -euo pipefail
umask 077

fail() { printf '%s\n' "$1" >&2; exit 1; }
[[ $# == 0 ]] || fail 'Usage: ./install-tools.sh'
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -z $(git -C "$repo_dir" status --porcelain --untracked-files=all) ]] || fail 'Checkout must be clean; nothing installed.'
git -C "$repo_dir" fetch --quiet origin main || fail 'Cannot fetch origin/main; nothing installed.'
commit=$(git -C "$repo_dir" rev-parse --verify HEAD)
git -C "$repo_dir" merge-base --is-ancestor "$commit" refs/remotes/origin/main || fail 'Checkout commit is not contained in origin/main; nothing installed.'
[[ -z $(git -C "$repo_dir" status --porcelain --untracked-files=all) ]] || fail 'Checkout must be clean; nothing installed.'

# Single install list: add the review page and any local imports here when merged.
# recheck.py currently imports only the Python standard library.
files=(recheck.py)
python3 - "$repo_dir" "$commit" "${files[@]}" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    repo, commit, *files = sys.argv[1:]
    root = Path.home() / 'Library/Application Support/mac-dictation'
    target = root / 'bin'
    if root.is_symlink() or target.is_symlink():
        raise ValueError('symlink destination')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    target.mkdir(exist_ok=True, mode=0o700)
    target.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix='.install-', dir=root) as directory:
        staging = Path(directory)
        for name in files:
            # Export the stamped commit, rather than racing working-file edits.
            content = subprocess.check_output(
                ['git', '-C', repo, 'show', commit + ':' + name],
                stderr=subprocess.DEVNULL)
            (staging / name).write_bytes(content)
            (staging / name).chmod(0o600)
        (staging / 'install.json').write_text(json.dumps({'commit': commit}) + '\n')
        (staging / 'install.json').chmod(0o600)
        for name in [*files, 'install.json']:
            os.replace(staging / name, target / name)
except Exception:
    print('Tool installation failed; output suppressed.', file=sys.stderr)
    raise SystemExit(1) from None
print('Installed commit: ' + commit)
PY
