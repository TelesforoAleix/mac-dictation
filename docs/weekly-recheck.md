# Weekly re-check

The source contract is VoiceInk 2.21 at `c09cc1f`. The model in
`VoiceInk/Infrastructure/Persistence/Models/Transcription.swift` has `id`, `timestamp`,
`text`, `enhancedText`, `audioFileURL`, duration and status. The recording workflow
writes UUID-named WAVs into VoiceInk's `Recordings/` directory. Vocabulary is read
from a file copy of `dictionary.store`; preferences are not queried. The reader
validates the SQLite entity and column layout
on a temporary snapshot; mismatches stop processing and uploads with a fixed message.

## Repeatable node setup

Generate the job's sole key on the Mac, with no passphrase (unattended operation).
During this slice it was generated in the repository's ignored `.build-cache/w1/`
folder. Never stage that folder or display the key. The setup consumes only its
public half on stdin:

```bash
mkdir -p .build-cache/w1
ssh-keygen -q -t ed25519 -N '' -C dictation-weekly -f .build-cache/w1/dictation-node-key
python3 - <<'PY'
from pathlib import Path
import shlex
import subprocess
subprocess.run(['ssh', 'homelab-agent', 'sudo bash -c ' +
                shlex.quote(Path('setup-dictation-node.sh').read_text())],
               input=Path('.build-cache/w1/dictation-node-key.pub').read_bytes(), check=True)
PY
```

The script creates the system user `dictation-backup` with a locked password, its
own group, no sudo membership, and a root-owned home at `/var/lib/dictation-backup`.
`/bin/sh` is needed by sshd to execute the forced command; no unrestricted SSH shell
is authorized. The root-owned `.ssh/authorized_keys` is readable by the account but
cannot be changed by it. It contains exactly:

```text
restrict,command="/usr/bin/rrsync -wo /srv/homelab/voice-data/originals/dictation" <public-key>
```

The account owns only the new backup directory (0700). Traverse-only POSIX ACLs on
`/srv/homelab`, `voice-data` and `originals` let it reach that directory without
listing the ancestors or reading siblings. The script uses the node's existing
`libacl.so.1` through Python, preserving other ACL entries and their mask except
for the execute bit needed for traversal. It is repeatable with the same public key
and refuses to replace a different authorized key. It does not alter sshd policy.

SSH admission is managed by `homelab-v2`, separately from this setup script.
Its approved PR #36 admits this account through `AllowUsers` and applies a
`Match User dictation-backup` block disabling forwarding and TTY and forcing the
same `/usr/bin/rrsync -wo /srv/homelab/voice-data/originals/dictation` command.
That policy was deployed at node revision `f03df42`. A node rebuild needs both
that managed SSH policy and this account/key/directory setup. Do not change SSH
configuration from this repository.

The job resolves `homelab-agent`'s hostname and port with `ssh -G`, then uses
`ssh -F /dev/null`, strict host-key checking, `IdentitiesOnly=yes`, no SSH agent and
only its dedicated key. The node must already be trusted in the Mac's standard
known-hosts file. The alias must reach the node directly; it supplies no inherited
ProxyCommand or ProxyJump. The job never uses the administrative identity.

The node's existing weekly encrypted backup covers
`/srv/homelab/voice-data/originals/dictation/` automatically. Nothing is added to
backup configuration or `homelab-v2`.

## Orchestrator's Mac setup (outside the sandbox)

Run from the repository in a normal terminal. The CLI is installed durably through
Homebrew's `whisper.cpp` formula. Homebrew Python supplies the fixed executable for
the profile job slot; no pip packages are needed. Homebrew rsync is required because
macOS's system openrsync sends `--dirs`, which the node's rrsync rejects.

```bash
set -e
umask 077
brew install whisper.cpp python rsync
private_dir="$HOME/Library/Application Support/mac-dictation"
mkdir -p "$private_dir/models"
chmod 700 "$private_dir" "$private_dir/models"
test ! -e "$private_dir/models/ggml-large-v3.bin"
mv "$HOME/Code/voice-assessment/models/runtimes/whisper.cpp/models/ggml-large-v3.bin" \
   "$private_dir/models/ggml-large-v3.bin"
chmod 600 "$private_dir/models/ggml-large-v3.bin"
test ! -e "$private_dir/dictation-node-key"
mv .build-cache/w1/dictation-node-key "$private_dir/dictation-node-key"
mv .build-cache/w1/dictation-node-key.pub "$private_dir/dictation-node-key.pub"
chmod 600 "$private_dir/dictation-node-key" "$private_dir/dictation-node-key.pub"
/opt/homebrew/bin/whisper-cli --help >/dev/null 2>&1
```

If either destination already exists, stop and establish which installation owns
it; do not replace a durable model or key. The model move intentionally removes
the scratch copy. The cleanup GGUF remains in the same durable models directory.

## Install and update the job code

The weekly job runs an installed copy, never the working checkout. On the Mac,
install or update from a clean checkout of `main`:

```bash
cd "$HOME/Code/mac-dictation"
git switch main
git pull --ff-only origin main
./install-tools.sh
/opt/homebrew/bin/python3 "$HOME/Library/Application Support/mac-dictation/bin/recheck.py" status
```

The installer fetches `origin/main` and refuses tracked, staged or untracked changes,
or a checkout commit not contained in `origin/main`. A clean ancestor is permitted;
use the commands above to install the latest main. Git and Python 3 are required.
The single `files` list in `install-tools.sh` owns the install inventory: the weekly job,
review module and HTML/CSS/JavaScript assets, plus the synthetic proof CLI.
The Python tools use only the standard library and sibling installed modules. Files are
exported from the exact commit and installed under `$HOME/Library/Application Support/mac-dictation/bin/`
(0700), with files and `install.json` (0600). The stamp contains `commit`, the full
Git commit ID. The installer prints that ID, never private settings or data.
It replaces only listed code and the stamp; the model, key, state and private
output remain in their existing locations. Re-run the installer after updates;
switching branches alone never changes installed code.

Do not run the weekly job for installation validation: use `status` only. The
existing `verify-node.py` probes belong to initial node setup, not this update.

For a **new, unregistered** job, the slot's registration syntax is:

```bash
"$HOME/Library/Application Support/homelab/mac/homelab-profile" job add dictation-recheck \
  --calendar '{"Weekday":0,"Hour":3,"Minute":0}' \
  /opt/homebrew/bin/python3 "$HOME/Library/Application Support/mac-dictation/bin/recheck.py"
```

After the owning `homelab-profile` installation supports `job remove`, replace the
existing registration through that interface. This preserves the Sunday 03:00
calendar and avoids editing saved profile state:

```bash
profile="$HOME/Library/Application Support/homelab/mac/homelab-profile"
installed="$HOME/Library/Application Support/mac-dictation/bin/recheck.py"
"$profile" job remove dictation-recheck
"$profile" job add dictation-recheck \
  --calendar '{"Weekday":0,"Hour":3,"Minute":0}' \
  /opt/homebrew/bin/python3 "$installed"
```

Use `job list` to select only `dictation-recheck`, then assert its command and
calendar; do not publish the unfiltered list. Run the installed `recheck.py status`,
switch this checkout to a different branch, and repeat the command/calendar
assertion. Return to `main` afterward. Never use `job run` for these checks.

The profile controller reads `$HOME/.config/homelab/profiles.json`. It permits jobs
with a per-profile boolean:
set `profiles.working.jobs` to `true` for Working and, if desired,
`profiles.inference.jobs` to `true` for Inference. Both default to `true` when omitted.
For example, merge `"jobs": true` into the existing `profiles.working` object; preserve
all other fields. Do not add a `dictation-recheck` key. This permission applies to all
registered jobs in that profile. Registration does not override `"jobs": false`.
The orchestrator owns this private configuration; this repository does not read or
change it.
The slot waits for AC power and idle time and handles missed runs. Do not switch
profiles or use the slot's manual run command for validation.

Job commands are argument arrays launched directly, without a shell. Pass the
installed path with `"$HOME/Library/Application Support/mac-dictation/bin/recheck.py"`
so the calling shell expands it. Do not save a literal `~` or `$HOME` argument.

## Private files and recovery

All paths below are relative to `$HOME/Library/Application Support/mac-dictation/`.
Directories are 0700 and newly created files are 0600. The job prints only fixed
messages and counts. whisper-cli, rsync and SSH output are suppressed, including
errors. The profile slot discards stdout/stderr. Optional manual logs must be
redirected inside this private directory, never into the repository.

| Path | Format and purpose |
| --- | --- |
| `bin/` | Installed job code (0700); files 0600 |
| `bin/install.json` | Public code provenance: full `commit` ID |
| `recheck-state.json` | Version 1; `last_run` (Unix seconds), `processed_ids`, `latest_run` |
| `recheck-pending.json` | Durable transaction: run ID, row IDs, time and counts; retained on upload failure |
| `rechecks/<run-id>/summary.json` | Version 1; rows, recordings, assessed, unreadable, excluded_cancelled, excluded_silent, loops_discarded and items |
| `recheck-building/` | Pre-upload staging, cleaned on build failure or replaced after interruption |
| `rechecks/<run-id>/review.json` | Version 1, run ID, `items` in descending score order, maximum 20 |
| `rechecks/<run-id>/upload/history/<run-id>.jsonl` | One row per selected history entry |
| `rechecks/<run-id>/upload/recordings/` | Copies of recordings selected in that run |
| `recheck.lock` | Advisory exclusive lock prevents overlapping runs |
| `recheck-disabled` | Presence disables future runs |
| `dictation-node-key` | Job-only Ed25519 private key, 0600 |

History rows contain `id`, `timestamp` (Unix seconds), `recording` (relative path or
null), `text`, `cleaned_text` and `status`. No absolute audio URL, request prompts,
vocabulary or preferences are exported. Review items contain those fields plus
`recheck` and `score`. A score is normalized word-level Levenshtein distance divided
by the longer token count, in [0,1]. NFKC, case folding, punctuation removal and
whitespace splitting normalize comparison inputs. Comparison uses raw `text`;
`cleaned_text` is retained alongside it for owner review. This is a disagreement
ranking, not a correctness judgment. Zero disagreements are omitted. SwiftData
does not store a delivery receipt, so stored text is the available insertion proxy;
failed rows retain their status rather than claiming successful insertion. Rows whose
status is `canceled` are backed up but excluded from assessment and review, including
when they have no recording. This is VoiceInk's own status, not a text-label heuristic.

The first run selects all non-pending history. Later runs select unseen IDs, rather
than relying solely on timestamps: equal timestamps, delayed commits and pending
entries later completed must not be lost. A successful run advances `last_run` and
records processed IDs only after rsync succeeds. Edits to an already processed
history entry do not cause a second assessment. Missing/unsafe recordings stop the
batch instead of silently marking them processed. Entries without audio are backed
up as history, with no assessment. Zero-frame WAVs and confirmed Whisper audio-read
failures are backed up with their history but skipped for assessment. Their count
is stored as `unreadable` in the private summary and printed by the hand run,
without filenames. Missing or unsafe paths still fail because the job
cannot safely back up their audio.

Whisper loads large-v3 once per run and accepts all candidate clips with separate
output files. Zero-frame WAVs are filtered before inference; other decoding failures
are recognized from Whisper's suppressed, in-memory diagnostics. Exit 11 is accepted
only when its failed-input count matches confirmed audio-read failures. Model,
inference and output failures abort the build instead of silently losing assessments.
Nothing from Whisper's stdout or stderr is printed or saved.

## VAD, vocabulary and unreliable re-checks

Large-v3 remains the second opinion. Every inference uses whisper.cpp's `--vad` and
`--vad-model` with the official small Silero v5.1.2 model. Install it once with:

```bash
/opt/homebrew/bin/python3 "$HOME/Library/Application Support/mac-dictation/bin/recheck.py" download-vad
```

This downloads to the private `models/` directory from a pinned revision of
[ggml-org/whisper-vad](https://huggingface.co/ggml-org/whisper-vad/blob/e5614ed76a5dd4b03fad5068c89efcd2617a9d1e/ggml-silero-v5.1.2.bin).
SHA-256 must be `29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf`.
The download is verified before installation; existing models are verified without
re-downloading. A mismatching existing file is left untouched and causes failure.
The hash is checked again before every weekly assessment and re-assessment.

VAD with no detected speech produces zero output segments and an empty text file;
these recordings are counted as `excluded_silent`, backed up, and never reviewed.
Zero-frame WAVs remain classified as `unreadable`. A re-check containing any
normalized phrase of 1–64 words repeated at least three times consecutively is
discarded in its entirety, counted as `loops_discarded`, and never ranked. The rule
uses the same NFKC, case-folding and punctuation normalization as disagreement.
Two repeats and separated repetitions do not qualify. Intentional triple repetition
can be discarded too; the count makes this conservative tradeoff visible.

VoiceInk's `VocabularyWord.word` entries are read only from a temporary file copy of
`dictionary.store` and its available `-wal`/`-shm` sidecars, with integrity checks,
read-only SQLite access, schema validation and one retry, just like history.
Only that entity is queried; replacements and preferences are not read. The sorted
words are joined with comma-space and prefixed with `Important Vocabulary: `,
matching `CustomVocabularyService.swift` at `c09cc1f`. An empty dictionary adds no
prompt. This vocabulary string is supplied via Whisper's initial-prompt mechanism
(`--prompt`), matching the mechanism used by VoiceInk's `LibWhisper.swift`.
At this upstream revision VoiceInk's local Whisper request resolves a language
prompt, while its vocabulary formatter is used by enhancement; the re-check uses
that formatter for the requested vocabulary hint without reading saved prompts.
The vocabulary is never printed, exported to history, or uploaded. The CLI receives
it as a process argument, so do not capture process arguments or enable shell tracing.
Whisper limits the prompt to its model's context capacity.

**Context decision (W1d):** do not pass `--max-context 0`. In whisper.cpp 1.9.5,
[initial-prompt conditioning requires `n_max_text_ctx > 0`](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/src/whisper.cpp#L7285).
The [CLI maps `--max-context` to that field](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/examples/cli/cli.cpp#L1237)
and exposes no independent no-context option. Retain default context to preserve
the vocabulary hint, as the orchestrator authorized. VAD and loop rejection guard
against silence/noise and repeated-output failures. These settings do not guarantee
that large-v3 is more accurate than the inserted text.

## Re-assess the latest copied batch and supersede its old corrections

Close any open review first. From a clean, updated `main` checkout on the Mac:

```bash
cd "$HOME/Code/mac-dictation"
git switch main
git pull --ff-only origin main
./install-tools.sh
private_dir="$HOME/Library/Application Support/mac-dictation"
/opt/homebrew/bin/python3 "$private_dir/bin/recheck.py" download-vad
/opt/homebrew/bin/python3 "$private_dir/bin/recheck.py" reassess
/opt/homebrew/bin/python3 "$private_dir/bin/recheck.py" status
/opt/homebrew/bin/python3 "$private_dir/bin/review.py"
```

`reassess` reads only the latest run's existing `upload/history/` and
`upload/recordings/` copies, plus a new dictionary file snapshot. It never opens the
original recordings or history store, sends nothing to the node, and leaves
`recheck-state.json` byte-identical. It uses the weekly and review locks and refuses
an unfinished weekly upload. Model or assessment failure leaves existing reviews
and submissions unchanged. After a successful assessment it supersedes all existing
submissions in that run, removes their generated local import files, archives the
previous review under `superseded-reviews/`, and replaces `review.json` and
`summary.json`. The normal review command therefore opens the fresh review.
Re-running re-assessment supersedes any submissions made since the preceding one.
An interrupted superseding/promotion can be recovered by rerunning `reassess`;
revocations already written remain authoritative, even if promotion failed.

Superseding writes content-free metadata (submission and run IDs) at
`supersessions/<submission>.json`, marks the local corrections payload and latest
`review-result.json` as `superseded`, and blocks retries of revoked submissions.
There is no automatic learning job in this repository. Any learning integration must
use `review.active_corrections(run)` and then exclude `skip`/`noise` choices, as
`proposals` does. Never learn directly from a glob of backed-up correction files.
The local run is authoritative immediately. On the next **Done**, the fresh
submission includes a `supersedes` list and additive `supersessions/` files; those
are copied with the new corrections using the existing additive transfer. Old node
files remain untouched. A restored node backup must reconcile these revocations
before use (`active_corrections` also accepts its flat `corrections/` and
`supersessions/` layout). Until fresh submission copying succeeds, the node's old
backup alone is not a current learning source. No model, history or recording is
re-uploaded by this procedure.

`status` prints only five counts: reviewed (`assessed`, eligible completed
re-checks before ranking), excluded as cancelled, excluded as silent, loops discarded,
and items flagged (positive disagreements, capped at 20). It prints no IDs, paths,
text, notes or settings. Unreadable and missing-audio history are not included in
reviewed; the detailed private summary retains row/recording/unreadable counts.


## Snapshot and transfer recovery

Each snapshot copies the database and any WAL/SHM sidecars using plain file I/O.
SQLite receives only a path in the private temporary folder and opens it read-only.
It replays committed WAL frames there and runs `PRAGMA integrity_check`. Corruption
or a SQLite failure triggers one new copy after 10 seconds. Two failures skip the
run with unchanged progress state. The snapshot directory is removed in all cases.
Schema mismatch stops without retrying or uploading. Integrity checking detects
structural corruption; it is not a guarantee that a concurrent plain copy captured
every latest commit. The processed-ID ledger catches unobserved entries on later runs.

The batch, review and count-only summary are first built in `recheck-building/`.
A build exception removes this staging, before any upload starts. If the process
is interrupted before a pending transaction exists, the next run removes incomplete
staging and rebuilds it. Completed staging is recorded in `recheck-pending.json`
**before** promotion to `rechecks/<run-id>/`; an interruption during promotion is
resumed from that transaction. Thus retry never ignores a half-built new run folder.

On transfer failure, the next run resends that same complete batch before new work.
rsync uses `-rt --ignore-existing`, never `--delete`. Files already on the node are
not replaced. A crash after upload but before progress commit may resend the batch;
ignore-existing makes this safe. Completed review and staging retention is manual
in this slice. The node key is write-only, not append-enforced against a malicious
client: `rrsync -wo` confines writes, while the job's flags enforce additive copies.

## Repository validation

```bash
git ls-files -z '*.sh' | xargs -0 shellcheck
bash check-no-data.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Fixtures are constructed from synthetic rows. Tests cover WAL replay, source hashes
(including a second connection committing during the file copy), rejection of SQLite
opens on original paths, corruption retry/skip, schema rejection, pending and tied
history timestamps, normalization, ranking, confinement, unreadable-audio backup,
batch model loading, pre-upload cleanup, interrupted promotion and upload retry state.
W1d fixtures also cover canceled-row backup/exclusion, a silent WAV with mocked
empty VAD output, loop rejection, vocabulary argument passing and source hashes,
verified downloads, re-assessment without upload or state changes, noise choices
and superseded submissions/imports. GPU inference is a Mac validation step.
The concurrent-writer test compares all three source hashes immediately after the
writer's deliberate commit with their hashes after the reader returns: the reader
must add no changes beyond the test writer's own commit.

Installer tests use disposable Git repositories and synthetic homes. They cover dirty
tracked/staged/untracked refusals, unmerged commits, fetch failure, ancestor acceptance,
commit stamps, updates, permissions, symlink refusal and branch independence.
