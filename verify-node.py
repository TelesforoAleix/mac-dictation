#!/opt/homebrew/bin/python3
"""Synthetic restriction/overwrite probes; admin audit prints only counts and verdicts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import recheck


def admin_audit():
    code = '''import hashlib,json,os
from pathlib import Path
root=Path('/srv/homelab/voice-data/originals/dictation')
files=[p for p in root.rglob('*') if p.is_file()]
probe=root/'.w1-validation/synthetic.txt'
print(json.dumps(dict(recordings=sum(p.suffix=='.wav' for p in files),
 history=sum(p.suffix=='.jsonl' for p in files),
 probe_hash=hashlib.sha256(probe.read_bytes()).hexdigest(),
 escape_exists=(root.parent/'w1-escape').exists())))'''
    result = subprocess.run(['ssh', 'homelab-agent', 'sudo python3 -'], input=code,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=True)
    return json.loads(result.stdout)


def cleanup_probe():
    code = '''import os
from pathlib import Path
root=Path('/srv/homelab/voice-data/originals/dictation')
probe=root/'.w1-validation'
if probe.exists():
    if probe.is_symlink() or not probe.is_dir() or probe.parent != root:
        raise SystemExit(2)
    entries=list(probe.iterdir())
    if len(entries)!=1 or entries[0].name!='synthetic.txt' or entries[0].is_symlink() or not entries[0].is_file():
        raise SystemExit(3)
    entries[0].unlink()
    probe.rmdir()'''
    subprocess.run(['ssh', 'homelab-agent', 'sudo python3 -c', code],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def verify(key):
    expected = hashlib.sha256(b'synthetic first version\n').hexdigest()
    try:
        with tempfile.TemporaryDirectory(prefix='dictation-probe-') as directory:
            batch = Path(directory)
            (batch / '.w1-validation').mkdir()
            probe = batch / '.w1-validation/synthetic.txt'
            probe.write_bytes(b'synthetic first version\n')
            recheck.send(batch, key)
            before = admin_audit()
            if before['probe_hash'] != expected or before['escape_exists']:
                raise recheck.SafeError('Synthetic probe precondition failed.')
            probe.write_bytes(b'synthetic attempted replacement\n')
            recheck.send(batch, key)
            after = admin_audit()
            if after != before:
                raise recheck.SafeError('Additive copy verification failed.')
        print('Synthetic uploads: 2; overwrites: 0.')
        probes = {
            'shell': 'id',
            'read': 'rsync --server --sender -r . ./',
            'outside write': 'rsync --server -r . ../w1-escape',
        }
        for name, command in probes.items():
            result = subprocess.run(recheck.ssh_command(key) + [recheck.NODE, command],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            expected_error = {'shell': b'SSH_ORIGINAL_COMMAND does not run rsync',
                              'read': b'reading from write-only server is not allowed',
                              'outside write': b'do not use .. in arg'}[name]
            if result.returncode == 0 or expected_error not in result.stderr:
                raise recheck.SafeError('Restriction probe failed (output suppressed): ' + name)
            print('Refused: ' + name + '.')
        print('Node recordings: {}; history files: {}.'.format(after['recordings'], after['history']))
    finally:
        # Synthetic probes must never remain under originals, including on assertion failure.
        cleanup_probe()


if __name__ == '__main__':
    try:
        key = Path(sys.argv[1]) if len(sys.argv) == 2 else recheck.PRIVATE / 'dictation-node-key'
        verify(key)
    except recheck.SafeError as error:
        print(str(error))
        raise SystemExit(1) from None
    except Exception:
        print('Node verification failed; output suppressed.')
        raise SystemExit(1) from None
