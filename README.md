# mac-dictation

Scripts build unmodified upstream [VoiceInk](https://github.com/Beingpax/VoiceInk) at a chosen commit and apply local dictation settings. There is no fork, patch, or dependency pin. This repository holds code only; VoiceInk keeps recordings, transcripts, settings, models, vocabulary, and replacements on the Mac. These scripts are MIT licensed; VoiceInk is GPLv3.

## Prerequisites

Use macOS 27 or later and full Xcode 27.0 or later, with its licence accepted and `xcode-select -p` pointing to the Xcode developer directory. Git and network access are needed to fetch upstream and its build dependencies. ShellCheck is needed to validate repository changes (`brew install shellcheck` if missing).

Complete Xcode first-launch setup and install its Metal Toolchain before building:

```bash
sudo xcodebuild -runFirstLaunch
xcodebuild -downloadComponent MetalToolchain
xcodebuild -checkFirstLaunchStatus
xcodebuild -showComponent MetalToolchain
```

The first status command must succeed and the component report must say `Status: installed`. A successful `xcrun --find metal` alone is insufficient: it can resolve a stub without the toolchain. The build script checks both prerequisites and reports the fix command if either is missing. These two environment requirements were discovered during validation on macOS 27.0.1 / Xcode 27.0; after setup, upstream commit `c09cc1f677f40f2ee665843a61f07670210f012e` built version 2.21 without changes.

The settings script uses the system Swift interpreter supplied by Xcode. Whisper large-v3-turbo must already be downloaded in VoiceInk; neither script downloads speech models.

## Install and configure

Run in a normal terminal outside a restricted sandbox, from this repository:

```bash
./build.sh
./cleanup-server.sh install
# Complete the one-time provider step below before applying settings.
./settings.sh
./settings.sh --verify
```

`build.sh` defaults to `c09cc1f`, owns the ignored `.upstream/VoiceInk` clone, rejects changed upstream source, and runs `make local` as published. It reuses upstream's existing framework cache at `$HOME/VoiceInk-Dependencies`, which the Xcode project references directly. The validation build reused that framework successfully. Upstream builds into its clone, copies to Downloads, and clears quarantine. The script quits VoiceInk before building, installs `/Applications/VoiceInk.app` with `ditto`, verifies signing and version, and removes `$HOME/Downloads/VoiceInk.app` after installation. It prints the resolved commit, version, elapsed seconds, and signing information. The successful validation run took 163 seconds after the two prerequisite failures above; a first build can take longer.

`settings.sh` quits VoiceInk and, before any settings write, exports the current preferences domain to a dated private backup under `$HOME/Library/Application Support/mac-dictation/`. It prints the restore command, never backup contents. It applies:

- Default mode: local `ggml-large-v3-turbo`, language Auto, enabled and selected.
- Default mode: AI enhancement on with the local Custom model `qwen4` and the generic cleanup prompt, with Use System Instructions off. Selected-text, clipboard and screen context are off. Enhancement and screen context remain off in other modes.
- Auto Learn on, pinned to Custom / `qwen4`, so candidate review stays local.
- Audio cleanup and transcript cleanup off, keeping all recordings and transcripts.
- Fn (🌐) as the primary recording shortcut in hybrid mode; no secondary recording shortcut.
- Announcements, Sparkle automatic checks, and VoiceInk's update probe off.
- macOS Fn action: Do Nothing (`com.apple.HIToolbox` / `AppleFnUsageType`, integer zero).

The encoded mode and shortcut formats are taken from the default upstream commit. Other mode fields and unrelated preferences are preserved. Model and language choices in other modes are preserved, so per-app modes can override the default. The script reads only keys needed for comparison and never prints encoded settings or private content. It writes through `defaults`, verifies readback, and relaunches VoiceInk. A second run with matching settings reports `No change` and creates no new backup. `--verify` checks settings without quitting the app or writing preferences, and reports only key names and pass/mismatch status.

## Local cleanup server and one-time provider step

The server requires `/opt/homebrew/bin/llama-server` (validated configuration: 0.6.0) and runs on `127.0.0.1:8095` only. Ports 8091 and 8092 are untouched. Before installing, stop the manually launched cleanup server; the installer refuses an occupied port when its own agent is not loaded. The orchestrator uses `pkill -x llama-server` for the known manual process.

`cleanup-server.sh install` checks SHA256 `8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc` for `$HOME/Library/Application Support/mac-dictation/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`. If absent, it announces the 2,497,280,448-byte download from `lmstudio-community/Qwen3-4B-Instruct-2507-GGUF` on Hugging Face and checks the hash before installation. A mismatch stops installation. Model downloads are the only external model traffic; inference stays local.

The script generates `$HOME/Library/LaunchAgents/org.mac-dictation.cleanup.plist` with RunAtLoad and KeepAlive, bootstraps it, and waits for `/health` HTTP 200 for up to 60 seconds. It uses the fixed GPU, context and thread settings, thinking off, and `--samplers top_k --top-k 1` to force greedy decoding even though VoiceInk sends temperature 0.3. Logs are under `$HOME/Library/Logs/mac-dictation/`; keep these outside the repository. Reinstalling replaces only this agent. `./cleanup-server.sh status` checks health and reports the agent process's resident memory in KiB and GiB. The measured idle memory was 3.6 GiB on an M4 Pro; the measurement's 12 items took 0.86 seconds median and 1.5 seconds maximum. Actual memory and delay must be checked on the running Mac.

**One-time provider step (owner, in VoiceInk):** open Settings → Models → Custom, click the add button beside **Custom Enhancement Models**, and enter these fields:

| Field | Value |
| --- | --- |
| Display Name | `Local cleanup` |
| Base URL | `http://127.0.0.1:8095/v1/chat/completions` |
| Model Name | `qwen4` |
| API Key | `local` |

Despite the “Base URL” label, upstream's editor expects the full chat-completions endpoint. Save the provider; use its connection test with the server running. VoiceInk itself stores the placeholder key in the Keychain. The scripts never read or write the Keychain, never create a provider, and leave `customAIProviders` and `TranscriptionPrompt` untouched. The provider preferences and Keychain survive app rebuilds. Avoid another provider using model `qwen4`: upstream resolves Custom providers by model name. If `Local cleanup` is missing, settings exits nonzero with an instruction to this step; incorrect or ambiguous metadata also stops it. `--verify` reports presence by name only and cannot verify the Keychain credential.

After saving, run `./settings.sh` twice (the second should report `No change`), then `./settings.sh --verify`. The script preserves existing custom prompts and adds or updates the prompt titled `Local cleanup` from [cleanup-prompt.txt](cleanup-prompt.txt), verbatim without added system instructions. Reopen VoiceInk after applying settings. **The server must be healthy before dictating with cleanup**; run `./cleanup-server.sh status`. `./cleanup-server.sh test` sends two generic synthetic Spanish requests at temperature 0.3 and checks identical Spanish cleanup, printing only pass/fail.

To return to D1's cleanup behaviour:

```bash
./settings.sh --cleanup-off
./settings.sh --cleanup-off --verify
./cleanup-server.sh uninstall
```

This disables enhancement in all modes and Auto Learn, unloads the agent, and removes its generated plist. The model, logs, UI-created provider, Keychain and custom prompts are retained. The transcription model, language, Fn shortcut and recording retention stay as in D1. Both recording-deletion keys remain false in either configuration.

## Permissions and manual steps

Launch `/Applications/VoiceInk.app` and grant Microphone and Accessibility to that app in System Settings → Privacy & Security. Remove stale entries for the old Downloads copy if shown. An ad hoc rebuild may require these permissions again. **After granting permissions, quit and reopen VoiceInk**, including after a rebuild that asks for permissions again. If VoiceInk was launched before Accessibility was granted, Fn may do nothing until the app is reopened: upstream creates its shortcut event tap at launch and does not retry it. Finish or skip onboarding without enabling a cloud provider or Screen Recording, and rerun `settings.sh` if onboarding changes a decided setting.

Enable **Launch at Login** in VoiceInk Settings → General. Upstream implements this through `SMAppService.mainApp` registration, not a preferences key, so the script cannot enable it with `defaults write`. Approve it in macOS Login Items if macOS requests that.

Confirm the default mode displays large-v3-turbo, Auto, and enhancement on in VoiceInk. For the dictation check, confirm that the default mode is in use in each target app; a per-app mode may select another model or language. Confirm System Settings → Keyboard → “Press 🌐 key to” displays **Do Nothing**. The zero value was checked against macOS 27's Keyboard Settings Fn picker; if a changed preference has not taken effect, select Do Nothing in that pane.

Hybrid Fn uses a short press to start and another to stop; holding Fn for more than half a second while speaking uses push-to-talk and releasing ends the recording. Verify insertion at the cursor, correct language, and no translation in Word, Teams, VS Code, a terminal chat session, Chrome, and Safari, using English, Spanish, and Catalan. Report pass or fail and timing/indicator notes only; do not put dictated text in this repository or a PR.

**Vocabulary and replacements are kept by VoiceInk on the Mac — export them from VoiceInk after editing.** Keep those exports outside this repository.

## Update and restore

To update, pass an upstream commit hash, rebuild, and reapply settings:

```bash
./build.sh <newer-commit-hash>
./settings.sh
```

The settings schema is verified at the default commit; inspect a newer commit's setting definitions before using this settings script with a changed schema. Local builds have no trial restriction, iCloud dictionary sync, or automatic updates. The validated arm64 build is ad hoc signed with no team identifier; the script leaves upstream's signing selection unchanged.

To restore preferences, quit VoiceInk, run the exact `defaults import com.prakashjoshipax.VoiceInk` command printed by `settings.sh` with its backup filename, and reopen `/Applications/VoiceInk.app`. Backups contain private preferences: keep them outside version control. The domain backup does not restore macOS's Fn action or login registration; restore those in System Settings and VoiceInk if desired. The weekly re-check reads a temporary snapshot of the history store and reads recordings; it never writes to VoiceInk's files. Other scripts leave the stores, recordings, model directory and Keychain untouched.

## Repository checks

Before every commit:

```bash
git ls-files -z '*.sh' | xargs -0 shellcheck
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
bash check-no-data.sh
```

CI runs ShellCheck on every tracked shell script, the no-data-files guard, and Gitleaks with full Git history. The guard rejects tracked audio, JSON/JSONL, private keys, Python bytecode, CSV, plist, store/SQLite, and model files, and any file larger than 256 KiB. It checks tracked files only; ignore rules also keep the upstream clone and build output out of Git. These guards cannot identify all personal content in otherwise allowed text files: review every staged diff. GitHub secret scanning and push protection are enabled. Protected `main` requires the strict `check` job, including for administrators, and disallows force pushes and deletions.

## Weekly re-check and node copy

[Weekly setup and recovery](docs/weekly-recheck.md) covers the Mac commands, repeatable node
setup, file formats and validation. The job loads its model once per run and re-transcribes new recordings with local Whisper
large-v3 through Homebrew whisper.cpp (Metal, Auto). It compares normalized raw Whisper text
with the re-check and keeps up to 20 disagreements, largest first. Raw and cleaned text are
both retained for review. The local review page lets you listen and settle each disagreement.

The first run processes the existing history; subsequent runs process unseen completed,
failed or canceled entries, including entries completed after an earlier snapshot. Each run
copies new recordings and a JSON-lines history file to the node's existing encrypted weekly
backup source. Copies use `--ignore-existing`, with no deletion. VoiceInk keeps its originals
on the Mac. The snapshot is a plain copy of the store and WAL/SHM sidecars; SQLite opens only
the copy, checks integrity, retries once after 10 seconds on failure, then skips without
advancing state if the second copy fails.

Reviews, state, staging, the dedicated key and optional count-only logs stay under
`$HOME/Library/Application Support/mac-dictation/` (0700), outside this public repository.
Unreadable and empty recordings retain their additive audio/history backup and are skipped
for assessment, with only an unreadable count in the private summary and output.
The profile job slot schedules Sunday at 03:00 local time, subject to the profile jobs permission,
AC-power and idle checks. The job never changes profiles. It runs installed code,
never the working checkout. Install and update on the Mac from clean `main`:

```bash
cd "$HOME/Code/mac-dictation"
git switch main
git pull --ff-only origin main
./install-tools.sh
```

The installer fetches `origin/main`, refuses dirty or unmerged commits, and copies
its single code inventory into `$HOME/Library/Application Support/mac-dictation/bin/`
(0700). `bin/install.json` records the full installed commit. Re-run after updates;
branch switches do not alter installed code. To replace an existing job command,
use `homelab-profile job remove` and then `job add`, preserving its calendar as
shown in the [migration and verification steps](docs/weekly-recheck.md#install-and-update-the-job-code).
Do not edit the profile slot's state file.

To turn it off (including any already pending scheduled run):

```bash
/opt/homebrew/bin/python3 "$HOME/Library/Application Support/mac-dictation/bin/recheck.py" disable
```

Use `enable` to resume, or `status` to print only enabled state, review existence and counts.
Disabling does not remove recordings, reviews, backups, the model, or the key. A run already
in progress is allowed to finish.


## Five-minute weekly review

When a weekly run finishes with items, **Weekly Dictation Review** posts a count-only
notification. Click it, or open the latest batch by hand:

```bash
/opt/homebrew/bin/python3 "$HOME/Code/mac-dictation/review.py"
```

Use **Play recording** (or the audio controls), compare the highlighted words, then choose **Inserted was right**,
**Re-check was right**, or type the correct text (prefilled with the re-check). Skipping is
allowed. Playback shows Playing/Paused/Finished, elapsed time and a progress bar even for silent audio. Playback is available while the review server is open. The page preserves W1's order and limit of 20. Inserted text shows the cleaned text
when available, with the original Whisper text alongside it; the stored text remains an
insertion proxy rather than a delivery receipt.

**Done** saves every choice privately, builds a VoiceInk dictionary JSON file, and reveals it
in Finder. In **VoiceInk → Dictionary → Import**, select that file and use **Merge**. Review
VoiceInk's preview before importing. Nothing is applied automatically. VoiceInk skips duplicate
vocabulary and duplicate or conflicting replacement sources in Merge mode; Replace would
remove existing entries and is not the intended workflow. An all-skipped or inserted-right
batch can produce an empty archive; there is then nothing to import.

Vocabulary proposals are changed words in confirmed corrections compared with the original
Whisper text. A replacement requires the same changed phrase in two distinct items, with no
conflicting correction or confirmed correct use. Changed spans are limited to four words per
side for replacements; punctuation-only and capitalization-only differences create no proposals.
These are conservative suggestions, not certainty; confirm them in VoiceInk's preview.

The server binds only `127.0.0.1` on a random port with an unguessable session URL. It exits
on Done, tab closure, or five minutes without activity. Closing or timing out before Done
discards unsaved choices. Do not share the session URL. There are no external page resources
or packages. Requests require the expected Host; saving also requires a same-origin JSON POST.

Files stay below the batch's private `rechecks/<run-id>/` folder (directories 0700, files 0600):

- `correction-copies/<submission>/corrections/<submission>.json`: version 1, run ID, and one
  `{id, choice, text}` record per item; skipped text is null.
- `Imports/VoiceInk-<submission>.json`: VoiceInk version-1 dictionary archive; Finder reveals it.
- `review-result.json`: the latest submission, relative import path and node-copy outcome.

Every Done creates an immutable submission. Its corrections use W1's dedicated write-only
key and `rsync -rt --ignore-existing` mechanism; import files are not sent. Failed copies remain
locally, with a notice on the page. Retry all saved submissions in the latest batch with:

```bash
/opt/homebrew/bin/python3 "$HOME/Code/mac-dictation/review.py" --retry-copies
```

For an older batch, add `--review "$HOME/Library/Application Support/mac-dictation/rechecks/<run-id>/review.json"`.
The write-only key cannot verify remote contents; its admission and live upload proof remain
W1 prerequisites. No files are automatically deleted from either destination.

The notification hook compiles a local AppleScript app under the private folder with macOS's
`osacompile`, gives it the stable bundle identifier `org.mac-dictation.review`, and re-signs it ad hoc after updating `Info.plist`. It verifies the signature before launching the app to consume a one-use private notification marker and post the count. That first launch does not open a page. Clicking the notification subsequently runs that app's
review handler, as described by [Apple's notification documentation](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/DisplayNotifications.html).
No developer signing identity or third-party notifier is used. On the first notification,
macOS asks the owner once for permission to notify as **Weekly Dictation Review**. Choose
**Allow**, then rerun the notification if the first attempt was suppressed. If notifications
were denied, enable them in System Settings → Notifications → Weekly Dictation Review. GUI delivery and click
behavior require the orchestrator's Mac proof; scheduled execution must reach the logged-in
user's GUI session. A notification failure preserves W1's completed batch and prints a fixed
instruction to run the review manually.

To silence review notifications while keeping weekly assessments, create the private marker:

```bash
umask 077
touch "$HOME/Library/Application Support/mac-dictation/review-notifications-disabled"
```

Remove that marker to resume notifications. `recheck.py disable` turns off future weekly
assessments and their notifications; an active run may finish. Manual review still works.
See [the synthetic Mac proof](docs/review-proof.md) for orchestrator-only commands. Never use
real items in that proof, publish private files, or put them in repository logs.


Synthetic proof sessions use `review.py --no-copy`. The generated proof folder also has a
`.review-no-copy` marker: sessions opened through the notification and `--retry-copies` are
therefore prevented from sending synthetic corrections, even when the real key is installed.
The page and save result explicitly report that copying is disabled.
