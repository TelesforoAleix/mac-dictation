#!/bin/bash
set +x
set -euo pipefail
umask 077

[[ $(uname -s) == Darwin ]] || { printf 'Requires macOS.\n' >&2; exit 1; }
[[ $# == 1 ]] || { printf 'Usage: ./cleanup-server.sh install|uninstall|status|test\n' >&2; exit 1; }
label=org.mac-dictation.cleanup
agent="$HOME/Library/LaunchAgents/$label.plist"
logs="$HOME/Library/Logs/mac-dictation"
model_dir="$HOME/Library/Application Support/mac-dictation/models"
model="$model_dir/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
expected_hash=8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc
service="gui/$(id -u)/$label"
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
fail() { printf '%s\n' "$1" >&2; exit 1; }
health() {
    [[ $(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 2 http://127.0.0.1:8095/health) == 200 ]]
}
loaded() { launchctl print "$service" >/dev/null 2>&1; }
case "$1" in
    install)
        [[ -x /opt/homebrew/bin/llama-server ]] || fail 'Missing /opt/homebrew/bin/llama-server.'
        # Only replace our own agent; never kill an unmanaged process or another service.
        if ! loaded && lsof -nP -iTCP:8095 -sTCP:LISTEN >/dev/null 2>&1; then
            fail 'Port 8095 is occupied; stop the manual cleanup server before installing.'
        fi
        mkdir -p "$model_dir" "$logs" "$HOME/Library/LaunchAgents"
        if [[ ! -f "$model" ]]; then
            printf 'Model download: 2,497,280,448 bytes (about 2.50 GB).\n'
            download=$(mktemp "$model_dir/.download.XXXXXX")
            trap 'rm -f "$download"' EXIT
            curl --fail --location --silent --show-error --output "$download" \
                'https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
            [[ $(shasum -a 256 "$download" | awk '{print $1}') == "$expected_hash" ]] || fail 'Model SHA256 mismatch; refusing installation.'
            mv "$download" "$model"
            trap - EXIT
        fi
        [[ $(shasum -a 256 "$model" | awk '{print $1}') == "$expected_hash" ]] || fail 'Model SHA256 mismatch; refusing installation.'
        printf 'Verified: model SHA256\n'
        # PropertyListSerialization escapes paths, including spaces and XML metacharacters.
        /usr/bin/swift - "$agent" "$label" "$model" "$logs" <<'SWIFT'
import Foundation
let args = CommandLine.arguments
let plist: [String: Any] = [
    "Label": args[2], "RunAtLoad": true, "KeepAlive": true,
    "ProgramArguments": ["/opt/homebrew/bin/llama-server", "-m", args[3],
        "--alias", "qwen4", "--host", "127.0.0.1", "--port", "8095",
        "-ngl", "99", "-c", "8192", "-t", "6", "--no-webui", "--temp", "0",
        "--seed", "0", "--samplers", "top_k", "--top-k", "1", "-n", "2048",
        "--chat-template-kwargs", "{\"enable_thinking\":false}"],
    "StandardOutPath": args[4] + "/cleanup-server.stdout.log",
    "StandardErrorPath": args[4] + "/cleanup-server.stderr.log"
]
do {
    let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
    try data.write(to: URL(fileURLWithPath: args[1]), options: .atomic)
} catch {
    fputs("Cannot generate LaunchAgent.\n", stderr)
    exit(1)
}
SWIFT
        chmod 600 "$agent"
        if loaded; then
            launchctl bootout "$service" >/dev/null 2>&1 || fail 'Cannot unload existing cleanup agent.'
        fi
        launchctl bootstrap "gui/$(id -u)" "$agent" >/dev/null 2>&1 || fail 'Cannot bootstrap cleanup agent.'
        printf 'Loaded: org.mac-dictation.cleanup\n'
        # A per-request limit prevents a stalled health request extending the deadline.
        deadline=$((SECONDS + 60))
        until health; do
            # shellcheck disable=SC2016 # Print the portable log path, never the owner's path.
            (( SECONDS < deadline )) || fail 'Health timeout after 60 seconds; inspect $HOME/Library/Logs/mac-dictation/cleanup-server.stderr.log.'
            sleep 1
        done
        printf 'Verified: /health HTTP 200\n'
        ;;
    uninstall)
        if loaded; then
            launchctl bootout "$service" >/dev/null 2>&1 || fail 'Cannot unload cleanup agent.'
        fi
        rm -f "$agent"
        printf 'Removed: org.mac-dictation.cleanup (model and logs retained)\n'
        ;;
    status)
        loaded || fail 'Cleanup agent is not loaded.'
        pid=$(launchctl print "$service" 2>/dev/null | awk '$1 == "pid" && $2 == "=" {print $3; exit}')
        [[ -n "$pid" ]] || fail 'Cleanup agent is loaded but has no running process.'
        rss=$(ps -p "$pid" -o rss= | awk '{print $1}')
        [[ -n "$rss" ]] || fail 'Cleanup server process exited.'
        printf 'Loaded: org.mac-dictation.cleanup\n'
        awk -v rss="$rss" 'BEGIN {printf "Resident memory: %s KiB (%.2f GiB)\n", rss, rss/1048576}'
        health || fail 'Health check failed.'
        printf 'Verified: /health HTTP 200\n'
        ;;
    test)
        health || fail 'Health check failed.'
        # Generic synthetic example only. Never read VoiceInk preferences or owner data.
        /usr/bin/swift - "$script_dir/cleanup-prompt.txt" <<'SWIFT'
import Foundation
import Dispatch
func fail(_ message: String) -> Never {
    fputs(message + "\n", stderr)
    exit(1)
}
guard let prompt = try? String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8) else {
    fail("Cannot read generic cleanup prompt.")
}
let body: [String: Any] = ["model": "qwen4", "temperature": 0.3, "messages": [
    ["role": "system", "content": prompt.trimmingCharacters(in: .newlines)],
    ["role": "user", "content": "<TRANSCRIPT>Eh, trae el cuaderno el lunes, no, el martes.</TRANSCRIPT>"]
]]
var outputs = [String]()
for _ in 0..<2 {
    var request = URLRequest(url: URL(string: "http://127.0.0.1:8095/v1/chat/completions")!)
    request.httpMethod = "POST"
    request.timeoutInterval = 30
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("Bearer local", forHTTPHeaderField: "Authorization")
    request.httpBody = try JSONSerialization.data(withJSONObject: body)
    let semaphore = DispatchSemaphore(value: 0)
    let task = URLSession.shared.dataTask(with: request) { data, response, error in
        defer { semaphore.signal() }
        guard error == nil, (response as? HTTPURLResponse)?.statusCode == 200,
              let data = data,
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let choices = object["choices"] as? [[String: Any]],
              let message = choices.first?["message"] as? [String: Any],
              let content = message["content"] as? String else {
            fail("Synthetic cleanup request failed.")
        }
        outputs.append(content.trimmingCharacters(in: .whitespacesAndNewlines))
    }
    task.resume()
    guard semaphore.wait(timeout: .now() + 35) == .success else { fail("Synthetic request timed out.") }
}
guard outputs[0] == outputs[1] else { fail("Mismatch: two synthetic outputs differ.") }
guard outputs[0] == "Trae el cuaderno el martes." else {
    fail("Mismatch: synthetic output differs from expected Spanish cleanup; inspect using only synthetic data.")
}
print("Verified: two identical Spanish outputs at temperature 0.3")
SWIFT
        ;;
    *) fail 'Usage: ./cleanup-server.sh install|uninstall|status|test' ;;
esac
