"""Text-only scoring primitives for the private model comparison."""

import unicodedata
from collections import Counter


def normalize(text):
    """Fold case, remove punctuation, and collapse whitespace."""
    text = unicodedata.normalize('NFC', text).lower()
    return ' '.join(''.join('' if unicodedata.category(c).startswith('P') else c
                            for c in text).split())


def edit_distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for index, ref in enumerate(reference, 1):
        current = [index]
        for position, hyp in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[position] + 1,
                               previous[position - 1] + (ref != hyp)))
        previous = current
    return previous[-1]


def error_counts(reference, hypothesis):
    ref, hyp = normalize(reference), normalize(hypothesis)
    return {
        'word_errors': edit_distance(ref.split(), hyp.split()),
        'reference_words': len(ref.split()),
        'character_errors': edit_distance(ref, hyp),
        'reference_characters': len(ref),
    }


def rate(errors, length):
    return errors / length if length else None


def aggregate(counts):
    """Micro-average errors across clips, retaining denominators."""
    keys = ('word_errors', 'reference_words', 'character_errors', 'reference_characters')
    totals = {key: sum(item[key] for item in counts) for key in keys}
    totals['wer'] = rate(totals['word_errors'], totals['reference_words'])
    totals['cer'] = rate(totals['character_errors'], totals['reference_characters'])
    return totals


def new_word_ratio(source, output):
    """Fraction of output word occurrences not accounted for by the input."""
    before = Counter(normalize(source).split())
    after = Counter(normalize(output).split())
    count = sum(after.values())
    return sum((after - before).values()) / count if count else 0.0


def has_loop(text):
    """Flag three consecutive copies of a sequence of up to 64 words."""
    words = normalize(text).split()
    for size in range(1, min(64, len(words) // 3) + 1):
        for start in range(len(words) - 3 * size + 1):
            phrase = words[start:start + size]
            if phrase == words[start + size:start + 2 * size] == words[start + 2 * size:start + 3 * size]:
                return True
    return False
