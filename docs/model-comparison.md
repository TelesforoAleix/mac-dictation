# Local model comparison

The five verified clips contain 169 reference words and 915 normalized characters. Language detection classified all five as English. These results do not establish accuracy for Catalan, Spanish, mixed-language speech, or longer dictations. All 180 speech results and 15 cleanup results completed successfully.

## Decision

The speech-to-text comparison is inconclusive; no reference model is selected by the numbers. The maintainer keeps Whisper large-v3 as the weekly reference for now. Weekly analysis proposing cleanup and vocabulary changes will use a local model of about 27B parameters. A proposed change applies only if a replay test shows it is better. All five verified clips were detected as English, so Catalan, Spanish, and mixed-language speech remain untested.

No reference model beat Whisper large-v3-turbo on the five clips. **Reference selection requires escalation:** the best tested baseline has 13.61% WER, versus 17.75%, 21.30%, and 51.48% for the alternatives. Qwen3-8B has the lowest cleanup WER in this small sample, 13.02% versus the 4B baseline's 16.57%; this does not establish that it preserves all languages, names, or meaning.

## Speech recognition

Each cell is WER / CER in percent; total errors are micro-averaged. The same private vocabulary prompt was supplied to every model. Times include model startup per clip; RTF is elapsed time divided by audio duration. Peak memory is process peak resident memory, not a complete measurement of GPU or system-wide memory.

| Model | Clip 1 | Clip 2 | Clip 3 | Clip 4 | Clip 5 | Total | Median clip RTF | Peak RSS GiB |
| --- | --- | --- | --- | --- | --- | --- | ---: | ---: |
| Whisper large-v3-turbo | 13.5 / 13.0 | 19.0 / 9.5 | 12.5 / 11.6 | 20.8 / 9.5 | 0.0 / 0.0 | 13.61 / 8.85 | 0.215 | 1.89 |
| Whisper large-v3 | 10.8 / 8.7 | 33.3 / 12.4 | 41.7 / 31.2 | 17.0 / 9.5 | 0.0 / 0.0 | 17.75 / 11.15 | 0.349 | 3.95 |
| Aina Whisper large-v3 | 16.2 / 14.5 | 23.8 / 12.4 | 45.8 / 32.6 | 26.4 / 11.2 | 0.0 / 0.0 | 21.30 / 13.22 | 0.377 | 3.96 |
| Catalan-tuned Whisper turbo | 48.6 / 39.6 | 19.0 / 10.5 | 33.3 / 21.0 | 81.1 / 66.8 | 41.2 / 25.3 | 51.48 / 39.56 | 0.244 | 1.90 |

| Model | Clip 1 RTF / GiB | Clip 2 RTF / GiB | Clip 3 RTF / GiB | Clip 4 RTF / GiB | Clip 5 RTF / GiB |
| --- | --- | --- | --- | --- | --- |
| Whisper large-v3-turbo | 0.156 / 1.88 | 0.235 / 1.88 | 0.215 / 1.88 | 0.175 / 1.89 | 0.293 / 1.88 |
| Whisper large-v3 | 0.290 / 3.94 | 0.348 / 3.94 | 0.349 / 3.93 | 0.421 / 3.93 | 1.416 / 3.93 |
| Aina Whisper large-v3 | 0.594 / 3.94 | 0.355 / 3.94 | 0.377 / 3.94 | 0.305 / 3.94 | 0.954 / 3.85 |
| Catalan-tuned Whisper turbo | 0.148 / 1.89 | 0.244 / 1.88 | 0.242 / 1.89 | 0.409 / 1.89 | 0.784 / 1.89 |

The 40 unlabelled recordings do not establish correctness. Loop counts use a repeated-word-sequence heuristic; empty counts include very short or silent recordings. Detected-language counts are model outputs, not verified languages.

| Model | Loops / 40 | Empty / 40 | Detected language counts |
| --- | ---: | ---: | --- |
| Whisper large-v3-turbo | 1 | 3 | en 37, ja 1, ca 1, es 1 |
| Whisper large-v3 | 2 | 4 | en 37, ja 1, ca 1, es 1 |
| Aina Whisper large-v3 | 0 | 4 | en 38, ja 1, es 1 |
| Catalan-tuned Whisper turbo | 5 | 3 | ca 39, en 1 |

Pairwise micro-averaged WER is directional: the first value uses the first model as the denominator, the second uses the second model. An empty reference contributes no reference words; WER can exceed 100%.

| First model | Second model | First → second % | Second → first % |
| --- | --- | ---: | ---: |
| Whisper large-v3-turbo | Whisper large-v3 | 14.4 | 13.8 |
| Whisper large-v3-turbo | Aina Whisper large-v3 | 9.0 | 8.8 |
| Whisper large-v3-turbo | Catalan-tuned Whisper turbo | 54.5 | 95.3 |
| Whisper large-v3 | Aina Whisper large-v3 | 15.0 | 15.3 |
| Whisper large-v3 | Catalan-tuned Whisper turbo | 57.6 | 105.0 |
| Aina Whisper large-v3 | Catalan-tuned Whisper turbo | 53.5 | 95.5 |

## Cleanup

Each model received the same turbo output and the current generic cleanup prompt. WER / CER compare cleaned output with the handwritten references, with the same normalization as speech. New-word ratio counts output word occurrences not accounted for in the input; edits can increase it without adding meaning. Language and name checks are heuristics, not guarantees. The name check considers names detected in either the input or reference, so a missing detected reference name may already be absent from the input.

| Model | Clip 1 WER / CER % | Clip 2 | Clip 3 | Clip 4 | Clip 5 | Total WER / CER % |
| --- | --- | --- | --- | --- | --- | --- |
| Qwen3-4B | 13.5 / 13.0 | 19.0 / 9.5 | 20.8 / 16.7 | 26.4 / 13.2 | 0.0 / 0.0 | 16.57 / 10.82 |
| Qwen3-8B | 13.5 / 13.0 | 19.0 / 9.5 | 12.5 / 11.6 | 18.9 / 8.8 | 0.0 / 0.0 | 13.02 / 8.63 |
| Qwen3.6-27B | 13.5 / 13.0 | 23.8 / 13.3 | 20.8 / 16.7 | 22.6 / 11.2 | 0.0 / 0.0 | 15.98 / 10.60 |

| Model | Latency seconds, clips 1–5 | New-word ratio %, clips 1–5 | Input → output words, clips 1–5 | Same detected language | Missing detected names |
| --- | --- | --- | --- | ---: | ---: |
| Qwen3-4B | 1.33, 0.95, 1.02, 2.32, 1.74 | 0, 0, 0, 8.3, 0 | 41→41, 20→20, 24→24, 60→60, 34→34 | 5 / 5 | 1 / 4 |
| Qwen3-8B | 7.40, 2.14, 2.43, 8.17, 3.32 | 0, 0, 0, 1.7, 0 | 41→41, 20→20, 24→24, 60→60, 34→34 | 5 / 5 | 1 / 4 |
| Qwen3.6-27B | 18.92, 14.13, 13.80, 25.73, 13.16 | 0, 4.8, 0, 5.1, 0 | 41→41, 20→21, 24→24, 60→59, 34→34 | 5 / 5 | 1 / 4 |

## Candidate coverage

The [Catalan-tuned Whisper turbo model card](https://huggingface.co/duhow/whisper-large-v3-turbo-cat) lists Catalan, Spanish and English and reports Catalan evaluation results. Its checkpoint was converted locally for whisper.cpp; the poor English results here do not establish whether the conversion affected accuracy. [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) and [Canary 1B v2](https://huggingface.co/nvidia/canary-1b-v2) were excluded because their published language lists omit Catalan. [SeamlessM4T v2](https://huggingface.co/facebook/seamless-m4t-v2-large) supports the three languages but requires a target language, limiting its suitability for mixed-language transcription. Parakeet was excluded because its available model does not support Catalan. No additional cleanup model was downloaded.
