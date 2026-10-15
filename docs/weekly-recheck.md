# Weekly re-check

The source contract is VoiceInk 2.21 at `c09cc1f`. The model in
`VoiceInk/Infrastructure/Persistence/Models/Transcription.swift` has `id`, `timestamp`,
`text`, `enhancedText`, `audioFileURL`, duration and status. The recording workflow
writes UUID-named WAVs into VoiceInk's `Recordings/` directory. No dictionary or
preferences are queried. The reader validates the SQLite entity and column layout
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

Run once by hand after installing the model and key:

```bash
/opt/homebrew/bin/python3 "$HOME/Code/mac-dictation/recheck.py"
/opt/homebrew/bin/python3 "$HOME/Code/mac-dictation/recheck.py" status
/opt/homebrew/bin/python3 "$HOME/Code/mac-dictation/verify-node.py"
```

Only share the count-only output. Never show the review or history contents.
`verify-node.py` uses the dedicated key to upload a synthetic file twice with changed
content, then checks its hash through administrative SSH to prove zero overwrites.
It leaves the small synthetic `.w1-validation/synthetic.txt` in the backup folder.
It separately tries an SSH command, an rsync read, and a `../` write escape, requiring
rrsync refusals rather than authentication failures. It reports aggregate WAV/history
counts using administrative SSH; the write-only job key cannot audit remote files.

Register the schedule only after the hand run and probes pass:

```bash
"$HOME/Code/homelab-v2/mac/homelab-profile" job add dictation-recheck \
  --calendar '{"Weekday":0,"Hour":3,"Minute":0}' \
  /opt/homebrew/bin/python3 "$HOME/Code/mac-dictation/recheck.py"
```

The profile controller reads `$HOME/.config/homelab/profiles.json`. Its current
implementation permits jobs with a per-profile boolean, **not** a list of job names:
set `profiles.working.jobs` to `true` for Working and, if desired,
`profiles.inference.jobs` to `true` for Inference. Both default to `true` when omitted.
For example, merge `"jobs": true` into the existing `profiles.working` object; preserve
all other fields. Do not add a `dictation-recheck` key. This permission applies to all
registered jobs in that profile. Registration does not override `"jobs": false`.
The orchestrator owns this private configuration; this repository does not read or
change it.
The slot waits for AC power and idle time and handles missed runs. Do not switch
profiles or use the slot's manual run command for validation.

## Private files and recovery

All paths below are relative to `$HOME/Library/Application Support/mac-dictation/`.
Directories are 0700 and newly created files are 0600. The job prints only fixed
messages and counts. whisper-cli, rsync and SSH output are suppressed, including
errors. The profile slot discards stdout/stderr. Optional manual logs must be
redirected inside this private directory, never into the repository.

| Path | Format and purpose |
| --- | --- |
| `recheck-state.json` | Version 1; `last_run` (Unix seconds), `processed_ids`, `latest_run` |
| `recheck-pending.json` | Durable transaction: run ID, row IDs, time and counts; retained on upload failure |
| `rechecks/<run-id>/summary.json` | Version 1; counts of rows, recordings, assessed, unreadable and review items |
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
failed/canceled rows retain their status rather than claiming successful insertion.

The first run selects all non-pending history. Later runs select unseen IDs, rather
than relying solely on timestamps: equal timestamps, delayed commits and pending
entries later completed must not be lost. A successful run advances `last_run` and
records processed IDs only after rsync succeeds. Edits to an already processed
history entry do not cause a second assessment. Missing/unsafe recordings stop the
batch instead of silently marking them processed. Entries without audio are backed
up as history, with no assessment. Zero-frame WAVs and confirmed Whisper audio-read
failures are backed up with their history but skipped for assessment. Their count
is stored as `unreadable` in the private summary and printed by the hand run and
`status`, without filenames. Missing or unsafe paths still fail because the job
cannot safely back up their audio.

Whisper loads large-v3 once per run and accepts all candidate clips with separate
output files. Zero-frame WAVs are filtered before inference; other decoding failures
are recognized from Whisper's suppressed, in-memory diagnostics. Exit 11 is accepted
only when its failed-input count matches confirmed audio-read failures. Model,
inference and output failures abort the build instead of silently losing assessments.
Nothing from Whisper's stdout or stderr is printed or saved.

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

The initial failed hand run predates this staging scheme. Its orphaned folder can
be removed outside the sandbox with this guarded command. It removes only the
reported incomplete copies, with no progress/pending state and no history/review:

```bash
/opt/homebrew/bin/python3 -B - <<'PY'
from pathlib import Path
import re
import shutil
root = Path.home() / 'Library/Application Support/mac-dictation'
assert not (root / 'recheck-state.json').exists()
assert not (root / 'recheck-pending.json').exists()
folders = list((root / 'rechecks').glob('05c0e6*'))
assert len(folders) == 1
folder = folders[0]
assert folder.is_dir() and not folder.is_symlink()
assert re.fullmatch(r'[0-9a-f]{32}', folder.name)
paths = list(folder.rglob('*'))
assert not any(p.is_symlink() for p in paths)
files = [p for p in paths if p.is_file()]
assert len(files) == 56
assert all(p.parent == folder / 'upload/recordings' and p.suffix == '.wav' for p in files)
shutil.rmtree(folder)
print('Incomplete run folders removed: 1; staged copies removed: 56.')
PY
```

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
The concurrent-writer test compares all three source hashes immediately after the
writer's deliberate commit with their hashes after the reader returns: the reader
must add no changes beyond the test writer's own commit.
