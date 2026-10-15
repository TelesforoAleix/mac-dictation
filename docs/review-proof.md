# Orchestrator: synthetic W2 proof on the Mac

Run outside the executor sandbox, in the W2 worktree. This proof creates only three generic
sentences and three half-second silence WAVs. It never reads VoiceInk's files. Do not run the
weekly assessment for this proof. The owner reviews a real batch later.

```bash
set -e
umask 077
cd "$HOME/Code/mac-dictation-w2"
export PYTHONDONTWRITEBYTECODE=1
python3 review-proof.py prepare
proof_dir="$HOME/Library/Application Support/mac-dictation/w2-synthetic-proof"
python3 review.py --review "$proof_dir/review.json" --session "$proof_dir/session.json" \
  >"$proof_dir/review.log" 2>&1 &
review_pid=$!
python3 review-proof.py check
wait "$review_pid"
```

The review command opens the browser. `check` inspects `lsof -nP -a -p <pid> -iTCP
-sTCP:LISTEN -Fn`, requiring exactly one `127.0.0.1:<port>` listener. It POSTs one answer
per item to the capability URL's `save` endpoint (re-check, typed correction, inserted-right),
requires three 0600 corrections, then compiles and runs the **actual upstream VoiceInk
`DictionaryArchive.swift` decoder at c09cc1f** against the produced import. It requires
one vocabulary term and one replacement and checks that Done closed the listener.
Only fixed messages and counts are printed. If SSH admission is still pending, local proof
can pass with the node copy explicitly pending. The live node proof remains outstanding.

For the notification and click path, run:

```bash
python3 review-proof.py notify
```

Require **3 dictations to review**. Click the notification; the same three-item page must
open. Allow notifications for Weekly Dictation Review if macOS requests it. To exercise the
same app `run` handler by command (without clicking), use:

```bash
open -a "$proof_dir/Weekly Dictation Review.app"
```

Close the synthetic page without Done (avoids another submission). Confirm its Python
review listener has exited, or wait five minutes for idle expiry. Report notification
appearance, click-to-page, play control, highlights, typed field and shutdown as pass/fail
only. If clicking cannot launch review without a signed app, stop and escalate under W2's
contract. Do not substitute a notification that merely opens Script Editor.

Remove generated synthetic files only after all synthetic review pages have closed:

```bash
python3 review-proof.py cleanup
```

Cleanup refuses an existing scripted session and requires the synthetic marker. It removes
only the generated local proof folder, including its notification app, audio, corrections,
import, log and Swift build artifacts. Any additive synthetic node copy is retained; deleting
remote backup data is outside this proof. Share only the count-only output and pass/fail.
The PR still waits for W1's merge and the owner's later real review/import check.
