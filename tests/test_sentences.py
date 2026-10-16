"""Generated sentence fixtures and synthetic PCM only; no owner data."""
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import recheck
import review


def segments(sentences):
    return [dict(text=text, offsets={'from': i * 3000, 'to': i * 3000 + 2000})
            for i, text in enumerate(sentences)]


class SentenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audio = self.root / 'synthetic.wav'
        with wave.open(str(self.audio), 'wb') as wav:
            wav.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0\0' * 300000)

    def assess(self, inserted, sentences, cleaned=None):
        row = dict(id='synthetic', text=inserted, cleaned_text=cleaned)
        def whisper(args, **kwargs):
            self.assertIn('-oj', args)
            self.assertNotIn('-otxt', args)
            output = Path(args[args.index('-of') + 1] + '.json')
            output.write_text(json.dumps({'transcription': segments(sentences)}))
            return subprocess.CompletedProcess(args, 0, stderr=b'')
        with patch.object(recheck.subprocess, 'run', side_effect=whisper):
            return recheck.assess_batch([(row, self.audio)], self.root, Path('model'), Path('cli'))

    def test_long_dictation_flags_only_one_sentence(self):
        sentences = [f'Synthetic object {i} is blue.' for i in range(80)]
        inserted = ' '.join(sentences)
        sentences[43] = sentences[43].replace('blue', 'green')
        items, counts = self.assess(inserted, sentences)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item['text'], 'Synthetic object 43 is blue.')
        self.assertEqual((item['start'], item['end']), (129, 131))
        self.assertEqual(inserted[item['text_start']:item['text_end']], item['text'])
        self.assertEqual(counts['assessed_sentences'], 80)

    def test_multiple_differences_rank_ten_sentences_across_dictations(self):
        sentences = [f'Synthetic object {i} is blue.' for i in range(16)]
        checked = [text.replace('blue', 'green') for text in sentences]
        items, counts = self.assess(' '.join(sentences), checked)
        self.assertEqual((len(items), counts['assessed_sentences']), (16, 16))
        self.assertEqual(len({item['id'] for item in items}), 16)
        larger = dict(items[0], id='other:0', dictation_id='other', score=1)
        ranked = recheck.rank([*items, larger])
        self.assertEqual(len(ranked), 10)
        self.assertEqual(ranked[0], larger)
        self.assertEqual([i['score'] for i in ranked], sorted([i['score'] for i in ranked], reverse=True))

    def test_normalization_and_cleaned_insertion_proxy(self):
        items, counts = self.assess('Synthetic raw.', ['HELLO café!'], cleaned='Hello, café.')
        self.assertEqual(items, [])
        self.assertEqual(counts['assessed_sentences'], 1)
        items, _ = self.assess('Synthetic raw.', ['The flute is blue.'], cleaned='The clarinet is blue.')
        self.assertEqual((items[0]['text'], items[0]['source_field']),
                         ('The clarinet is blue.', 'cleaned_text'))

    def test_missing_and_extra_sentences_do_not_shift_later_alignment(self):
        inserted = 'The door opens. Synthetic extra sentence. The flute sounds. The lamp glows.'
        checked = recheck.timed_sentences(segments(['The door opens.', 'The flute sounds.', 'The lamp shines.']))
        items = recheck.sentence_items(dict(id='synthetic', text=inserted), self.audio, checked)
        self.assertEqual([(i['text'], i['recheck']) for i in items],
                         [('Synthetic extra sentence.', ''), ('The lamp glows.', 'The lamp shines.')])
        inserted = 'The door opens. The lamp glows.'
        checked = recheck.timed_sentences(segments(['The door opens.', 'The flute sounds.', 'The lamp glows.']))
        items = recheck.sentence_items(dict(id='synthetic', text=inserted), self.audio, checked)
        self.assertEqual([(i['text'], i['recheck']) for i in items], [('', 'The flute sounds.')])
        self.assertEqual(items[0]['text_start'], items[0]['text_end'])

    def test_cut_across_segments_and_within_one_segment(self):
        checked = recheck.timed_sentences([
            dict(text='The synthetic', offsets={'from': 1000, 'to': 2000}),
            dict(text=' flute sounds. The lamp glows!', offsets={'from': 2000, 'to': 6000})])
        self.assertEqual([i['text'] for i in checked], ['The synthetic flute sounds.', 'The lamp glows!'])
        self.assertEqual(checked[0]['start'], 1)
        self.assertLess(checked[0]['end'], 6)
        self.assertGreater(checked[1]['start'], 2)
        self.assertEqual(checked[1]['end'], 6)
        self.assertEqual(len(recheck.sentence_spans('Dr. Synthetic has 3.5 tokens. A lamp glows.')), 2)
        # Punctuation-only resegmentation must never generate review work.
        items, _ = self.assess('The flute sounds and the lamp glows.', ['The flute sounds.', 'And the lamp glows.'])
        self.assertEqual(items, [])

    def test_correction_carries_exact_sentence_span_and_preserves_surrounding_text(self):
        original = 'The lamp glows.  The flute is blue.\nThe door opens.'
        items, _ = self.assess(original, ['The lamp glows.', 'The flute is green.', 'The door opens.'])
        record = review.corrections(items, [dict(id=items[0]['id'], choice='recheck')])[0]
        corrected = original[:record['text_start']] + record['text'] + original[record['text_end']:]
        self.assertEqual(corrected, 'The lamp glows.  The flute is green.\nThe door opens.')
        self.assertEqual(record['dictation_id'], 'synthetic')
        self.assertEqual(record['source_field'], 'text')
        path = self.root / 'review.json'
        archive, _ = review.save(path, dict(run_id='synthetic', items=items),
                                [dict(id=items[0]['id'], choice='typed', text='The flute is red.')],
                                self.root, no_copy=True)
        self.assertEqual(json.loads(archive.read_text())['vocabulary'], [{'term': 'red'}])
        payload = json.loads(next(self.root.glob('correction-copies/*/corrections/*.json')).read_text())
        self.assertEqual(payload['items'][0]['text_start'], record['text_start'])

    def test_invalid_timing_fails_without_falling_back_to_full_audio(self):
        for start, end in [(float('nan'), 2000), (2000, 1000), (0, 0), (-1, 1000)]:
            with self.subTest(start=start, end=end), self.assertRaises(recheck.SafeError):
                recheck.timed_sentences([dict(text='Synthetic.', offsets={'from': start, 'to': end})])

    def test_clip_contains_only_permitted_frames_with_valid_wav_header(self):
        folder = self.root / 'upload/recordings'
        folder.mkdir(parents=True)
        audio = folder / 'synthetic.wav'
        # Distinct samples outside and inside the sentence; compare bytes, not just duration.
        frames = b''.join(struct.pack('<h', i) for i in range(10000))
        with wave.open(str(audio), 'wb') as wav:
            wav.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
            wav.writeframes(frames)
        item = dict(dictation_id='synthetic', recording='recordings/synthetic.wav', start=3, end=4)
        path = self.root / 'review.json'
        data = review.audio_clip(path, item, 'start=2.65&end=4.35')
        with wave.open(io.BytesIO(data), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 1700)
            self.assertEqual(wav.readframes(1700), frames[2650 * 2:4350 * 2])
        self.assertEqual(review.audio_clip(path, item, ''), data)
        for query in ['start=0&end=10', 'start=nan&end=4', 'start=3&end=inf',
                      'start=4&end=3', 'start=3', 'start=3&end=4&end=5']:
            with self.subTest(query=query), self.assertRaises(ValueError):
                review.audio_clip(path, item, query)
        item.update(start=0, end=12)
        with wave.open(io.BytesIO(review.audio_clip(path, item, '')), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 10000)


if __name__ == '__main__':
    unittest.main()
