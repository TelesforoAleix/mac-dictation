import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from research import compare_models as compare


class SpeechResumeTest(unittest.TestCase):
    def test_saves_and_reuses_each_clip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / 'clip.wav'
            with wave.open(str(audio), 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(b'\x00\x00' * 16000)
            class Response:
                returncode = 0
                stderr = b'12345 maximum resident set size\n'
            def whisper(command, **kwargs):
                path = Path(command[command.index('-of') + 1] + '.json')
                path.write_text(json.dumps({'result': {'language': 'en'},
                                            'transcription': [{'text': 'Synthetic words'}]}))
                return Response()
            with patch.object(compare, 'ROOT', root), patch.object(compare.subprocess, 'run', side_effect=whisper) as run:
                first = compare.speech('tiny', root / 'model', audio, 'clip-01', 'synthetic', 'key')
                second = compare.speech('tiny', root / 'model', audio, 'clip-01', 'synthetic', 'key')
                self.assertEqual(first, second)
                self.assertEqual(first['peak_rss_bytes'], 12345)
                self.assertEqual(first['audio_seconds'], 1)
                self.assertEqual(run.call_count, 1)
                self.assertTrue((root / 'speech/tiny/clip-01.json').exists())

    def test_failed_result_is_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'result.json'
            compare.save(path, {'status': 'failed', 'key': 'key'})
            self.assertIsNone(compare.cached(path, 'key'))
            compare.save(path, {'status': 'ok', 'key': 'key'})
            self.assertIsNone(compare.cached(path, 'changed'))
            self.assertIsNotNone(compare.cached(path, 'key'))
