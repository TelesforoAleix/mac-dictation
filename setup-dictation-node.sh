#!/bin/bash
# Run as root on the node; public key arrives on stdin, never private material.
set -euo pipefail
umask 077
[[ $(id -u) == 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
IFS= read -r public_key
[[ "$public_key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+\ dictation-weekly$ ]] || exit 1
user=dictation-backup
home=/var/lib/dictation-backup
target=/srv/homelab/voice-data/originals/dictation
if ! getent passwd "$user" >/dev/null; then
    useradd --system --user-group --home-dir "$home" --create-home --shell /bin/sh "$user"
    passwd --lock "$user" >/dev/null
fi
# Refuse to repurpose an existing account with different properties.
[[ $(getent passwd "$user" | cut -d: -f6-7) == "$home:/bin/sh" ]]
[[ $(id -Gn "$user") == "$user" ]]
[[ $(getent shadow "$user" | cut -d: -f2) == '!'* ]]
install -d -o root -g root -m 755 "$home"
install -d -o root -g "$user" -m 750 "$home/.ssh"
# sshd reads root-owned authorized_keys; the restricted account cannot alter it.
# Directory traversal only for the account; no access to sibling eval data.
# libacl is already installed on the node; the setfacl executable is not required.
python3 - "$user" <<'PYACL'
import ctypes
import pwd
import re
import sys
acl = ctypes.CDLL('libacl.so.1', use_errno=True)
acl.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
acl.acl_get_file.restype = ctypes.c_void_p
acl.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
acl.acl_to_text.restype = ctypes.c_void_p
acl.acl_from_text.argtypes = [ctypes.c_char_p]
acl.acl_from_text.restype = ctypes.c_void_p
acl.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
acl.acl_free.argtypes = [ctypes.c_void_p]
uid = pwd.getpwnam(sys.argv[1]).pw_uid
for path in ('/srv/homelab', '/srv/homelab/voice-data', '/srv/homelab/voice-data/originals'):
    old = acl.acl_get_file(path.encode(), 0x8000)  # ACL_TYPE_ACCESS
    if not old:
        raise SystemExit('Cannot read ancestor ACL.')
    text_pointer = acl.acl_to_text(old, None)
    if not text_pointer:
        raise SystemExit('Cannot decode ancestor ACL.')
    text = ctypes.string_at(text_pointer).decode()
    acl.acl_free(text_pointer)
    acl.acl_free(old)
    # Preserve every other entry and give this user only directory traversal.
    text = re.sub(r'^user:(?:' + str(uid) + '|' + re.escape(sys.argv[1]) + r'):.*\n', '', text, flags=re.M)
    mask = re.search(r'^mask::([r-][w-][x-])', text, re.M)
    if mask:
        text = text[:mask.start(1)] + mask.group(1)[:2] + 'x' + text[mask.end(1):]
    else:
        group = re.search(r'^group::([r-][w-][x-])', text, re.M).group(1)
        text += 'mask::' + group[:2] + 'x\n'
    updated = ctypes.c_void_p(acl.acl_from_text((text + f'user:{uid}:--x\n').encode()))
    if not updated or acl.acl_set_file(path.encode(), 0x8000, updated):
        raise SystemExit('Cannot install traverse-only ancestor ACL.')
    acl.acl_free(updated)
PYACL
install -d -o "$user" -g "$user" -m 700 "$target"
line="restrict,command=\"/usr/bin/rrsync -wo $target\" $public_key"
if [[ -e "$home/.ssh/authorized_keys" ]]; then
    [[ $(cat "$home/.ssh/authorized_keys") == "$line" ]] || {
        printf 'Existing key differs; refusing replacement.\n' >&2; exit 1;
    }
else
    printf '%s\n' "$line" > "$home/.ssh/authorized_keys"
fi
chown root:"$user" "$home/.ssh/authorized_keys"
chmod 640 "$home/.ssh/authorized_keys"
printf 'Dedicated account, restricted key and backup directory ready.\n'
