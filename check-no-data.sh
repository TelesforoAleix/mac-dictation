#!/bin/bash
set -euo pipefail

failed=0
# Fail even when the caller is outside a Git repository (process substitution hides errors).
git rev-parse --is-inside-work-tree >/dev/null
while IFS= read -r -d '' path; do
    lower=$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')
    case "$lower" in
        *.wav|*.m4a|*.mp3|*.caf|*.json|*.jsonl|*.csv|*.plist|*.store*|*.sqlite*|*.bin|*.gguf|*.mlmodelc|*.mlmodelc/*|*.pyc|*/__pycache__/*|*dictation-node-key*)
            printf 'Forbidden data file: %s\n' "$path" >&2
            failed=1
            ;;
    esac
    if [[ -f "$path" ]] && LC_ALL=C grep -Eq '^-----BEGIN ([A-Z0-9]+ )?PRIVATE KEY-----' "$path"; then
        printf 'Forbidden private key file: %s\n' "$path" >&2
        failed=1
    fi
    if [[ -f "$path" ]] && (( $(wc -c < "$path") > 262144 )); then
        printf 'File exceeds 256 KiB: %s\n' "$path" >&2
        failed=1
    fi
done < <(git ls-files -z)
exit "$failed"
