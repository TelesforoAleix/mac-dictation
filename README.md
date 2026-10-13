# mac-dictation

Two scripts build unmodified upstream [VoiceInk](https://github.com/Beingpax/VoiceInk) at a chosen commit and apply local dictation settings. There is no fork, patch, or dependency pin. This repository holds code only; VoiceInk keeps recordings, transcripts, settings, models, vocabulary, and replacements on the Mac. These scripts are MIT licensed; VoiceInk is GPLv3.

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
./settings.sh
./settings.sh --verify
```

`build.sh` defaults to `c09cc1f`, owns the ignored `.upstream/VoiceInk` clone, rejects changed upstream source, and runs `make local` as published. It reuses upstream's existing framework cache at `$HOME/VoiceInk-Dependencies`, which the Xcode project references directly. The validation build reused that framework successfully. Upstream builds into its clone, copies to Downloads, and clears quarantine. The script quits VoiceInk before building, installs `/Applications/VoiceInk.app` with `ditto`, verifies signing and version, and removes `$HOME/Downloads/VoiceInk.app` after installation. It prints the resolved commit, version, elapsed seconds, and signing information. The successful validation run took 163 seconds after the two prerequisite failures above; a first build can take longer.

`settings.sh` quits VoiceInk and, before any settings write, exports the current preferences domain to a dated private backup under `$HOME/Library/Application Support/mac-dictation/`. It prints the restore command, never backup contents. It applies:

- Default mode: local `ggml-large-v3-turbo`, language Auto, enabled and selected.
- AI enhancement off in every mode, with Screen Recording context disabled; Auto Learn off.
- Audio cleanup and transcript cleanup off, keeping all recordings and transcripts.
- Fn (🌐) as the primary recording shortcut in hybrid mode; no secondary recording shortcut.
- Announcements, Sparkle automatic checks, and VoiceInk's update probe off.
- macOS Fn action: Do Nothing (`com.apple.HIToolbox` / `AppleFnUsageType`, integer zero).

The encoded mode and shortcut formats are taken from the default upstream commit. Other mode fields and unrelated preferences are preserved. Model and language choices in other modes are preserved, so per-app modes can override the default. The script reads only keys needed for comparison and never prints encoded settings or private content. It writes through `defaults`, verifies readback, and relaunches VoiceInk. A second run with matching settings reports `No change` and creates no new backup. `--verify` checks settings without quitting the app or writing preferences, and reports only key names and pass/mismatch status.

## Permissions and manual steps

Launch `/Applications/VoiceInk.app` and grant Microphone and Accessibility to that app in System Settings → Privacy & Security. Remove stale entries for the old Downloads copy if shown. An ad hoc rebuild may require these permissions again. Finish or skip onboarding without enabling a cloud provider or Screen Recording, and rerun `settings.sh` if onboarding changes a decided setting.

Enable **Launch at Login** in VoiceInk Settings → General. Upstream implements this through `SMAppService.mainApp` registration, not a preferences key, so the script cannot enable it with `defaults write`. Approve it in macOS Login Items if macOS requests that.

Confirm the default mode displays large-v3-turbo, Auto, and enhancement off in VoiceInk. For the dictation check, confirm that the default mode is in use in each target app; a per-app mode may select another model or language. Confirm System Settings → Keyboard → “Press 🌐 key to” displays **Do Nothing**. The zero value was checked against macOS 27's Keyboard Settings Fn picker; if a changed preference has not taken effect, select Do Nothing in that pane.

Hybrid Fn uses a short press to start and another to stop; holding Fn for more than half a second while speaking uses push-to-talk and releasing ends the recording. Verify insertion at the cursor, correct language, and no translation in Word, Teams, VS Code, a terminal chat session, Chrome, and Safari, using English, Spanish, and Catalan. Report pass or fail and timing/indicator notes only; do not put dictated text in this repository or a PR.

**Vocabulary and replacements are kept by VoiceInk on the Mac — export them from VoiceInk after editing.** Keep those exports outside this repository.

## Update and restore

To update, pass an upstream commit hash, rebuild, and reapply settings:

```bash
./build.sh <newer-commit-hash>
./settings.sh
```

The settings schema is verified at the default commit; inspect a newer commit's setting definitions before using this settings script with a changed schema. Local builds have no trial restriction, iCloud dictionary sync, or automatic updates. The validated arm64 build is ad hoc signed with no team identifier; the script leaves upstream's signing selection unchanged.

To restore preferences, quit VoiceInk, run the exact `defaults import com.prakashjoshipax.VoiceInk` command printed by `settings.sh` with its backup filename, and reopen `/Applications/VoiceInk.app`. Backups contain private preferences: keep them outside version control. The domain backup does not restore macOS's Fn action or login registration; restore those in System Settings and VoiceInk if desired. Scripts never touch VoiceInk's SwiftData stores, recordings, model directory, or Keychain.

## Repository checks

Before every commit:

```bash
shellcheck build.sh settings.sh check-no-data.sh
bash check-no-data.sh
```

CI runs ShellCheck on every tracked shell script, the no-data-files guard, and Gitleaks with full Git history. The guard rejects tracked audio, JSON, CSV, plist, store/SQLite, and model files, and any file larger than 256 KiB. It checks tracked files only; ignore rules also keep the upstream clone and build output out of Git. These guards cannot identify all personal content in otherwise allowed text files: review every staged diff. GitHub secret scanning and push protection are enabled. Protected `main` requires the strict `check` job, including for administrators, and disallows force pushes and deletions.
