#!/bin/bash
set -euo pipefail

die() { printf '%s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Darwin ]] || die 'Requires macOS 27 or later.'
os_version=$(sw_vers -productVersion)
(( ${os_version%%.*} >= 27 )) || die 'Requires macOS 27 or later.'
xcode_version=$(xcodebuild -version | awk '/^Xcode / {print $2}')
[[ -n "$xcode_version" ]] || die 'Requires full Xcode 27.0 or later.'
(( ${xcode_version%%.*} >= 27 )) || die 'Requires Xcode 27.0 or later.'
xcodebuild -checkFirstLaunchStatus >/dev/null 2>&1 || die 'Complete Xcode setup: sudo xcodebuild -runFirstLaunch'
metal_component=$(xcodebuild -showComponent MetalToolchain 2>/dev/null) || die 'Install Metal Toolchain: xcodebuild -downloadComponent MetalToolchain'
printf '%s\n' "$metal_component" | /usr/bin/grep -Eq '^Status: installed[[:space:]]*$' || die 'Install Metal Toolchain: xcodebuild -downloadComponent MetalToolchain'
(( $# <= 1 )) || die 'Usage: ./build.sh [upstream-commit]'
commit=${1:-c09cc1f}
[[ "$commit" =~ ^[[:xdigit:]]{7,40}$ ]] || die 'Supply a commit hash (7–40 hexadecimal characters).'

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
clone_dir="$repo_dir/.upstream/VoiceInk"
upstream=https://github.com/Beingpax/VoiceInk.git
if [[ ! -e "$clone_dir" ]]; then
    git clone "$upstream" "$clone_dir"
fi
[[ $(git -C "$clone_dir" remote get-url origin) == "$upstream" ]] || die 'Unexpected clone origin; refusing to build.'
[[ -z $(git -C "$clone_dir" status --porcelain) ]] || die 'Upstream checkout has changes; refusing to build modified source.'
git -C "$clone_dir" fetch origin
resolved=$(git -C "$clone_dir" rev-parse --verify "$commit^{commit}")
git -C "$clone_dir" checkout --detach "$resolved"

# Quit before make local too: upstream replaces its Downloads copy during the build.
if pgrep -x VoiceInk >/dev/null; then
    osascript -e 'tell application id "com.prakashjoshipax.VoiceInk" to quit'
    for (( attempt=0; attempt<30; attempt++ )); do
        pgrep -x VoiceInk >/dev/null || break
        sleep 1
    done
    ! pgrep -x VoiceInk >/dev/null || die 'VoiceInk did not quit; close it and retry.'
fi

started=$SECONDS
# Reuse upstream's existing cache at $HOME/VoiceInk-Dependencies. No source changes or pins.
make -C "$clone_dir" local
built_app="$clone_dir/.local-build/Build/Products/Release/VoiceInk.app"
[[ -d "$built_app" ]] || die 'Upstream build produced no app.'
version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$built_app/Contents/Info.plist")
codesign --verify --deep --strict "$built_app"

if pgrep -x VoiceInk >/dev/null; then
    die 'VoiceInk was launched during the build; quit it and retry.'
fi
[[ ! -L /Applications/VoiceInk.app ]] || die 'Installation target is a symlink; refusing to replace it.'
rm -rf /Applications/VoiceInk.app
ditto "$built_app" /Applications/VoiceInk.app
codesign --verify --deep --strict /Applications/VoiceInk.app
installed_version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' /Applications/VoiceInk.app/Contents/Info.plist)
[[ "$installed_version" == "$version" ]] || die 'Installed version does not match the build.'
rm -rf "$HOME/Downloads/VoiceInk.app"
printf 'Installed commit: %s\nVersion: %s\nBuild and installation time: %s seconds\n' "$resolved" "$installed_version" "$(( SECONDS - started ))"
codesign -dv /Applications/VoiceInk.app 2>&1
