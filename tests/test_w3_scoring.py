import unittest

from research.scoring import aggregate, edit_distance, error_counts, has_loop, new_word_ratio, normalize, rate
from research.compare_models import cleanup_checks


class ScoringTests(unittest.TestCase):
    def test_normalize_unicode_punctuation_and_spacing(self):
        self.assertEqual(normalize('  A\u0300via,\tSÍ!  '), 'àvia sí')
        self.assertEqual(normalize('one—two'), 'onetwo')

    def test_edit_distance(self):
        self.assertEqual(edit_distance(['a', 'b'], ['a', 'c', 'd']), 2)
        self.assertEqual(edit_distance([], ['a']), 1)

    def test_counts_and_empty_reference(self):
        counts = error_counts('Hello, world!', 'hello there')
        self.assertEqual(counts['word_errors'], 1)
        self.assertEqual(counts['reference_words'], 2)
        self.assertEqual(rate(counts['word_errors'], counts['reference_words']), 0.5)
        self.assertIsNone(rate(1, 0))

    def test_new_words_count_occurrences(self):
        self.assertEqual(new_word_ratio('red blue', 'red red blue'), 1 / 3)
        self.assertEqual(new_word_ratio('red', ''), 0)

    def test_aggregate_weights_by_reference_length(self):
        result = aggregate([error_counts('one', 'two'), error_counts('one two three', 'one two three')])
        self.assertEqual(result['wer'], 0.25)
        self.assertEqual(result['reference_words'], 4)
        self.assertIsNone(aggregate([])['wer'])

    def test_repetition(self):
        self.assertTrue(has_loop('one two one two one two'))
        self.assertFalse(has_loop('one two one two three'))

    def test_cleanup_checks_use_only_counts_and_flag_language_change(self):
        analyses = [
            {'language': 'en', 'confidence': 0.9, 'names': ['Alex Smith']},
            {'language': 'en', 'confidence': 0.9, 'names': ['Alex Smith']},
            {'language': 'es', 'confidence': 0.9, 'names': []},
        ]
        result = cleanup_checks('', '', 'Synthetic output', analyses)
        self.assertFalse(result['same_language'])
        self.assertEqual(result['detected_names'], 1)
        self.assertEqual(result['missing_detected_names'], 1)
        self.assertNotIn('Alex Smith', str(result))

    def test_cleanup_low_confidence_is_unknown(self):
        analyses = [{'language': 'en', 'confidence': 0.1, 'names': []}] * 3
        self.assertIsNone(cleanup_checks('', '', '', analyses)['same_language'])


if __name__ == '__main__':
    unittest.main()
