# Orchestrator: synthetic W2 proof on the Mac

Run outside the executor sandbox, **after W2 is explicitly authorized and merged**. This
procedure does not authorize a merge: the owner's notification and real-review checks are
still gates. The installer refuses an unmerged W2 commit; do not bypass that check.

Install from clean `main`, then run the installed synthetic proof CLI. It imports the sibling
installed review module, whose notification callback launches the installed `review.py` and
serves the installed HTML/CSS/JavaScript assets. No review worktree is needed at runtime.
Regenerate the synthetic applet below; do not reuse a notification emitted by the earlier worktree copy.
This proof creates only three generic sentences and three four-second silence WAVs. It never
reads VoiceInk's files. Do not run the weekly assessment for this proof.

Close old synthetic review tabs before starting. The guarded cleanup below removes only a
marked synthetic folder and refuses cleanup while its review lock is held. If a tab was
abandoned, allow five minutes for its idle timeout first.

```bash
set -e
umask 077
cd "$HOME/Code/mac-dictation"
git switch main
git pull --ff-only origin main
./install-tools.sh
export PYTHONDONTWRITEBYTECODE=1
tools_dir="$HOME/Library/Application Support/mac-dictation/bin"
proof_dir="$HOME/Library/Application Support/mac-dictation/w2-synthetic-proof"
if [ -d "$proof_dir" ]; then python3 "$tools_dir/review-proof.py" cleanup; fi
python3 "$tools_dir/review-proof.py" prepare
python3 "$tools_dir/review-proof.py" notify
python3 "$tools_dir/review-proof.py" notification-only
```

Require **3 dictations to review** and **no page opening before a click**. `notification-only`
requires only that the posting app consumed its private marker and did not start a review
server; it cannot verify delivery or visibility. The app now has the stable bundle identifier
`org.mac-dictation.review`, and its edited bundle is re-signed ad hoc and verified before
launch. On the first notification macOS asks the owner once for permission to notify as
**Weekly Dictation Review**. Choose **Allow**. If the first attempt was suppressed, rerun:

```bash
python3 "$tools_dir/review-proof.py" notify
python3 "$tools_dir/review-proof.py" notification-only
```

If permission was previously denied, enable the app in System Settings → Notifications.
Confirm the notification is actually visible, then click the
notification. The three-item review must open. **While that review is still open**, press
**Play recording**: require **Playing**, elapsed time advancing toward **4.0 seconds**, a
moving progress bar, then **Finished**. Silence is intentional. Verify pause/replay, highlights
and editing. Report pass/fail only. Do not press Done yet; close the synthetic tab and allow
its server to exit before the scripted step below (if necessary, wait five minutes).

To inspect the app identity and verify its ad hoc signature independently:

```bash
/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$proof_dir/Weekly Dictation Review.app/Contents/Info.plist"
/usr/bin/codesign --verify --strict "$proof_dir/Weekly Dictation Review.app"
```

Require `org.mac-dictation.review` and signature verification exit 0.

If no click is available, this command exercises the app's later no-marker launch handler;
it does not substitute for verifying that notification posting itself leaves the page closed:

```bash
open -a "$proof_dir/Weekly Dictation Review.app"
```

If notification clicking requires a signed app, stop and escalate. Do not substitute a
notification that merely opens Script Editor.

Run the automated save proof separately, with no browser tab or Finder window:

```bash
python3 "$tools_dir/review.py" --review "$proof_dir/review.json" --no-copy --no-open \
  --session "$proof_dir/session.json" >"$proof_dir/review.log" 2>&1 &
review_pid=$!
python3 "$tools_dir/review-proof.py" check
wait "$review_pid"
```

`check` inspects `lsof -nP -a -p <pid> -iTCP -sTCP:LISTEN -Fn`, requiring exactly one
`127.0.0.1:<port>` listener. Before saving, it fetches all three WAVs: HTTP 200, `audio/wav`,
four-second decodable PCM, and HTTP 206 with exact Content-Range and bytes for initial,
open-ended and suffix ranges. It POSTs one answer per item to the capability URL's `save`
endpoint (re-check, typed correction, inserted-right), requires three 0600 corrections,
then compiles and runs the **actual upstream VoiceInk `DictionaryArchive.swift` decoder
at c09cc1f** against the produced import. It requires one vocabulary term and one replacement
and checks that Done closed the listener. Only fixed messages and counts are printed.

**No synthetic node copy is permitted, regardless of whether W1's key is installed.**
`prepare` writes `.review-no-copy` in the synthetic folder. The notification passes
`--no-copy` too, and the scripted run explicitly uses it. The marker also blocks retrying
these submissions. `check` requires `copy_disabled: true` before saving and requires
`copied: false` afterward. Live node copying is a separate proof.

Remove generated synthetic files after all synthetic review pages have closed:

```bash
python3 "$tools_dir/review-proof.py" cleanup
```

Cleanup refuses an existing scripted session and requires both the synthetic marker and
an available review lock. It removes only the generated local proof folder, including its
notification app, audio, corrections, import, log and Swift build artifacts. Share only
count-only output and pass/fail. W1 and W1c have merged; W2 remains unmerged until the
orchestrator explicitly authorizes it after the required owner checks.
