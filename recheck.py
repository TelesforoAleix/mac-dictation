#!/opt/homebrew/bin/python3
"""Weekly private VoiceInk snapshot, large-v3 assessment and additive backup."""
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import unquote, urlsplit
import uuid

APPLE_EPOCH = 978307200
PRIVATE = Path.home() / 'Library/Application Support/mac-dictation'
VOICEINK = Path.home() / 'Library/Application Support/com.prakashjoshipax.VoiceInk'
NODE = 'dictation-backup@homelab-agent'
# SwiftData's Core Data mapping of Transcription at c09cc1f. No other entity is read.
REQUIRED = {'Z_PK': 'INTEGER', 'Z_ENT': 'INTEGER', 'Z_OPT': 'INTEGER',
            'ZID': 'BLOB', 'ZTEXT': 'VARCHAR', 'ZENHANCEDTEXT': 'VARCHAR',
            'ZTIMESTAMP': 'TIMESTAMP', 'ZDURATION': 'FLOAT',
            'ZAUDIOFILEURL': 'VARCHAR', 'ZTRANSCRIPTIONSTATUS': 'VARCHAR',
            'ZTRANSCRIPTIONMODELNAME': 'VARCHAR', 'ZAIENHANCEMENTMODELNAME': 'VARCHAR',
            'ZPROMPTNAME': 'VARCHAR', 'ZTRANSCRIPTIONDURATION': 'FLOAT',
            'ZENHANCEMENTDURATION': 'FLOAT', 'ZAIREQUESTSYSTEMMESSAGE': 'VARCHAR',
            'ZAIREQUESTUSERMESSAGE': 'VARCHAR'}
MODE_COLUMNS = ({'ZMODENAME': 'VARCHAR', 'ZMODEEMOJI': 'VARCHAR'},
                {'ZPOWERMODENAME': 'VARCHAR', 'ZPOWERMODEEMOJI': 'VARCHAR'})


class SafeError(Exception):
    """Only fixed, content-free messages may be displayed."""


def run_quiet(args):
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise SafeError('External command failed; state retained for retry (output suppressed).')


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_schema(db):
    columns = {r[1]: r[2].upper() for r in db.execute('PRAGMA table_info(ZTRANSCRIPTION)')}
    if not any(columns == {**REQUIRED, **mode} for mode in MODE_COLUMNS):
        raise SafeError('VoiceInk store layout differs from c09cc1f; nothing processed.')
    if not {'Z_ENT', 'Z_NAME'}.issubset({r[1] for r in db.execute('PRAGMA table_info(Z_PRIMARYKEY)')}):
        raise SafeError('VoiceInk store entity layout differs from c09cc1f; nothing processed.')
    entities = db.execute("SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME = 'Transcription'").fetchall()
    if len(entities) != 1:
        raise SafeError('VoiceInk store entity differs from c09cc1f; nothing processed.')
    return entities[0][0]


def copy_snapshot(source, snapshot):
    # File I/O only on the live store. SQLite must never receive a live path.
    shutil.copyfile(source, snapshot)
    for suffix in ('-wal', '-shm'):
        try:
            shutil.copyfile(Path(str(source) + suffix), Path(str(snapshot) + suffix))
        except FileNotFoundError:
            # A sidecar may disappear on checkpoint/close. Integrity is checked below.
            pass


def snapshot_rows(source, temporary_root):
    rows = None
    for attempt in range(2):
        try:
            with tempfile.TemporaryDirectory(prefix='snapshot-', dir=temporary_root) as directory:
                snapshot = Path(directory) / 'history.sqlite'
                copy_snapshot(source, snapshot)
                with contextlib.closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)) as db:
                    db.execute('PRAGMA query_only=ON')
                    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise sqlite3.DatabaseError('Snapshot integrity check failed')
                    entity = validate_schema(db)
                    rows = db.execute('SELECT Z_PK, ZID, ZTIMESTAMP, ZAUDIOFILEURL, ZTEXT, '
                                      'ZENHANCEDTEXT, ZTRANSCRIPTIONSTATUS FROM ZTRANSCRIPTION '
                                      'WHERE Z_ENT=? ORDER BY ZTIMESTAMP, Z_PK', (entity,)).fetchall()
            break
        except sqlite3.DatabaseError:
            if attempt == 0:
                time.sleep(10)
            else:
                raise SafeError('Snapshot integrity failed twice; run skipped, state unchanged.') from None
    result = []
    for pk, identifier, timestamp, audio, text, cleaned, status in rows:
        if (not isinstance(identifier, bytes) or len(identifier) != 16
                or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)
                or not isinstance(text, str) or (cleaned is not None and not isinstance(cleaned, str))
                or (audio is not None and not isinstance(audio, str))
                or status not in (None, 'pending', 'completed', 'failed', 'canceled')):
            raise SafeError('VoiceInk row layout differs from c09cc1f; nothing processed.')
        result.append(dict(id=str(uuid.UUID(bytes=identifier)), timestamp=timestamp + APPLE_EPOCH,
                           audio_url=audio, text=text, cleaned_text=cleaned, status=status))
    return result


def new_rows(rows, state):
    # IDs prevent boundary ties and catch rows committed late or completed after a run.
    seen = set(state.get('processed_ids', []))
    return [r for r in rows if r['id'] not in seen and r['status'] != 'pending']


def recording_path(url, recordings):
    parsed = urlsplit(url)
    path = Path(unquote(parsed.path))
    if (parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost')
            or parsed.query or parsed.fragment or path.parent != recordings
            or not re.fullmatch(r'[0-9a-fA-F-]{36}\.wav', path.name)
            or path.is_symlink() or not path.is_file()):
        raise SafeError('Recording missing or outside the expected UUID WAV directory; retry required.')
    uuid.UUID(path.stem)
    return path


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return ''.join(c if c.isalnum() or c.isspace() else ' ' for c in text).split()


def disagreement(left, right):
    a, b = normalize(left), normalize(right)
    previous = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (word != other)))
        previous = current
    return previous[-1] / max(len(a), len(b), 1)


def rank(items):
    return sorted((i for i in items if i['score'] > 0),
                  key=lambda i: (-i['score'], i['id']))[:20]


def assess(row, audio, work, model, cli):
    output = work / 'assessment'
    output.with_suffix('.txt').unlink(missing_ok=True)
    run_quiet([str(cli), '-m', str(model), '-l', 'auto', '-f', str(audio),
               '-otxt', '-of', str(output)])  # Metal enabled by default; no -ng / translation.
    recheck = output.with_suffix('.txt').read_text(encoding='utf-8').strip()
    return dict(row, recording='recordings/' + audio.name, recheck=recheck,
                score=disagreement(row['text'], recheck))


def ssh_command(key):
    # Resolve the existing alias without inheriting its authentication identities.
    config = subprocess.run(['/usr/bin/ssh', '-G', 'homelab-agent'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=True, text=True).stdout
    options = dict(line.split(' ', 1) for line in config.splitlines() if ' ' in line)
    return ['/usr/bin/ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                      '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
                      '-o', 'StrictHostKeyChecking=yes',
                      '-o', 'HostKeyAlias=' + options.get('hostkeyalias', options['hostname']),
                      '-o', 'Hostname=' + options['hostname'], '-p', options['port'],
                      '-i', str(key)]


def send(batch, key):
    ssh = shlex.join(ssh_command(key))
    run_quiet(['/usr/bin/rsync', '-rt', '--ignore-existing', '-e', ssh,
               '--', str(batch) + '/', NODE + ':./'])


def execute(root=PRIVATE, voice=VOICEINK):
    os.umask(0o077)
    if (root / 'recheck-disabled').exists():
        print('Weekly re-check disabled.')
        return
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with (root / 'recheck.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SafeError('Weekly re-check already running.') from None
        state_path = root / 'recheck-state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {'processed_ids': []}
        # Always validate the current layout before resuming any uploads or changing state.
        rows = snapshot_rows(voice / 'default.store', root)
        pending = root / 'recheck-pending.json'
        if not pending.exists():
            selected = new_rows(rows, state)
            if not selected:
                print('New history rows: 0; no upload needed.')
                return
            run_id = uuid.uuid4().hex
            run_dir = root / 'rechecks' / run_id
            batch = run_dir / 'upload'
            (batch / 'recordings').mkdir(parents=True, mode=0o700)
            (batch / 'history').mkdir(mode=0o700)
            model = root / 'models/ggml-large-v3.bin'
            cli = Path('/opt/homebrew/bin/whisper-cli')
            if not model.is_file() or not cli.is_file():
                raise SafeError('Install Homebrew whisper.cpp and move large-v3 first; see docs.')
            reviews = []
            with tempfile.TemporaryDirectory(prefix='assessment-', dir=root) as directory:
                for row in selected:
                    if not row['audio_url']:
                        continue  # Assistant-only history has no recording; still back it up.
                    audio = recording_path(row['audio_url'], voice / 'Recordings')
                    shutil.copyfile(audio, batch / 'recordings' / audio.name)
                    reviews.append(assess(row, audio, Path(directory), model, cli))
            # History contains only the four requested fields plus stable ID/status, no prompts.
            # Replace the absolute URL with a relative recording name in all private artifacts.
            history = []
            for row in selected:
                value = dict(row)
                value['recording'] = ('recordings/' + recording_path(row['audio_url'], voice / 'Recordings').name
                                      if row['audio_url'] else None)
                del value['audio_url']
                history.append(value)
            for item in reviews:
                del item['audio_url']
            with (batch / 'history' / (run_id + '.jsonl')).open('w', encoding='utf-8') as stream:
                for row in history:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            write_json(run_dir / 'review.json', {'version': 1, 'run_id': run_id, 'items': rank(reviews)})
            write_json(pending, {'run_id': run_id, 'ids': [r['id'] for r in selected],
                                 'last_run': time.time(), 'rows': len(selected),
                                 'recordings': len(reviews), 'items': len(rank(reviews))})
        transaction = json.loads(pending.read_text())
        run_dir = root / 'rechecks' / transaction['run_id']
        key = root / 'dictation-node-key'
        if key.stat().st_mode & 0o077:
            raise SafeError('Job key must be private (0600).')
        send(run_dir / 'upload', key)
        write_json(state_path, {'version': 1, 'last_run': transaction['last_run'],
                               'processed_ids': sorted(set(state['processed_ids']) | set(transaction['ids'])),
                               'latest_run': transaction['run_id']})
        pending.unlink()
        print('History rows: {rows}; recordings: {recordings}; review items: {items}; copy complete.'
              .format(**transaction))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 2 and sys.argv[1] in ('disable', 'enable', 'status'):
            if sys.argv[1] == 'status':
                state_file = PRIVATE / 'recheck-state.json'
                state = json.loads(state_file.read_text()) if state_file.exists() else {}
                review = PRIVATE / 'rechecks' / state.get('latest_run', 'none') / 'review.json'
                count = len(json.loads(review.read_text())['items']) if review.exists() else 0
                print('Disabled: {}; review exists: {}; review items: {}; processed rows: {}.'
                      .format((PRIVATE / 'recheck-disabled').exists(), review.exists(), count,
                              len(state.get('processed_ids', []))))
            else:
                os.umask(0o077)
                PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
                PRIVATE.chmod(0o700)
                marker = PRIVATE / 'recheck-disabled'
                if sys.argv[1] == 'disable':
                    marker.touch(mode=0o600)
                else:
                    marker.unlink(missing_ok=True)
                print('Weekly re-check ' + sys.argv[1] + 'd.')
        elif len(sys.argv) == 1:
            execute()
        else:
            raise SafeError('Usage: recheck.py [disable|enable|status]')
    except SafeError as error:
        print(str(error))
        raise SystemExit(1) from None
    except Exception:
        # Never print exception values, traceback, command arguments or data-derived paths.
        print('Weekly re-check failed; private state retained. No content logged.')
        raise SystemExit(1) from None
