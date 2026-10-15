"""Synthetic-only fixtures; no VoiceInk owner files are opened."""
import json
import hashlib
import subprocess
import wave
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

import recheck as r


class RecheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / 'default.store'
        self.db = sqlite3.connect(self.store)
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA journal_mode=WAL')
        # Independent explicit fixture schema, corresponding to upstream's model fields.
        self.db.execute('''CREATE TABLE ZTRANSCRIPTION (
            Z_PK INTEGER, Z_ENT INTEGER, Z_OPT INTEGER, ZID BLOB,
            ZTEXT VARCHAR, ZENHANCEDTEXT VARCHAR, ZTIMESTAMP TIMESTAMP, ZDURATION FLOAT,
            ZAUDIOFILEURL VARCHAR, ZTRANSCRIPTIONSTATUS VARCHAR,
            ZTRANSCRIPTIONMODELNAME VARCHAR, ZAIENHANCEMENTMODELNAME VARCHAR,
            ZPROMPTNAME VARCHAR, ZTRANSCRIPTIONDURATION FLOAT, ZENHANCEMENTDURATION FLOAT,
            ZAIREQUESTSYSTEMMESSAGE VARCHAR, ZAIREQUESTUSERMESSAGE VARCHAR,
            ZMODENAME VARCHAR, ZMODEEMOJI VARCHAR)''')
        self.db.execute('CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME VARCHAR)')
        self.db.execute("INSERT INTO Z_PRIMARYKEY VALUES (1, 'Transcription')")
        self.db.commit()

    def add(self, pk=1, status='completed', timestamp=100):
        identifier = uuid.UUID(int=pk)
        self.db.execute('INSERT INTO ZTRANSCRIPTION '
                        '(Z_PK,Z_ENT,ZID,ZTIMESTAMP,ZTEXT,ZTRANSCRIPTIONSTATUS) '
                        'VALUES (?,1,?,?,?,?)', (pk, identifier.bytes, timestamp, 'synthetic', status))
        self.db.commit()
        return str(identifier)

    def hashes(self):
        return {suffix: hashlib.sha256(Path(str(self.store) + suffix).read_bytes()).hexdigest()
                for suffix in ('', '-wal', '-shm')}

    def test_snapshot_includes_wal_and_leaves_source_unchanged(self):
        self.add()
        before = self.hashes()
        rows = r.snapshot_rows(self.store, self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['timestamp'], r.APPLE_EPOCH + 100)
        self.assertEqual(before, self.hashes())

    def test_concurrent_writer_and_no_source_modifications_by_reader(self):
        self.add()
        writer = sqlite3.connect(self.store)
        self.addCleanup(writer.close)
        copyfile = r.shutil.copyfile
        connect = r.sqlite3.connect
        expected = []
        before_copy = self.hashes()

        def copying(source, target):
            result = copyfile(source, target)
            if source == self.store:
                # Commit on a second connection between the database and WAL copies.
                writer.execute("UPDATE ZTRANSCRIPTION SET ZTEXT='synthetic concurrent'")
                writer.commit()
                expected.append(self.hashes())
            return result

        def open_copy_only(database, *args, **kwargs):
            self.assertNotIn(self.store.as_uri(), str(database))
            return connect(database, *args, **kwargs)

        with patch.object(r.shutil, 'copyfile', side_effect=copying), \
                patch.object(r.sqlite3, 'connect', side_effect=open_copy_only):
            rows = r.snapshot_rows(self.store, self.root)
        self.assertEqual(rows[0]['text'], 'synthetic concurrent')
        self.assertNotEqual(before_copy['-wal'], expected[0]['-wal'])
        # The writer's deliberate commit is the only permitted source change.
        # All three source hashes after that commit equal hashes after the reader finishes.
        self.assertEqual(expected[0], self.hashes())

    def test_torn_snapshot_retries_once(self):
        self.add()
        copy = r.copy_snapshot
        calls = []

        def torn_first(source, snapshot):
            calls.append(1)
            if len(calls) == 1:
                snapshot.write_bytes(b'synthetic torn snapshot')
            else:
                copy(source, snapshot)

        with patch.object(r, 'copy_snapshot', side_effect=torn_first), patch.object(r.time, 'sleep') as sleep:
            self.assertEqual(len(r.snapshot_rows(self.store, self.root)), 1)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(10)
        self.assertFalse(any(self.root.glob('snapshot-*')))

    def test_two_torn_snapshots_skip_without_state_change(self):
        state = self.root / 'recheck-state.json'
        state.write_text('{"processed_ids": []}')
        before = state.read_bytes()
        with patch.object(r, 'copy_snapshot', side_effect=lambda source, target: target.write_bytes(b'torn')), \
                patch.object(r.time, 'sleep') as sleep:
            with self.assertRaisesRegex(r.SafeError, 'failed twice'):
                r.execute(self.root, self.root)
        self.assertEqual(state.read_bytes(), before)
        sleep.assert_called_once_with(10)
        self.assertFalse(any(self.root.glob('snapshot-*')))

    def test_uncommitted_row_excluded(self):
        self.add()
        self.db.execute("UPDATE ZTRANSCRIPTION SET ZTEXT='uncommitted'")
        self.assertEqual(r.snapshot_rows(self.store, self.root)[0]['text'], 'synthetic')

    def test_schema_mismatch(self):
        self.db.execute('ALTER TABLE ZTRANSCRIPTION ADD COLUMN ZNEW VARCHAR')
        self.db.commit()
        with self.assertRaisesRegex(r.SafeError, 'layout differs'):
            r.snapshot_rows(self.store, self.root)
        self.assertFalse(any(self.root.glob('snapshot-*')))

    def test_since_last_run_with_ties_late_commits_and_pending(self):
        first = self.add()
        self.add(2)  # same timestamp, different ID
        self.add(3, 'pending', 50)
        rows = r.snapshot_rows(self.store, self.root)
        self.assertEqual([x['id'] for x in r.new_rows(rows, {'processed_ids': [first]})],
                         [str(uuid.UUID(int=2))])
        self.db.execute("UPDATE ZTRANSCRIPTION SET ZTRANSCRIPTIONSTATUS='completed' WHERE Z_PK=3")
        self.db.commit()
        self.assertEqual(len(r.new_rows(r.snapshot_rows(self.store, self.root),
                                       {'processed_ids': [first]})), 2)

    def test_normalization_and_distance(self):
        self.assertEqual(r.disagreement('HELLO,   café!', 'hello café'), 0)
        self.assertEqual(r.disagreement('one two', 'one three'), .5)
        self.assertEqual(r.disagreement('', ''), 0)
        self.assertEqual(r.disagreement('', 'one'), 1)

    def test_top_twenty_descending_positive_only(self):
        items = [dict(id=str(i), score=i / 30) for i in range(30)]
        result = r.rank(items)
        self.assertEqual(len(result), 20)
        self.assertEqual(result[0]['score'], 29 / 30)
        self.assertEqual(r.rank([dict(id='zero', score=0)]), [])

    def test_recording_confinement(self):
        recordings = self.root / 'Recordings'
        recordings.mkdir()
        audio = recordings / (str(uuid.UUID(int=1)) + '.wav')
        audio.touch()
        self.assertEqual(r.recording_path(audio.as_uri(), recordings), audio)
        audio.unlink()
        audio.symlink_to(self.store)
        with self.assertRaises(r.SafeError):
            r.recording_path(audio.as_uri(), recordings)
        with self.assertRaises(r.SafeError):
            r.recording_path(self.store.as_uri(), recordings)

    def test_transfer_is_additive_and_uses_only_job_identity(self):
        config = 'hostname synthetic.invalid\nport 22\n'
        with patch.object(r.subprocess, 'run') as run, patch.object(r, 'run_quiet') as quiet:
            run.return_value.stdout = config
            r.send(self.root, self.root / 'key')
        args = quiet.call_args.args[0]
        self.assertEqual(args[0], '/opt/homebrew/bin/rsync')
        self.assertIn('--ignore-existing', args)
        self.assertNotIn('--delete', args)
        self.assertIn('-F /dev/null', args[args.index('-e') + 1])
        self.assertIn('IdentityAgent=none', args[args.index('-e') + 1])

    def test_full_synthetic_run_creates_private_artifacts_and_second_run_skips(self):
        identifier = self.add()
        recordings = self.root / 'Recordings'
        recordings.mkdir()
        audio = recordings / (identifier + '.wav')
        audio.write_bytes(b'synthetic audio fixture')
        self.db.execute('UPDATE ZTRANSCRIPTION SET ZAUDIOFILEURL=?, ZENHANCEDTEXT=?',
                        (audio.as_uri(), 'synthetic cleaned'))
        self.db.commit()
        root = self.root / 'private'
        (root / 'models').mkdir(parents=True, mode=0o700)
        (root / 'models/ggml-large-v3.bin').touch(mode=0o600)
        (root / 'dictation-node-key').touch(mode=0o600)
        original_is_file = Path.is_file

        def installed(path):
            return str(path) == '/opt/homebrew/bin/whisper-cli' or original_is_file(path)

        def whisper(args, **kwargs):
            Path(args[args.index('-of') + 1] + '.txt').write_text('synthetic different')
            return subprocess.CompletedProcess(args, 0, stderr=b'')

        with patch.object(Path, 'is_file', installed), patch.object(r.subprocess, 'run', side_effect=whisper), \
                patch.object(r, 'send') as send, patch('builtins.print'):
            r.execute(root, self.root)
            r.execute(root, self.root)
        send.assert_called_once()
        state = json.loads((root / 'recheck-state.json').read_text())
        run = root / 'rechecks' / state['latest_run']
        review = json.loads((run / 'review.json').read_text())
        self.assertEqual(len(review['items']), 1)
        item = review['items'][0]
        self.assertEqual(item['recording'], 'recordings/' + audio.name)
        self.assertEqual(item['text'], 'synthetic')
        self.assertEqual(item['cleaned_text'], 'synthetic cleaned')
        self.assertNotIn('audio_url', item)
        history = json.loads(next((run / 'upload/history').glob('*.jsonl')).read_text())
        self.assertEqual(history['id'], identifier)
        self.assertNotIn('audio_url', history)
        for path in root.rglob('*'):
            self.assertFalse(path.stat().st_mode & 0o077)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def audio_fixture(self, name, frames):
        path = self.root / name
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b'\0\0' * frames)
        return path

    def test_batch_loads_once_and_skips_empty_and_decoder_failure(self):
        good = self.audio_fixture('good.wav', 100)
        empty = self.audio_fixture('empty.wav', 0)
        broken = self.root / 'broken.wav'
        broken.write_bytes(b'synthetic invalid audio')
        row = {'id': 'synthetic', 'text': 'synthetic', 'audio_url': 'synthetic'}
        work = self.root / 'work'
        work.mkdir()

        def whisper(args, **kwargs):
            inputs = [args[i + 1] for i, value in enumerate(args) if value == '-f']
            outputs = [args[i + 1] for i, value in enumerate(args) if value == '-of']
            self.assertEqual(inputs, [str(good), str(broken)])
            self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
            self.assertEqual(kwargs['stderr'], subprocess.PIPE)
            Path(outputs[0] + '.txt').write_text('synthetic recheck')
            error = ("error: failed to read audio file '" + str(broken) + "'\n"
                     'error: 1 input file(s) failed\n').encode()
            return subprocess.CompletedProcess(args, 11, stderr=error)

        with patch.object(r.subprocess, 'run', side_effect=whisper) as run:
            reviews, unreadable = r.assess_batch([(row, good), (row, empty), (row, broken)],
                                                work, self.root / 'model', Path('cli'))
        run.assert_called_once()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(unreadable, 2)

    def test_all_empty_audio_skips_cli_entirely(self):
        empty = self.audio_fixture('empty.wav', 0)
        with patch.object(r.subprocess, 'run') as run:
            self.assertEqual(r.assess_batch([({}, empty)], self.root, Path('model'), Path('cli')), ([], 1))
        run.assert_not_called()

    def test_runtime_failure_and_output_failure_are_fatal(self):
        audio = self.audio_fixture('good.wav', 100)
        for status, error in ((3, b'synthetic model failure'),
                              (11, b'error: 1 input file(s) failed')):
            with self.subTest(status=status), patch.object(r.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], status, stderr=error)):
                with self.assertRaises(r.SafeError):
                    r.assess_batch([({'text': 'synthetic'}, audio)], self.root, Path('model'), Path('cli'))

    def test_decoder_error_does_not_mask_an_output_write_error(self):
        good = self.audio_fixture('good.wav', 100)
        broken = self.root / 'broken.wav'
        broken.write_bytes(b'synthetic broken audio')
        row = {'id': 'synthetic', 'text': 'synthetic'}

        def whisper(args, **kwargs):
            outputs = [args[i + 1] for i, value in enumerate(args) if value == '-of']
            Path(outputs[0] + '.txt').write_text('synthetic partial output')
            error = ("error: failed to read audio file '" + str(broken) + "'\n"
                     'error: 2 input file(s) failed\n').encode()
            return subprocess.CompletedProcess(args, 11, stderr=error)

        with patch.object(r.subprocess, 'run', side_effect=whisper):
            with self.assertRaisesRegex(r.SafeError, 'batch failed'):
                r.assess_batch([(row, good), (row, broken)], self.root, Path('model'), Path('cli'))

    def test_empty_recording_and_history_are_backed_up_with_summary(self):
        identifier = self.add()
        recordings = self.root / 'Recordings'
        recordings.mkdir()
        audio = self.audio_fixture('empty.wav', 0)
        target = recordings / (identifier + '.wav')
        audio.rename(target)
        self.db.execute('UPDATE ZTRANSCRIPTION SET ZAUDIOFILEURL=?', (target.as_uri(),))
        self.db.commit()
        root = self.root / 'private'
        (root / 'models').mkdir(parents=True, mode=0o700)
        (root / 'models/ggml-large-v3.bin').touch(mode=0o600)
        (root / 'dictation-node-key').touch(mode=0o600)
        is_file = Path.is_file
        with patch.object(Path, 'is_file', lambda path: str(path) == '/opt/homebrew/bin/whisper-cli' or is_file(path)), \
                patch.object(r, 'send') as send, patch.object(r.subprocess, 'run') as run, patch('builtins.print'):
            r.execute(root, self.root)
        send.assert_called_once()
        run.assert_not_called()
        state = json.loads((root / 'recheck-state.json').read_text())
        run_dir = root / 'rechecks' / state['latest_run']
        summary = json.loads((run_dir / 'summary.json').read_text())
        self.assertEqual((summary['recordings'], summary['assessed'], summary['unreadable']), (1, 0, 1))
        self.assertEqual((run_dir / 'upload/recordings' / target.name).read_bytes(), target.read_bytes())
        self.assertEqual(len(list((run_dir / 'upload/history').glob('*.jsonl'))), 1)
        self.assertEqual(state['processed_ids'], [identifier])
        self.assertFalse((root / 'recheck-building').exists())

    def test_pre_upload_failure_removes_building_and_retry_replaces_crash_staging(self):
        root = self.root / 'private'
        (root / 'models').mkdir(parents=True, mode=0o700)
        (root / 'models/ggml-large-v3.bin').touch()
        pending = root / 'recheck-pending.json'
        with patch.object(Path, 'is_file', return_value=True), \
                patch.object(r, 'assess_batch', side_effect=r.SafeError('synthetic model error')):
            with self.assertRaises(r.SafeError):
                r.build_run([], root, self.root, 'synthetic', pending)
        self.assertFalse((root / 'recheck-building').exists())
        self.assertFalse(pending.exists())
        (root / 'recheck-building').mkdir()
        (root / 'recheck-building/stale').touch()
        with patch.object(Path, 'is_file', return_value=True), patch.object(r, 'assess_batch', return_value=([], 0)):
            r.build_run([], root, self.root, 'synthetic', pending)
        self.assertFalse((root / 'recheck-building/stale').exists())
        self.assertTrue(pending.exists())

    def test_pending_staging_is_promoted_and_resumed_after_crash(self):
        self.add()
        root = self.root / 'private'
        (root / 'recheck-building/upload').mkdir(parents=True)
        (root / 'dictation-node-key').touch(mode=0o600)
        pending = dict(run_id='synthetic', ids=['new'], last_run=123, rows=1,
                       recordings=0, items=0, unreadable=1)
        (root / 'recheck-pending.json').write_text(json.dumps(pending))
        with patch.object(r, 'send') as send, patch('builtins.print'):
            r.execute(root, self.root)
        send.assert_called_once_with(root / 'rechecks/synthetic/upload', root / 'dictation-node-key')
        self.assertTrue((root / 'rechecks/synthetic/upload').is_dir())
        self.assertFalse((root / 'recheck-building').exists())
        self.assertFalse((root / 'recheck-pending.json').exists())

    def test_disabled_job_does_not_read_source(self):
        (self.root / 'recheck-disabled').touch()
        with patch.object(r, 'snapshot_rows') as snapshot, patch('builtins.print'):
            r.execute(self.root, self.root)
        snapshot.assert_not_called()

    def test_failed_upload_retries_saved_batch_before_advancing_state(self):
        self.add()
        root = self.root / 'private'
        root.mkdir()
        key = root / 'dictation-node-key'
        key.touch(mode=0o600)
        pending = dict(run_id='synthetic', ids=['new'], last_run=123,
                       rows=1, recordings=0, items=0)
        (root / 'rechecks/synthetic/upload').mkdir(parents=True)
        (root / 'recheck-pending.json').write_text(json.dumps(pending))
        with patch.object(r, 'send', side_effect=r.SafeError('synthetic failure')):
            with self.assertRaises(r.SafeError):
                r.execute(root, self.root)
        self.assertFalse((root / 'recheck-state.json').exists())
        self.assertTrue((root / 'recheck-pending.json').exists())
        with patch.object(r, 'send'), patch('builtins.print'):
            r.execute(root, self.root)
        self.assertEqual(json.loads((root / 'recheck-state.json').read_text())['processed_ids'], ['new'])
        self.assertFalse((root / 'recheck-pending.json').exists())


if __name__ == '__main__':
    unittest.main()
