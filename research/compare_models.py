#!/usr/bin/env python3
"""Private, resumable local model comparison. No private text is printed."""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave

try:
    from .scoring import aggregate, error_counts, has_loop, new_word_ratio, normalize, rate
except ImportError:
    from scoring import aggregate, error_counts, has_loop, new_word_ratio, normalize, rate

HOME = Path.home()
BASE = HOME / 'Code/voice-assessment'
ROOT = BASE / 'results/w3'
VERIFIED = BASE / 'verified/2026-10-16'
PROMPT = BASE / 'inputs/whisper-prompt.txt'
RECORDINGS = BASE / 'inputs/w3-recordings'
WHISPER = '/opt/homebrew/bin/whisper-cli'
SERVER = '/opt/homebrew/bin/llama-server'
SPEECH = {
    'turbo': BASE / 'models/turbo/ggml-large-v3-turbo.bin',
    'large-v3': HOME / 'Library/Application Support/mac-dictation/models/ggml-large-v3.bin',
    'aina': BASE / 'models/assets/aina-ggml/ggml-model.bin',
    'catalan-turbo': BASE / 'models/w3/catalan-turbo-ggml/ggml-model.bin',
}
CLEANUP = {
    'qwen3-4b': HOME / 'Library/Application Support/mac-dictation/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    'qwen3-8b': HOME / 'homelab-models/coding-eval/Qwen3-8B-Q4_K_M.gguf',
    'qwen3.6-27b': HOME / 'homelab-models/coding-eval/Qwen3.6-27B-Q4_K_M.gguf',
}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def progress(message):
    target = os.environ.get('HOMELAB_QUEUE_PROGRESS')
    if target:
        Path(target).write_text(message + '\n', encoding='utf-8')
    print(message, flush=True)


def audio_seconds(path):
    with wave.open(str(path), 'rb') as audio:
        return audio.getnframes() / audio.getframerate()


def fingerprint(*parts):
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()


def result_path(kind, model, clip):
    return ROOT / kind / model / (clip + '.json')


def cached(path, key):
    if path.is_file():
        data = json.loads(path.read_text())
        if data.get('key') == key and data.get('status') == 'ok':
            return data
    return None


def speech(model, model_path, audio, clip, prompt, key, cpu=False):
    path = result_path('speech', model, clip)
    old = cached(path, key)
    if old:
        return old
    scratch = ROOT / 'scratch'
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = scratch / 'whisper-output'
    json_path = output.with_suffix('.json')
    if json_path.exists():
        json_path.unlink()
    command = [WHISPER, '-m', str(model_path), '-l', 'auto',
               '--prompt', prompt, '-oj', '-of', str(output), '-f', str(audio)]
    if cpu:
        command.append('--no-gpu')
    else:
        command = ['/usr/bin/time', '-l'] + command
    start = time.monotonic()
    try:
        process = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 timeout=7200, check=False)
        (scratch / 'whisper-stderr.log').write_bytes(process.stderr)
        elapsed = time.monotonic() - start
        if process.returncode or not json_path.exists():
            raise RuntimeError('speech process failed with status %d' % process.returncode)
        data = json.loads(json_path.read_text())
        text = ' '.join(segment['text'].strip() for segment in data['transcription']).strip()
        match = re.search(rb'(\d+)\s+maximum resident set size', process.stderr)
        if not cpu and not match:
            raise RuntimeError('peak memory unavailable')
        duration = audio_seconds(audio)
        result = {'status': 'ok', 'key': key, 'text': text,
                  'language': data.get('result', {}).get('language'),
                  'loop': has_loop(text), 'empty': not bool(normalize(text)),
                  'seconds': elapsed, 'audio_seconds': duration,
                  'rtf': elapsed / duration if duration else None,
                  'peak_rss_bytes': int(match.group(1)) if match else None}
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result = {'status': 'failed', 'key': key,
                  'reason': str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
    save(path, result)
    return result


def post(port, prompt, text):
    body = json.dumps({'model': 'w3', 'temperature': 0.3, 'messages': [
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': '<TRANSCRIPT>' + text + '</TRANSCRIPT>'}]}).encode()
    request = urllib.request.Request('http://127.0.0.1:%d/v1/chat/completions' % port,
                                     data=body, headers={'Content-Type': 'application/json'})
    with OPENER.open(request, timeout=600) as response:
        return json.load(response)['choices'][0]['message']['content'].strip()


def language_and_names(texts):
    """Run macOS language and named-entity heuristics without exposing text."""
    cache = ROOT / 'swift-cache'
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(cache))
    process = subprocess.run(['/usr/bin/swift', '-module-cache-path', str(cache),
                              str(Path(__file__).with_name('language.swift'))],
                             input=json.dumps({'texts': texts}).encode(),
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env=env, timeout=300, check=False)
    if process.returncode:
        raise RuntimeError('language and name analysis failed')
    return json.loads(process.stdout)


def cleanup_checks(source, reference, output, analyses):
    src, ref, out = analyses
    confident = lambda item: item['language'] if (item.get('confidence') or 0) >= 0.7 else None
    src_language, out_language = confident(src), confident(out)
    names = {normalize(name) for name in src['names'] + ref['names'] if normalize(name)}
    output_words = normalize(output).split()
    missing = sum(not any(output_words[i:i + len(name.split())] == name.split()
                          for i in range(len(output_words))) for name in names)
    return {'input_language': src_language, 'output_language': out_language,
            'same_language': src_language == out_language if src_language and out_language else None,
            'detected_names': len(names), 'missing_detected_names': missing}


def cleanup_model(model, model_path, references, turbo, prompt, model_hash, reference_hash, runtime_hash):
    pending = []
    for i, source in enumerate(turbo):
        key = fingerprint(model_hash, hashlib.sha256(prompt.encode()).hexdigest(), source['key'],
                          reference_hash, runtime_hash)
        path = result_path('cleanup', model, 'clip-%02d' % (i + 1))
        if not cached(path, key):
            pending.append((i, key, path))
    if not pending:
        return
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = ROOT / 'scratch' / ('server-' + model + '.log')
    command = [SERVER, '-m', str(model_path), '--alias', 'w3', '--host', '127.0.0.1',
               '--port', str(port), '-ngl', '99', '-c', '8192', '-t', '6', '--no-webui',
               '--temp', '0', '--seed', '0', '--samplers', 'top_k', '--top-k', '1',
               '-n', '2048', '--chat-template-kwargs', '{"enable_thinking":false}']
    with log.open('wb') as logfile:
        process = subprocess.Popen(command, stdout=logfile, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('server failed')
                try:
                    with OPENER.open('http://127.0.0.1:%d/health' % port, timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(1)
            else:
                raise RuntimeError('server startup timed out')
            failures = 0
            for i, key, path in pending:
                progress('cleanup %s %d/5' % (model, i + 1))
                start = time.monotonic()
                try:
                    output = post(port, prompt, turbo[i]['text'])
                    result = {'status': 'ok', 'key': key, 'text': output,
                              'seconds': time.monotonic() - start,
                              'scores': error_counts(references[i]['reference'], output),
                              'input_words': len(normalize(turbo[i]['text']).split()),
                              'output_words': len(normalize(output).split()),
                              'new_word_ratio': new_word_ratio(turbo[i]['text'], output)}
                except (OSError, ValueError, KeyError, urllib.error.URLError):
                    failures += 1
                    result = {'status': 'failed', 'key': key, 'reason': 'cleanup inference failed'}
                save(path, result)
                if failures >= 3:
                    raise RuntimeError('first cleanup calls failed')
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cpu-check', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    (ROOT / 'scratch').mkdir(mode=0o700, parents=True, exist_ok=True)
    refs = json.loads((VERIFIED / 'reference.json').read_text())['clips']
    if len(refs) != 5:
        raise RuntimeError('expected five reference clips')
    prompt = PROMPT.read_text()
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    clips = [('clip-%02d' % (i + 1), VERIFIED / ('clip-%02d.wav' % (i + 1))) for i in range(5)]
    recordings = sorted(RECORDINGS.glob('*.wav'), key=lambda p: p.stat().st_mtime, reverse=True)[:40]
    clips.extend(('recording-%02d' % (i + 1), path) for i, path in enumerate(recordings))
    if args.cpu_check:
        clips = clips[:1]
        models = {'tiny': BASE / 'models/w3/ggml-tiny.bin'}
    else:
        if len(recordings) != 40:
            raise RuntimeError('expected 40 copied recordings')
        models = SPEECH
    hashes = {name: digest(path) for name, path in models.items()}
    if not args.cpu_check and hashes['catalan-turbo'] != '4a82362ac08d603ce95c0ebe812752784ad1a3aefbf350db63c0b36aeeeb857c':
        raise RuntimeError('converted model hash mismatch')
    runner_hash = digest(Path(__file__))
    whisper_hash = digest(Path(WHISPER))
    audio_hashes = {clip: digest(path) for clip, path in clips}
    results = {}
    for model, model_path in models.items():
        results[model] = []
        failures = 0
        for i, (clip, audio) in enumerate(clips):
            progress('speech %s %d/%d' % (model, i + 1, len(clips)))
            key = fingerprint(hashes[model], audio_hashes[clip], prompt_hash, whisper_hash,
                              runner_hash, 'cpu' if args.cpu_check else 'gpu')
            result = speech(model, model_path, audio, clip, prompt, key, args.cpu_check)
            results[model].append(result)
            if result['status'] != 'ok':
                failures += 1
            if i == 2 and failures == 3:
                raise RuntimeError('first three speech items failed')
    if args.cpu_check:
        if results['tiny'][0]['status'] != 'ok':
            raise RuntimeError('CPU speech inference failed')
        score = error_counts(refs[0]['reference'], results['tiny'][0]['text'])
        save(ROOT / 'cpu-summary.json', {'score': aggregate([score]),
             'rtf': results['tiny'][0]['rtf'], 'peak_rss_bytes': results['tiny'][0]['peak_rss_bytes']})
        return
    turbo = results['turbo'][:5]
    if any(item['status'] != 'ok' for item in turbo):
        raise RuntimeError('turbo reference inputs failed')
    cleanup_prompt = (Path(__file__).resolve().parent.parent / 'cleanup-prompt.txt').read_text().strip()
    for model, path in CLEANUP.items():
        cleanup_model(model, path, refs, turbo, cleanup_prompt, digest(path),
                      digest(VERIFIED / 'reference.json'), fingerprint(digest(Path(SERVER)), runner_hash))
    summary = {'speech': {}, 'pairwise': {}, 'cleanup': {}}
    for model, rows in results.items():
        scores = [error_counts(refs[i]['reference'], row['text']) if row['status'] == 'ok' else None
                  for i, row in enumerate(rows[:5])]
        item = {'clips': [dict(score, wer=rate(score['word_errors'], score['reference_words']),
                               cer=rate(score['character_errors'], score['reference_characters'])) if score else None
                          for score in scores],
                'total': aggregate([score for score in scores if score]),
                'failed': sum(row['status'] != 'ok' for row in rows),
                'recordings_loop_count': sum(row.get('loop', False) for row in rows[5:]),
                'recordings_empty_count': sum(row.get('empty', False) for row in rows[5:]),
                'recordings': [{'language': row.get('language'), 'loop': row.get('loop'),
                                'empty': row.get('empty'), 'rtf': row.get('rtf'),
                                'peak_rss_bytes': row.get('peak_rss_bytes')} for row in rows[5:]],
                'clip_rtf': [row.get('rtf') for row in rows[:5]],
                'clip_peak_rss_bytes': [row.get('peak_rss_bytes') for row in rows[:5]]}
        summary['speech'][model] = item
    for first, second in itertools.combinations(models, 2):
        scores = [error_counts(a['text'], b['text']) if a['status'] == b['status'] == 'ok' else None
                  for a, b in zip(results[first][5:], results[second][5:])]
        reverse = [error_counts(b['text'], a['text']) if a['status'] == b['status'] == 'ok' else None
                   for a, b in zip(results[first][5:], results[second][5:])]
        summary['pairwise'][first + '__' + second] = {
            'forward_clips': [rate(s['word_errors'], s['reference_words']) if s else None for s in scores],
            'reverse_clips': [rate(s['word_errors'], s['reference_words']) if s else None for s in reverse],
            'forward_total': aggregate([s for s in scores if s])['wer'],
            'reverse_total': aggregate([s for s in reverse if s])['wer']}
    for model in CLEANUP:
        rows = [json.loads(result_path('cleanup', model, 'clip-%02d' % (i + 1)).read_text()) for i in range(5)]
        texts = [text for i, row in enumerate(rows) if row['status'] == 'ok'
                 for text in (turbo[i]['text'], refs[i]['reference'], row['text'])]
        analyses = iter(language_and_names(texts)) if texts else iter(())
        checked = []
        for i, row in enumerate(rows):
            numeric = {k: v for k, v in row.items() if k not in ('text', 'key')}
            if row['status'] == 'ok':
                numeric['wer'] = rate(row['scores']['word_errors'], row['scores']['reference_words'])
                numeric['cer'] = rate(row['scores']['character_errors'], row['scores']['reference_characters'])
                numeric.update(cleanup_checks(turbo[i]['text'], refs[i]['reference'], row['text'],
                                              [next(analyses) for _ in range(3)]))
            checked.append(numeric)
        summary['cleanup'][model] = {'clips': checked,
                                      'total': aggregate([row['scores'] for row in rows if row['status'] == 'ok'])}
    save(ROOT / 'summary.json', summary)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Comparison stopped: ' + (str(exc) if type(exc) is RuntimeError else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
