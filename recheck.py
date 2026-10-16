#!/opt/homebrew/bin/python3
"""Weekly private VoiceInk snapshot, large-v3 assessment and additive backup."""
import contextlib
import fcntl
import hashlib
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
import urllib.request
from urllib.parse import unquote, urlsplit
import uuid
import wave

APPLE_EPOCH = 978307200
PRIVATE = Path.home() / 'Library/Application Support/mac-dictation'
VOICEINK = Path.home() / 'Library/Application Support/com.prakashjoshipax.VoiceInk'
NODE = 'dictation-backup@homelab-agent'
VAD_NAME = 'ggml-silero-v5.1.2.bin'
VAD_SHA256 = '29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf'
VAD_URL = ('https://huggingface.co/ggml-org/whisper-vad/resolve/'
           'e5614ed76a5dd4b03fad5068c89efcd2617a9d1e/' + VAD_NAME)
# SwiftData's Core Data mapping of Transcription at c09cc1f.
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


def snapshot_vocabulary(source, temporary_root):
    """Only VocabularyWord.word is read, and only from a disposable file copy."""
    for attempt in range(2):
        try:
            with tempfile.TemporaryDirectory(prefix='dictionary-', dir=temporary_root) as directory:
                snapshot = Path(directory) / 'dictionary.sqlite'
                copy_snapshot(source, snapshot)
                with contextlib.closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)) as db:
                    db.execute('PRAGMA query_only=ON')
                    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise sqlite3.DatabaseError('Snapshot integrity check failed')
                    columns = {r[1]: r[2].upper() for r in db.execute('PRAGMA table_info(ZVOCABULARYWORD)')}
                    required = {'Z_PK': 'INTEGER', 'Z_ENT': 'INTEGER', 'Z_OPT': 'INTEGER',
                                'ZWORD': 'VARCHAR', 'ZDATEADDED': 'TIMESTAMP'}
                    if columns != required:
                        raise SafeError('VoiceInk dictionary layout differs from c09cc1f; nothing processed.')
                    entities = db.execute("SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME='VocabularyWord'").fetchall()
                    if len(entities) != 1:
                        raise SafeError('VoiceInk dictionary entity differs from c09cc1f; nothing processed.')
                    words = [r[0] for r in db.execute(
                        'SELECT ZWORD FROM ZVOCABULARYWORD WHERE Z_ENT=? ORDER BY ZWORD', entities[0])]
                    if not all(isinstance(word, str) and '\0' not in word for word in words):
                        raise SafeError('VoiceInk vocabulary layout differs from c09cc1f; nothing processed.')
                    return 'Important Vocabulary: ' + ', '.join(words) if words else ''
        except sqlite3.DatabaseError:
            if attempt == 0:
                time.sleep(10)
            else:
                raise SafeError('Dictionary snapshot failed twice; state unchanged.') from None


def verify_vad(path):
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != VAD_SHA256:
        raise SafeError('VAD model missing or hash mismatch; run download-vad first.')


def download_vad(root=PRIVATE):
    os.umask(0o077)
    models = root / 'models'
    models.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = models / VAD_NAME
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix='vad-', dir=models) as directory:
            staged = Path(directory) / VAD_NAME
            with urllib.request.urlopen(VAD_URL, timeout=60) as response, staged.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            verify_vad(staged)
            staged.replace(target)
    verify_vad(target)
    print('VAD model verified.')


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


def looping(text):
    """Discard any normalized 1..64-word phrase repeated at least three times consecutively."""
    words = normalize(text)
    for size in range(1, min(64, len(words) // 3) + 1):
        for start in range(len(words) - 3 * size + 1):
            phrase = words[start:start + size]
            if phrase == words[start + size:start + 2 * size] == words[start + 2 * size:start + 3 * size]:
                return True
    return False


def assess_batch(entries, work, model, cli, vocabulary=''):
    """One model load; only audio-read failures may be skipped."""
    # 1.9.5 has no CLI no-context switch. max-context=0 also disables the
    # initial prompt, so retain default context to preserve vocabulary conditioning.
    args = [str(cli), '-m', str(model), '-l', 'auto', '-otxt',
            '--vad', '--vad-model', str(model.parent / VAD_NAME)]
    if vocabulary:
        args.extend(['--prompt', vocabulary])
    candidates = []
    counts = dict(unreadable=0, excluded_cancelled=0, excluded_silent=0, loops_discarded=0)
    for index, (row, audio) in enumerate(entries):
        if row.get('status') == 'canceled':
            counts['excluded_cancelled'] += 1
            continue
        # Avoid loading Whisper at all for valid WAV headers with zero frames.
        try:
            with wave.open(str(audio), 'rb') as wav:
                empty = wav.getnframes() == 0
        except (wave.Error, EOFError):
            empty = False  # Let Whisper handle formats Python's wave cannot decode.
        if empty:
            counts['unreadable'] += 1
            continue
        output = work / str(index)
        candidates.append((row, audio, output.with_suffix('.txt')))
        args.extend(['-f', str(audio), '-of', str(output)])
    if not candidates:
        return [], counts
    # stderr is examined only in memory and never printed or saved. stdout can
    # contain transcribed text, so discard it. Metal remains enabled by default.
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode not in (0, 11):
        raise SafeError('Whisper re-check failed; no upload started (output suppressed).')
    reviews = []
    read_failures = 0
    for row, audio, output in candidates:
        read_error = ("error: failed to read audio file '" + str(audio) + "'").encode()
        if read_error in result.stderr:
            counts['unreadable'] += 1
            read_failures += 1
            output.unlink(missing_ok=True)
            continue
        if not output.is_file():
            raise SafeError('Whisper output missing; no upload started (output suppressed).')
        recheck = output.read_text(encoding='utf-8').strip()
        if not recheck:
            counts['excluded_silent'] += 1
            continue
        if looping(recheck):
            counts['loops_discarded'] += 1
            continue
        reviews.append(dict(row, recording='recordings/' + audio.name, recheck=recheck,
                            score=disagreement(row['text'], recheck)))
    # Exit 11 also covers output-write failures. Do not silently classify those as bad audio.
    failures = re.findall(rb'error: (\d+) input file\(s\) failed', result.stderr)
    if ((result.returncode == 11 and (not failures or int(failures[-1]) != read_failures))
            or (result.returncode == 0 and read_failures)):
        raise SafeError('Whisper batch failed; no upload started (output suppressed).')
    return reviews, counts


def build_run(selected, root, recordings, run_id, pending, vocabulary=''):
    building = root / 'recheck-building'
    if building.exists():
        shutil.rmtree(building)  # Incomplete staging from an interrupted pre-upload build.
    batch = building / 'upload'
    model = root / 'models/ggml-large-v3.bin'
    cli = Path('/opt/homebrew/bin/whisper-cli')
    if not model.is_file() or not cli.is_file():
        raise SafeError('Install Homebrew whisper.cpp and move large-v3 first; see docs.')
    verify_vad(model.parent / VAD_NAME)
    try:
        (batch / 'recordings').mkdir(parents=True, mode=0o700)
        (batch / 'history').mkdir(mode=0o700)
        entries = []
        history = []
        for row in selected:
            value = dict(row)
            value['recording'] = None
            if row['audio_url']:
                audio = recording_path(row['audio_url'], recordings)
                staged_audio = batch / 'recordings' / audio.name
                shutil.copyfile(audio, staged_audio)
                entries.append((row, staged_audio))
                value['recording'] = 'recordings/' + audio.name
            del value['audio_url']
            history.append(value)
        with tempfile.TemporaryDirectory(prefix='assessment-', dir=root) as directory:
            reviews, counts = assess_batch(entries, Path(directory), model, cli, vocabulary)
        # Include canceled history with no associated recording in the exclusion count.
        counts['excluded_cancelled'] = sum(r['status'] == 'canceled' for r in selected)
        for item in reviews:
            del item['audio_url']
        with (batch / 'history' / (run_id + '.jsonl')).open('w', encoding='utf-8') as stream:
            for row in history:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        items = rank(reviews)
        write_json(building / 'review.json', {'version': 1, 'run_id': run_id, 'items': items})
        summary = {'version': 1, 'run_id': run_id, 'rows': len(selected),
                   'recordings': len(entries), 'assessed': len(reviews),
                   **counts, 'items': len(items)}
        write_json(building / 'summary.json', summary)
        # Persist the transaction BEFORE moving staging to its final run folder.
        # A crash between these steps is resumed by execute(), never ignored.
        write_json(pending, dict(summary, ids=[r['id'] for r in selected], last_run=time.time()))
    except BaseException:
        if not pending.exists():
            shutil.rmtree(building, ignore_errors=True)
        raise


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
    run_quiet(['/opt/homebrew/bin/rsync', '-rt', '--ignore-existing', '-e', ssh,
               '--', str(batch) + '/', NODE + ':./'])


def latest_run(root):
    state = json.loads((root / 'recheck-state.json').read_text())
    run_id = state['latest_run']
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise SafeError('Invalid latest run identifier.')
    return root / 'rechecks' / run_id


def reassess(root=PRIVATE, voice=VOICEINK):
    """Assess only saved copies; never upload or modify the processed ledger."""
    os.umask(0o077)
    with (root / 'recheck.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SafeError('Weekly re-check already running.') from None
        if (root / 'recheck-pending.json').exists():
            raise SafeError('Pending weekly copy; finish it before re-assessment.')
        run = latest_run(root)
        # The review server uses the same lock for every review in this batch.
        with (run / 'review.lock').open('a') as review_lock:
            try:
                fcntl.flock(review_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SafeError('Close the current review before re-assessment.') from None
            model = root / 'models/ggml-large-v3.bin'
            cli = Path('/opt/homebrew/bin/whisper-cli')
            if not model.is_file() or not cli.is_file():
                raise SafeError('Install Homebrew whisper.cpp and move large-v3 first; see docs.')
            verify_vad(model.parent / VAD_NAME)
            vocabulary = snapshot_vocabulary(voice / 'dictionary.store', root)
            history = run / 'upload/history' / (run.name + '.jsonl')
            rows = [json.loads(line) for line in history.read_text().splitlines()]
            entries = []
            for row in rows:
                if row['recording']:
                    relative = Path(row['recording'])
                    if relative.parts != ('recordings', relative.name):
                        raise SafeError('Invalid copied recording path.')
                    audio = recording_path((run / 'upload' / relative).as_uri(), run / 'upload/recordings')
                    entries.append((row, audio))
            with tempfile.TemporaryDirectory(prefix='assessment-', dir=root) as directory:
                reviews, counts = assess_batch(entries, Path(directory), model, cli, vocabulary)
            counts['excluded_cancelled'] = sum(r['status'] == 'canceled' for r in rows)
            items = rank(reviews)
            from review import supersede_corrections, private_json
            # Assessment must succeed before invalidating any previous submissions.
            supersede_corrections(run)
            if (run / 'review.json').exists():
                archived = run / 'superseded-reviews' / (uuid.uuid4().hex + '.json')
                private_json(archived, json.loads((run / 'review.json').read_text()))
            write_json(run / 'review.json', {'version': 1, 'run_id': run.name, 'items': items})
            write_json(run / 'summary.json', dict(version=1, run_id=run.name,
                       rows=len(rows), recordings=len(entries), assessed=len(reviews), items=len(items), **counts))
    print('Latest batch re-assessed; no upload; processed state unchanged.')


def status(root=PRIVATE):
    if not (root / 'recheck-state.json').exists():
        summary = {}
    else:
        run = latest_run(root)
        summary_file = run / 'summary.json'
        summary = json.loads(summary_file.read_text()) if summary_file.exists() else {}
    print('Reviewed: {}; excluded as cancelled: {}; excluded as silent: {}; '
          'loops discarded: {}; items flagged: {}.'
          .format(*(summary.get(key, 0) for key in
                    ('assessed', 'excluded_cancelled', 'excluded_silent', 'loops_discarded', 'items'))))


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
            if (root / 'recheck-building').exists():
                shutil.rmtree(root / 'recheck-building')
            selected = new_rows(rows, state)
            if not selected:
                print('New history rows: 0; no upload needed.')
                return
            vocabulary = snapshot_vocabulary(voice / 'dictionary.store', root)
            build_run(selected, root, voice / 'Recordings', uuid.uuid4().hex, pending, vocabulary)
        transaction = json.loads(pending.read_text())
        run_dir = root / 'rechecks' / transaction['run_id']
        if not run_dir.exists():
            run_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            (root / 'recheck-building').replace(run_dir)
        key = root / 'dictation-node-key'
        if key.stat().st_mode & 0o077:
            raise SafeError('Job key must be private (0600).')
        send(run_dir / 'upload', key)
        write_json(state_path, {'version': 1, 'last_run': transaction['last_run'],
                               'processed_ids': sorted(set(state['processed_ids']) | set(transaction['ids'])),
                               'latest_run': transaction['run_id']})
        pending.unlink()
        if transaction['items']:
            try:
                from review import notify
                notify(run_dir / 'review.json', transaction['items'], root)
            except Exception:
                print('Review notification unavailable; run review.py by hand.')
        print('History rows: {rows}; recordings: {recordings}; unreadable: {unreadable}; '
              'cancelled: {excluded_cancelled}; silent: {excluded_silent}; '
              'loops discarded: {loops_discarded}; review items: {items}; copy complete.'
              .format(**dict(dict(unreadable=0, excluded_cancelled=0, excluded_silent=0,
                                  loops_discarded=0), **transaction)))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 2 and sys.argv[1] in ('disable', 'enable', 'status', 'download-vad', 'reassess'):
            if sys.argv[1] == 'status':
                status()
            elif sys.argv[1] == 'download-vad':
                download_vad()
            elif sys.argv[1] == 'reassess':
                reassess()
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
            raise SafeError('Usage: recheck.py [disable|enable|status|download-vad|reassess]')
    except SafeError as error:
        print(str(error))
        raise SystemExit(1) from None
    except Exception:
        # Never print exception values, traceback, command arguments or data-derived paths.
        print('Weekly re-check failed; private state retained. No content logged.')
        raise SystemExit(1) from None
