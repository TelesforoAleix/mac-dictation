#!/opt/homebrew/bin/python3
"""Private, ephemeral weekly review. Standard library only; never logs content."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid

import recheck

PRIVATE = recheck.PRIVATE
WORDS = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def tokens(text):
    return WORDS.findall(text)


def proposals(items, records):
    """Conservative changed spans; rules need two distinct supporting items."""
    terms = {}
    evidence = defaultdict(lambda: defaultdict(set))
    protected = set()
    for item, record in zip(items, records):
        if record['choice'] == 'skip':
            continue
        wrong, right = tokens(item['text']), tokens(record['text'])
        if record['choice'] == 'inserted':
            protected.update(word.casefold() for word in wrong)
            continue
        matcher = SequenceMatcher(None, [s.casefold() for s in wrong],
                                  [s.casefold() for s in right], autojunk=False)
        for tag, a, b, c, d in matcher.get_opcodes():
            if tag == 'equal':
                protected.update(word.casefold() for word in wrong[a:b])
                continue
            for word in right[c:d]:
                terms.setdefault(word.casefold(), word)
            if tag == 'replace' and b - a <= 4 and d - c <= 4:
                source, target = ' '.join(wrong[a:b]), ' '.join(right[c:d])
                evidence[source.casefold()][target].add(item['id'])
    rules = []
    sources = set(evidence)
    for source, targets in sorted(evidence.items()):
        if len(targets) != 1 or source in protected:
            continue
        target, support = next(iter(targets.items()))
        # Avoid chains/cycles and rules contradicted by a confirmed unchanged phrase.
        if len(support) >= 2 and target.casefold() not in sources:
            contradicted = any(
                rec['choice'] != 'skip' and
                (' ' + source + ' ') in (' ' + ' '.join(tokens(rec['text'])).casefold() + ' ')
                for rec in records)
            if not contradicted:
                rules.append({'sources': [source], 'replacement': target})
    return {'format': 'voiceink.dictionary', 'schemaVersion': 1,
            'exportedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'vocabulary': [{'term': terms[k]} for k in sorted(terms)], 'replacements': rules}


def private_json(path, value):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    # Unique temporary names and explicit modes even if caller's umask is permissive.
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.review-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_review(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('version') != 1 or not isinstance(value.get('run_id'), str):
        raise ValueError('Invalid review')
    items = value['items']
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError('Invalid items')
    ids = set()
    for item in items:
        if not all(isinstance(item.get(k), str) for k in ('id', 'text', 'recheck', 'recording')):
            raise ValueError('Invalid item')
        if item['id'] in ids:
            raise ValueError('Duplicate item')
        ids.add(item['id'])
        audio_path(path, item)
    return value


def audio_path(review, item):
    relative = Path(item['recording'])
    if (relative.is_absolute() or len(relative.parts) != 2
            or relative.parts[0] != 'recordings' or relative.suffix != '.wav'):
        raise ValueError('Invalid recording')
    audio = review.parent / 'upload' / relative
    # Use only W1's staged copies. Never resolve an original VoiceInk recording.
    base = (review.parent / 'upload/recordings').resolve()
    if audio.is_symlink() or audio.resolve().parent != base or not audio.is_file():
        raise ValueError('Missing recording')
    return audio


def corrections(items, answers):
    if not isinstance(answers, list) or len(answers) != len(items):
        raise ValueError('Answer every item or skip')
    result = []
    for item, answer in zip(items, answers):
        if not isinstance(answer, dict) or answer.get('id') != item['id']:
            raise ValueError('Invalid item')
        choice = answer.get('choice')
        if choice == 'inserted':
            text = item.get('cleaned_text') or item['text']
        elif choice == 'recheck':
            text = item['recheck']
        elif choice == 'typed':
            text = answer.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 100000:
                raise ValueError('Invalid correction')
        elif choice == 'skip':
            text = None
        else:
            raise ValueError('Invalid choice')
        result.append({'id': item['id'], 'choice': choice, 'text': text})
    return result


def copy_corrections(batch, root):
    key = root / 'dictation-node-key'
    if key.stat().st_mode & 0o077:
        raise ValueError('Key is not private')
    recheck.send(batch, key)


def save(review, value, answers, root, sender=copy_corrections):
    records = corrections(value['items'], answers)
    # Immutable submission names support additive rsync and explicit retries.
    identifier = uuid.uuid4().hex
    batch = review.parent / 'correction-copies' / identifier
    output = batch / 'corrections' / (identifier + '.json')
    private_json(output, {'version': 1, 'run_id': value['run_id'], 'items': records})
    archive = review.parent / 'Imports' / ('VoiceInk-' + identifier + '.json')
    private_json(archive, proposals(value['items'], records))
    copied = False
    try:
        sender(batch, root)
        copied = True
    except Exception:
        pass  # Private immutable submission remains available for retry.
    private_json(review.parent / 'review-result.json',
                 {'version': 1, 'submission': identifier, 'copied': copied,
                  'import_file': str(archive.relative_to(review.parent))})
    return archive, copied


class ReviewServer(HTTPServer):
    def __init__(self, review, root=PRIVATE, idle=300, open_files=True, sender=copy_corrections):
        self.review = Path(review)
        self.value = load_review(self.review)
        self.root, self.idle, self.open_files, self.sender = root, idle, open_files, sender
        self.token = secrets.token_urlsafe(32)
        self.finished = False
        self.last_activity = time.monotonic()
        super().__init__(('127.0.0.1', 0), Handler)
        self.timeout = min(.25, idle)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_port}/{self.token}/'

    def handle_error(self, request, client_address):
        pass  # Never emit tracebacks, request paths or private data.

    def run(self):
        try:
            while not self.finished and time.monotonic() - self.last_activity < self.idle:
                self.handle_request()
        finally:
            self.server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(min(2, self.server.idle))

    def reply(self, status, data=b'', kind='application/json', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        expected = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != expected:
            return None
        prefix = '/' + self.server.token + '/'
        path = urlsplit(self.path).path
        if not path.startswith(prefix):
            return None
        return path[len(prefix):]

    def do_GET(self):
        route = self.route()
        if route is None:
            return self.reply(403)
        self.server.last_activity = time.monotonic()
        if route in ('', 'review.js', 'review.css'):
            name = {'': 'review.html'}.get(route, route)
            kinds = {'': 'text/html; charset=utf-8', 'review.js': 'text/javascript; charset=utf-8',
                     'review.css': 'text/css; charset=utf-8'}
            return self.reply(200, Path(__file__).with_name(name).read_bytes(), kinds[route])
        if route == 'items':
            return self.reply(200, json.dumps(self.server.value).encode())
        if route.startswith('audio/'):
            try:
                index = int(route[6:])
                if index < 0:
                    raise ValueError()
                audio = audio_path(self.server.review, self.server.value['items'][index])
                data = audio.read_bytes()
                headers = {'Accept-Ranges': 'bytes'}
                requested = self.headers.get('Range')
                if requested:
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
                    if not match:
                        return self.reply(416)
                    first = int(match[1])
                    last = min(int(match[2]) if match[2] else len(data) - 1, len(data) - 1)
                    if first > last:
                        return self.reply(416)
                    headers['Content-Range'] = f'bytes {first}-{last}/{len(data)}'
                    return self.reply(206, data[first:last + 1], 'audio/wav', headers)
                return self.reply(200, data, 'audio/wav', headers)
            except (ValueError, IndexError, OSError):
                return self.reply(404)
        return self.reply(404)

    def do_POST(self):
        route = self.route()
        if (route not in ('save', 'activity', 'close') or self.headers.get('Origin') != self.server.url.rsplit('/', 2)[0]
                or self.headers.get('Content-Type') != 'application/json'):
            return self.reply(403)
        self.server.last_activity = time.monotonic()
        if route in ('activity', 'close'):
            self.server.finished = route == 'close'
            return self.reply(200, b'{}')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2200000:
                raise ValueError()
            answers = json.loads(self.rfile.read(length))
            archive, copied = save(self.server.review, self.server.value, answers,
                                   self.server.root, self.server.sender)
        except (ValueError, KeyError, TypeError, OSError):
            return self.reply(400, b'{"error":"Could not save; check choices and private storage."}')
        self.server.finished = True
        if self.server.open_files:
            try:
                subprocess.Popen(['/usr/bin/open', '-R', str(archive)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass  # Saved import still has a private, known location.
        self.reply(200, json.dumps({'saved': True, 'copied': copied,
                                   'instruction': 'VoiceInk → Dictionary → Import. Choose Merge.',
                                   'import_file': str(archive.relative_to(self.server.review.parent))}).encode())


def applescript_string(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def notify(review, count, root=PRIVATE):
    """Compile a script app: notification clicks relaunch its no-argument handler."""
    if not count or (root / 'review-notifications-disabled').exists():
        return
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    app = root / 'Weekly Dictation Review.app'
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--review', str(review)])
    command += ' >/dev/null 2>&1 &'
    script = ('on run argv\n'
              'if (count of argv) > 0 then\n'
              f'display notification {applescript_string(str(count) + " dictations to review")} '
              'with title "Weekly dictation review"\n'
              'else\n'
              f'do shell script {applescript_string(command)}\n'
              'end if\nend run\n')
    # No transcript or identifier is embedded in the app, just the private review path.
    source = root / 'review-notification.applescript'
    with source.open('w', encoding='utf-8') as stream:
        os.chmod(source, 0o600)
        stream.write(script)
    recheck.run_quiet(['/usr/bin/osacompile', '-o', str(app), str(source)])
    for member in [app, *app.rglob('*')]:
        mode = member.stat().st_mode
        member.chmod(0o700 if member.is_dir() or mode & 0o111 else 0o600)
    recheck.run_quiet(['/usr/bin/open', '-a', str(app), '--args', '--notify'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--idle', type=float, default=300)
    parser.add_argument('--no-open', action='store_true', help='No browser or Finder (scripted proof)')
    parser.add_argument('--session', type=Path, help='Write private endpoint metadata for scripted proof')
    parser.add_argument('--retry-copies', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    if not 0 < args.idle <= 3600:
        raise ValueError('Invalid timeout')
    review = args.review
    if review is None:
        state = json.loads((PRIVATE / 'recheck-state.json').read_text())
        review = PRIVATE / 'rechecks' / state['latest_run'] / 'review.json'
    review = review.resolve()
    # Serialize sessions per batch, including Done/save and retry.
    with (review.parent / 'review.lock').open('a') as lock:
        os.chmod(review.parent / 'review.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.retry_copies:
            for batch in sorted((review.parent / 'correction-copies').glob('*')):
                copy_corrections(batch, PRIVATE)
            result_path = review.parent / 'review-result.json'
            if result_path.exists():
                result = json.loads(result_path.read_text())
                result['copied'] = True
                private_json(result_path, result)
            print('Correction copies complete.')
            return
        server = ReviewServer(review, idle=args.idle, open_files=not args.no_open)
        if args.session:
            private_json(args.session, {'url': server.url, 'pid': os.getpid()})
        try:
            if not args.no_open:
                recheck.run_quiet(['/usr/bin/open', server.url])
            server.run()
        finally:
            server.server_close()
            if args.session:
                args.session.unlink(missing_ok=True)
        print('Review closed.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Review failed; private files retained. No content logged.')
        raise SystemExit(1) from None
