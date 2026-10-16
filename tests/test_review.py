"""Only generated sentences and silence; never inspect owner data."""
import http.client
import io
import json
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

import review as r


def fixture(root):
    directory = root / 'synthetic-review'
    (directory / 'upload/recordings').mkdir(parents=True)
    items = []
    for i, (wrong, right) in enumerate([
            ('The flarnet is ready.', 'The clarinet is ready.'),
            ('A flarnet sounds clear.', 'A clarinet sounds clear.'),
            ('The door is open.', 'The floor is open.')]):
        name = str(i) + '.wav'
        with wave.open(str(directory / 'upload/recordings' / name), 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 2000)
        items.append(dict(id=str(i), text=wrong, cleaned_text=None, recheck=right,
                          recording='recordings/' + name, score=1 - i / 10))
    path = directory / 'review.json'
    r.private_json(path, {'version': 1, 'run_id': 'synthetic', 'items': items})
    return path


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = fixture(self.root)
        self.value = r.load_review(self.path)
        self.answers = [dict(id=str(i), choice='recheck' if i < 2 else 'inserted') for i in range(3)]

    def test_spelling_repeated_mishearing_and_inserted_right(self):
        records = r.corrections(self.value['items'], self.answers)
        archive = r.proposals(self.value['items'], records)
        self.assertEqual(archive['vocabulary'], [{'term': 'clarinet'}])
        self.assertEqual(archive['replacements'], [{'sources': ['flarnet'], 'replacement': 'clarinet'}])
        self.assertEqual(archive['format'], 'voiceink.dictionary')
        self.assertEqual(archive['schemaVersion'], 1)
        self.answers[0]['choice'] = 'inserted'
        self.assertEqual(r.proposals(self.value['items'], r.corrections(self.value['items'], self.answers))['replacements'], [])

    def test_typed_skip_and_cleaned_insertion(self):
        self.value['items'][2]['cleaned_text'] = 'The door stays open.'
        self.answers[0].update(choice='typed', text='The flute is ready.')
        self.answers[1]['choice'] = 'skip'
        records = r.corrections(self.value['items'], self.answers)
        self.assertIsNone(records[1]['text'])
        self.assertEqual(records[2]['text'], 'The door stays open.')
        self.assertEqual(r.proposals(self.value['items'], records)['vocabulary'], [{'term': 'flute'}])

    def test_conflicting_evidence_and_punctuation(self):
        self.answers[1].update(choice='typed', text='A trumpet sounds clear.')
        archive = r.proposals(self.value['items'], r.corrections(self.value['items'], self.answers))
        self.assertEqual(archive['replacements'], [])
        self.value['items'][0]['recheck'] = 'THE FLARNET is ready!'
        self.assertEqual(r.proposals(self.value['items'][:1], r.corrections(self.value['items'][:1], self.answers[:1]))['vocabulary'], [])

    def test_noise_is_own_choice_with_optional_note_and_no_learning(self):
        self.answers = [dict(id=str(i), choice='noise', note='synthetic note',
                             text='synthetic ignored text') for i in range(3)]
        records = r.corrections(self.value['items'], self.answers)
        self.assertEqual(records[0], dict(id='0', choice='noise', text=None, note='synthetic note'))
        proposals = r.proposals(self.value['items'], records)
        self.assertEqual((proposals['vocabulary'], proposals['replacements']), ([], []))
        self.answers[0].pop('note')
        self.assertEqual(r.corrections(self.value['items'], self.answers)[0]['note'], '')
        for note in (None, 'x' * 501):
            self.answers[0]['note'] = note
            with self.assertRaisesRegex(ValueError, 'noise note'):
                r.corrections(self.value['items'], self.answers)
        self.answers[0]['note'] = ''
        sent = []
        archive, _ = r.save(self.path, self.value, self.answers, self.root,
                            lambda batch, root: sent.append(batch))
        payload = json.loads(next(sent[0].glob('corrections/*.json')).read_text())
        self.assertEqual(payload['items'][0]['choice'], 'noise')
        self.assertIsNone(payload['items'][0]['text'])
        self.assertEqual(json.loads(archive.read_text())['vocabulary'], [])

    def test_noise_does_not_enter_conflicting_evidence_check(self):
        self.answers[2].update(choice='noise', note='synthetic')
        archive = r.proposals(self.value['items'], r.corrections(self.value['items'], self.answers))
        self.assertEqual(archive['replacements'], [{'sources': ['flarnet'], 'replacement': 'clarinet'}])

    def test_superseding_blocks_learning_import_and_retry_then_copies_revocations(self):
        # First submission represents the synthetic, now obsolete review.
        archive, _ = r.save(self.path, self.value, self.answers, self.root, lambda *args: None)
        run = self.path.parent
        old_path = next((run / 'correction-copies').glob('*/corrections/*.json'))
        old_id = old_path.stem
        original_remote_copy = old_path.read_bytes()
        self.assertEqual(len(list(r.active_corrections(run))), 1)
        with patch.object(r.recheck, 'send') as send:
            r.supersede_corrections(run)
            r.supersede_corrections(run)  # Repeatable after interruptions.
        send.assert_not_called()
        self.assertFalse(archive.exists())
        self.assertEqual(list(r.active_corrections(run)), [])
        self.assertTrue(json.loads(old_path.read_text())['superseded'])
        self.assertTrue(json.loads((run / 'review-result.json').read_text())['superseded'])
        with patch.object(r.recheck, 'send') as send:
            with self.assertRaisesRegex(ValueError, 'superseded'):
                r.copy_corrections(old_path.parent.parent, self.root)
            with patch.object(r.sys, 'argv', ['review.py', '--review', str(self.path), '--retry-copies']), \
                    patch('builtins.print'):
                r.main()
        send.assert_not_called()
        self.assertTrue(json.loads((run / 'review-result.json').read_text())['superseded'])
        sent = []
        new_archive, _ = r.save(self.path, self.value, self.answers, self.root,
                                lambda batch, root: sent.append(batch))
        self.assertTrue(new_archive.exists())
        active = list(r.active_corrections(run))
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['supersedes'], [old_id])
        self.assertTrue((sent[0] / 'supersessions' / (old_id + '.json')).exists())
        # Model an additive restored node copy: old bytes never change remotely.
        restored = self.root / 'synthetic-restored-backup'
        r.private_json(restored / 'corrections' / (old_id + '.json'), json.loads(original_remote_copy))
        new_payload = next(sent[0].glob('corrections/*.json'))
        r.private_json(restored / 'corrections' / new_payload.name, json.loads(new_payload.read_text()))
        self.assertEqual(list(r.active_corrections(restored)), active)
        for path in (run / 'supersessions').glob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_choice_or_identity_rejected_before_writing(self):
        for answers in [[], [dict(id='bad', choice='recheck')] * 3,
                        [dict(id=str(i), choice='bad') for i in range(3)]]:
            with self.assertRaises(ValueError):
                r.save(self.path, self.value, answers, self.root)
        self.assertFalse((self.path.parent / 'correction-copies').exists())

    def test_audio_path_escape_and_symlink_rejected(self):
        item = self.value['items'][0]
        for name in ['../other.wav', '/tmp/other.wav', 'recordings/../other.wav']:
            with self.assertRaises(ValueError):
                r.audio_path(self.path, dict(item, recording=name))
        audio = r.audio_path(self.path, item)
        audio.unlink()
        audio.symlink_to(self.path)
        with self.assertRaises(ValueError):
            r.audio_path(self.path, item)

    def start(self, **kwargs):
        server = r.ReviewServer(self.path, self.root, open_files=False, **kwargs)
        thread = threading.Thread(target=server.run)
        thread.start()
        self.addCleanup(lambda: (setattr(server, 'finished', True), thread.join(3), server.server_close()))
        return server, thread

    def request(self, server, route='', method='GET', value=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        data = json.dumps(value) if value is not None else None
        actual = {'Origin': f'http://127.0.0.1:{server.server_port}', 'Content-Type': 'application/json'}
        actual.update(headers or {})
        connection.request(method, '/' + server.token + '/' + route, data, actual)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_binding_idle_exit_and_closed_socket(self):
        server, thread = self.start(idle=.15)
        self.assertEqual(server.socket.getsockname()[0], '127.0.0.1')
        started = time.monotonic()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1)
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', server.server_port), timeout=.1)

    def test_page_audio_and_save_then_exit_private_files(self):
        sent = []
        server, thread = self.start(idle=3, sender=lambda batch, root: sent.append(batch))
        self.assertEqual(self.request(server)[0], 200)
        self.assertEqual(self.request(server, 'items')[0], 200)
        status, audio = self.request(server, 'audio/0')
        self.assertEqual(status, 200)
        self.assertTrue(audio.startswith(b'RIFF'))
        status, fragment = self.request(server, 'audio/0', headers={'Range': 'bytes=0-3'})
        self.assertEqual((status, fragment), (206, b'RIFF'))
        status, body = self.request(server, 'save', 'POST', self.answers)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['copied'])
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(sent), 1)
        output = next(sent[0].glob('corrections/*.json'))
        self.assertEqual(len(json.loads(output.read_text())['items']), 3)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        archive = self.path.parent / json.loads(body)['import_file']
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(archive.read_text())['schemaVersion'], 1)
        for path in (self.path.parent / 'correction-copies').rglob('*'):
            self.assertFalse(path.stat().st_mode & 0o077)

    def test_sentence_endpoint_enforces_time_bounds_before_byte_ranges(self):
        item = self.value['items'][0]
        item.update(dictation_id='synthetic', sentence_index=0, source_field='text',
                    text_start=0, text_end=len(item['text']), start=1, end=2)
        audio = r.audio_path(self.path, item)
        frames = b'\x01\x00' * 8000 + b'\x02\x00' * 8000 + b'\x03\x00' * 8000
        with wave.open(str(audio), 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(frames)
        r.private_json(self.path, self.value)
        server, _ = self.start(idle=3)
        route = 'audio/0?start=0.65&end=2.35'
        status, data = self.request(server, route)
        self.assertEqual(status, 200)
        with wave.open(io.BytesIO(data)) as wav:
            self.assertEqual(wav.getnframes(), 13600)
            self.assertEqual(wav.readframes(13600), frames[5200 * 2:18800 * 2])
        status, body = self.request(server, route, headers={'Range': 'bytes=44-'})
        self.assertEqual((status, body), (206, data[44:]))
        self.assertEqual(self.request(server, 'audio/0')[1], data)
        for query in ['start=0&end=3', 'start=nan&end=2', 'start=1&end=2&end=3']:
            self.assertEqual(self.request(server, 'audio/0?' + query), (404, b''))
        self.assertEqual(self.request(server, route, method='HEAD'), (200, b''))

    def test_audio_mime_wav_head_and_browser_ranges(self):
        server, _ = self.start(idle=3)
        def fetch(method='GET', headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request(method, '/' + server.token + '/audio/0', headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            result = response.status, dict(response.getheaders()), body
            connection.close()
            return result
        status, headers, data = fetch()
        self.assertEqual((status, headers['Content-Type'], headers['Accept-Ranges']),
                         (200, 'audio/wav', 'bytes'))
        with wave.open(io.BytesIO(data)) as audio:
            self.assertEqual(audio.getnframes(), 2000)
            self.assertEqual(audio.getframerate(), 8000)
        status, headers, body = fetch('HEAD')
        self.assertEqual((status, body, int(headers['Content-Length'])), (200, b'', len(data)))
        for requested, first, last in [('bytes=0-1', 0, 1), ('bytes=44-', 44, len(data)-1),
                                       ('bytes=-16', len(data)-16, len(data)-1)]:
            status, headers, body = fetch(headers={'Range': requested})
            self.assertEqual(status, 206)
            self.assertEqual(headers['Content-Type'], 'audio/wav')
            self.assertEqual(headers['Content-Range'], f'bytes {first}-{last}/{len(data)}')
            self.assertEqual(body, data[first:last+1])
        for invalid in ['bytes=-0', 'bytes=99999-', 'bytes=4-2', 'bytes=-', 'bytes=0-1,4-5']:
            status, headers, _ = fetch(headers={'Range': invalid})
            self.assertEqual(status, 416)
            self.assertEqual(headers['Content-Range'], f'bytes */{len(data)}')

    def test_no_copy_flag_and_marker_never_call_sender_even_with_key(self):
        (self.root / 'dictation-node-key').touch(mode=0o600)
        for marked in [False, True]:
            if marked:
                (self.path.parent / '.review-no-copy').touch(mode=0o600)
            sender = Mock()
            server, thread = self.start(idle=3, sender=sender, no_copy=not marked)
            status, body = self.request(server, 'save', 'POST', self.answers)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['copy_disabled'])
            self.assertFalse(json.loads(body)['copied'])
            thread.join(2)
            sender.assert_not_called()
            result = json.loads((self.path.parent / 'review-result.json').read_text())
            self.assertTrue(result['copy_disabled'])

    def test_no_copy_blocks_retry_before_key_or_network(self):
        for options in [[], ['--no-copy']]:
            if not options:
                (self.path.parent / '.review-no-copy').touch(mode=0o600)
            else:
                (self.path.parent / '.review-no-copy').unlink()
            with patch.object(r.sys, 'argv', ['review.py', '--review', str(self.path), '--retry-copies', *options]), \
                    patch.object(r, 'copy_corrections') as copy:
                with self.assertRaisesRegex(ValueError, 'copying disabled'):
                    r.main()
                copy.assert_not_called()

    def test_upload_failure_retains_immutable_retry_batch(self):
        def fail(*args):
            raise OSError('synthetic')
        archive, copied = r.save(self.path, self.value, self.answers, self.root, fail)
        self.assertFalse(copied)
        self.assertTrue(archive.exists())
        self.assertEqual(len(list((self.path.parent / 'correction-copies').glob('*/corrections/*.json'))), 1)
        # A second review creates a new name; --ignore-existing never hides its corrections.
        r.save(self.path, self.value, self.answers, self.root, fail)
        self.assertEqual(len(list((self.path.parent / 'correction-copies').glob('*/corrections/*.json'))), 2)

    def test_csrf_dns_rebinding_and_missing_token(self):
        server, _ = self.start(idle=3)
        self.assertEqual(self.request(server, 'save', 'POST', self.answers, {'Origin': 'https://example.invalid'})[0], 403)
        self.assertEqual(self.request(server, headers={'Host': 'example.invalid'})[0], 403)
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        connection.request('GET', '/items')
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()
        self.assertFalse((self.path.parent / 'review-result.json').exists())

    def test_copy_uses_w1_key_and_mechanism(self):
        key = self.root / 'dictation-node-key'
        key.touch(mode=0o600)
        with patch.object(r.recheck, 'send') as send:
            r.copy_corrections(self.root / 'batch', self.root)
            send.assert_called_once_with(self.root / 'batch', key)
        key.chmod(0o644)
        with self.assertRaises(ValueError):
            r.copy_corrections(self.root, self.root)

    def test_notification_marker_shell_check_present_and_absent(self):
        # Spaces, quotes and shell metacharacters must remain literal path characters.
        marker = self.root / "marker ' $(false)"
        command = r.marker_check_command(marker)
        for present in [False, True, False]:
            if present:
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
            result = subprocess.run(['/bin/sh', '-c', command], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout, 'yes' if present else 'no')
            self.assertEqual(result.stderr, '')

    def test_notification_signing_failure_prevents_launch(self):
        app = self.root / 'Weekly Dictation Review.app'
        (app / 'Contents').mkdir(parents=True)
        info_path = app / 'Contents/Info.plist'
        info_path.write_bytes(plistlib.dumps({}))
        def command(args):
            if args[0] == '/usr/bin/codesign':
                # The identity must already be in Info.plist when codesigning starts.
                self.assertEqual(plistlib.loads(info_path.read_bytes())['CFBundleIdentifier'],
                                 'org.mac-dictation.review')
                raise r.recheck.SafeError('Synthetic signing failure')
        with patch.object(r.recheck, 'run_quiet', side_effect=command) as run:
            with self.assertRaises(r.recheck.SafeError):
                r.notify(self.path, 3, self.root)
        self.assertFalse(any(call.args[0][0] == '/usr/bin/open' for call in run.call_args_list))
        self.assertFalse((self.root / 'review-notification-request').exists())

    def test_notification_compiles_app_with_click_handler_and_count_only(self):
        app = self.root / 'Weekly Dictation Review.app'
        (app / 'Contents').mkdir(parents=True)
        info_path = app / 'Contents/Info.plist'
        info_path.write_bytes(plistlib.dumps({'CFBundleName': 'Weekly Dictation Review'}))
        with patch.object(r.recheck, 'run_quiet') as run:
            r.notify(self.path, 3, self.root)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ['/usr/bin/osacompile', '/usr/bin/codesign', '/usr/bin/codesign', '/usr/bin/open'])
        self.assertEqual(plistlib.loads(info_path.read_bytes()),
                         {'CFBundleName': 'Weekly Dictation Review', 'CFBundleIdentifier': 'org.mac-dictation.review'})
        self.assertEqual(run.call_args_list[1].args[0],
                         ['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', 'org.mac-dictation.review', str(app)])
        self.assertEqual(run.call_args_list[2].args[0],
                         ['/usr/bin/codesign', '--verify', '--strict', str(app)])
        source = (self.root / 'review-notification.applescript').read_text()
        self.assertIn('3 sentences to review', source)
        self.assertIn('do shell script', source)
        self.assertNotIn('argv', source)
        self.assertNotIn('as alias', source)
        self.assertNotIn('on error number', source)
        self.assertIn(r.applescript_string(r.marker_check_command(self.root / 'review-notification-request')), source)
        self.assertNotIn('--args', run.call_args_list[-1].args[0])
        self.assertTrue((self.root / 'review-notification-request').is_file())
        notice_branch, click_branch = source.split('else\n')
        self.assertNotIn('--review', notice_branch)
        self.assertIn('--review', click_branch)
        self.assertLess(notice_branch.index('/bin/rm'), notice_branch.index('display notification'))
        self.assertNotIn('flarnet', source)
        with patch.object(r.recheck, 'run_quiet'):
            r.notify(self.path, 3, self.root, no_copy=True)
        self.assertIn('--no-copy', (self.root / 'review-notification.applescript').read_text())
        (self.root / 'review-notifications-disabled').touch()
        with patch.object(r.recheck, 'run_quiet') as run:
            r.notify(self.path, 3, self.root)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
