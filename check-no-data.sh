#!/bin/bash
set -euo pipefail

failed=0
while IFS= read -r -d '' path; do
    lower=$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')
    case "$lower" in
        *.wav|*.m4a|*.mp3|*.caf|*.json|*.csv|*.plist|*.store*|*.sqlite*|*.bin|*.gguf|*.mlmodelc|*.mlmodelc/*)
            printf 'Forbidden data file: %s\n' "$path" >&2
            failed=1
            ;;
    esac
    if [[ -f "$path" ]] && (( $(wc -c < "$path") > 262144 )); then
        printf 'File exceeds 256 KiB: %s\n' "$path" >&2
        failed=1
    fi
done < <(git ls-files -z)
exit "$failed"
