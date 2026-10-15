#!/opt/homebrew/bin/python3
"""Orchestrator-only synthetic Mac proof. Prints fixed messages and counts only."""
import argparse
import http.client
import fcntl
import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit
import wave

import review

ROOT = review.PRIVATE / 'w2-synthetic-proof'
MARKER = 'mac-dictation W2 synthetic proof v1'


def prepare():
    ROOT.mkdir(mode=0o700)  # Refuse to overwrite an earlier proof or any existing data.
    (ROOT / 'synthetic-marker').write_text(MARKER)
    (ROOT / '.review-no-copy').touch(mode=0o600)
    audio_dir = ROOT / 'upload/recordings'
    audio_dir.mkdir(parents=True, mode=0o700)
    items = []
    pairs = [('The flarnet is ready.', 'The clarinet is ready.'),
             ('A flarnet sounds clear.', 'A clarinet sounds clear.'),
             ('The door is open.', 'The floor is open.')]
    for i, (wrong, right) in enumerate(pairs):
        name = f'synthetic-{i}.wav'
        with wave.open(str(audio_dir / name), 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 32000)
        items.append(dict(id=f'synthetic-{i}', text=wrong, cleaned_text=None,
                          recheck=right, recording='recordings/' + name, score=1 - i / 10))
    review.private_json(ROOT / 'review.json', {'version': 1, 'run_id': 'synthetic-w2', 'items': items})
    print('Prepared 3 synthetic items and 3 silence WAVs.')


def check(upstream):
    session = ROOT / 'session.json'
    for _ in range(100):
        if session.exists():
            break
        time.sleep(.1)
    info = json.loads(session.read_text())
    assert info['copy_disabled'] is True, 'Synthetic sessions must disable copying'
    endpoint = urlsplit(info['url'])
    listeners = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(info['pid']),
                                '-iTCP', '-sTCP:LISTEN', '-Fn'], check=True,
                               capture_output=True, text=True).stdout
    names = [line[1:] for line in listeners.splitlines() if line.startswith('n')]
    assert names == [f'127.0.0.1:{endpoint.port}']
    print('PASS: one listener, 127.0.0.1 only, random port.')
    connection = http.client.HTTPConnection('127.0.0.1', endpoint.port, timeout=30)
    connection.request('GET', endpoint.path + 'items')
    response = connection.getresponse()
    assert response.status == 200
    items = json.loads(response.read())['items']
    assert len(items) == 3
    # Verify the same endpoint the media element uses, including browser probes.
    for index in range(3):
        route = endpoint.path + f'audio/{index}'
        connection.request('GET', route)
        response = connection.getresponse()
        data = response.read()
        assert response.status == 200 and response.getheader('Content-Type') == 'audio/wav'
        assert response.getheader('Accept-Ranges') == 'bytes'
        with wave.open(io.BytesIO(data), 'rb') as audio:
            assert audio.getnframes() / audio.getframerate() == 4
        for requested, first, last in [('bytes=0-1', 0, 1), ('bytes=44-', 44, len(data)-1),
                                       ('bytes=-16', len(data)-16, len(data)-1)]:
            connection.request('GET', route, headers={'Range': requested})
            response = connection.getresponse()
            assert response.status == 206 and response.getheader('Content-Type') == 'audio/wav'
            assert response.getheader('Content-Range') == f'bytes {first}-{last}/{len(data)}'
            assert response.read() == data[first:last+1]
    print('PASS: 3 playable four-second WAVs, audio/wav, HTTP 200 and byte ranges 206.')
    answers = [dict(id=item['id'], choice=choice, text=item['recheck'])
               for item, choice in zip(items, ('recheck', 'typed', 'inserted'))]
    connection.request('POST', endpoint.path + 'save', json.dumps(answers),
                       {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{endpoint.port}'})
    response = connection.getresponse()
    assert response.status == 200
    result = json.loads(response.read())
    assert result['copy_disabled'] is True and result['copied'] is False
    connection.close()
    archive = ROOT / result['import_file']
    parsed = json.loads(archive.read_text())
    assert len(parsed['vocabulary']) == 1 and len(parsed['replacements']) == 1
    corrections = list((ROOT / 'correction-copies').glob('*/corrections/*.json'))
    assert len(corrections) == 1
    assert len(json.loads(corrections[0].read_text())['items']) == 3
    for path in [archive, *corrections]:
        assert path.stat().st_mode & 0o777 == 0o600
    # Compile the actual, unmodified upstream archive decoder, not a Python imitation.
    actual = upstream / 'VoiceInk/Features/Dictionary/Workflows/DictionaryArchive.swift'
    revision = subprocess.run(['git', '-C', str(upstream), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    assert revision.startswith('c09cc1f')
    subprocess.run(['git', '-C', str(upstream), 'diff', '--exit-code', 'HEAD', '--', str(actual)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    main = ROOT / 'main.swift'
    main.write_text('''import Foundation
// Supply only the namespace; the decoder and Codable types are upstream's.
enum DictionaryImportExportService {}
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let payload = try DictionaryImportExportService.decodeArchiveData(data)
precondition(payload.archive.vocabulary.count == 1)
precondition(payload.archive.replacements.count == 1)
print("PASS: upstream VoiceInk decoder accepted 1 vocabulary term and 1 replacement.")
''')
    subprocess.run(['/usr/bin/xcrun', 'swiftc', '-module-cache-path', str(ROOT / 'swift-cache'),
                    str(actual), str(main), '-o', str(ROOT / 'validate')],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([str(ROOT / 'validate'), str(archive)], check=True)
    for _ in range(100):
        if not session.exists():
            break
        time.sleep(.1)
    assert not session.exists()
    try:
        probe = http.client.HTTPConnection('127.0.0.1', endpoint.port, timeout=1)
        probe.request('GET', endpoint.path)
    except OSError:
        pass
    else:
        raise AssertionError('Listener still open')
    finally:
        probe.close()
    print('PASS: 3 corrections saved privately; Done closed the listener.')
    print('PASS: synthetic node copying disabled, copied: false.')


def notification_only():
    # The posting launch must consume its marker, then exit without taking review's lock.
    request = ROOT / 'review-notification-request'
    for _ in range(100):
        if not request.exists():
            break
        time.sleep(.1)
    assert not request.exists(), 'Notification app did not consume posting request'
    with (ROOT / 'review.lock').open('a') as lock:
        for _ in range(20):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
            time.sleep(.1)
    print('PASS: notification posted without starting the synthetic review. Now click it.')


def cleanup():
    assert ROOT.name == 'w2-synthetic-proof' and not ROOT.is_symlink()
    assert (ROOT / 'synthetic-marker').read_text() == MARKER
    assert not (ROOT / 'session.json').exists(), 'Close the synthetic review first'
    with (ROOT / 'review.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        shutil.rmtree(ROOT)  # Only this marked, generated synthetic folder.
    print('Removed synthetic proof files.')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check', 'cleanup', 'notify', 'notification-only'])
    parser.add_argument('--upstream', type=Path,
                        default=Path.home() / 'Code/mac-dictation/.upstream/VoiceInk')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        assert (ROOT / 'synthetic-marker').read_text() == MARKER
        if args.action == 'check':
            check(args.upstream)
        elif args.action == 'notification-only':
            notification_only()
        elif args.action == 'notify':
            # Isolate the generated notification app, so it never replaces the real hook.
            review.notify(ROOT / 'review.json', 3, ROOT, no_copy=True)
            print('Synthetic notification requested. Click it to test the review callback.')
        else:
            cleanup()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Synthetic proof failed; files retained. No content logged.')
        raise SystemExit(1) from None
