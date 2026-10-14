#!/bin/bash
# Disable inherited tracing before handling encoded preferences.
set +x
set -euo pipefail
umask 077

[[ $(uname -s) == Darwin ]] || { printf 'Requires macOS.\n' >&2; exit 1; }
[[ -d /Applications/VoiceInk.app ]] || { printf 'Run build.sh first.\n' >&2; exit 1; }
verify=apply
cleanup=on
for option in "$@"; do
    case "$option" in
        --verify) verify=--verify ;;
        --cleanup-off) cleanup=off ;;
        *) printf 'Usage: ./settings.sh [--cleanup-off] [--verify]\n' >&2; exit 1 ;;
    esac
done
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
if [[ "$verify" == apply ]] && pgrep -x VoiceInk >/dev/null; then
    osascript -e 'tell application id "com.prakashjoshipax.VoiceInk" to quit'
    for (( attempt=0; attempt<30; attempt++ )); do
        pgrep -x VoiceInk >/dev/null || break
        sleep 1
    done
    if pgrep -x VoiceInk >/dev/null; then
        printf 'VoiceInk did not quit; close it and retry.\n' >&2
        exit 1
    fi
fi

# Source: upstream c09cc1f, ModeConfig/ModeRuntimeConfiguration, Shortcut/ShortcutStore,
# RecordingShortcutManager, AppDefaults, TranscriptionModelManager, UpdaterViewModel.
# Read only the keys below; encoded values stay in memory and never reach the console.
/usr/bin/swift - "$verify" "$cleanup" "$script_dir/cleanup-prompt.txt" <<'SWIFT'
import Foundation
import CoreFoundation
import AppKit
import Carbon.HIToolbox

let domain = "com.prakashjoshipax.VoiceInk"
let verifyOnly = CommandLine.arguments[1] == "--verify"
let cleanupEnabled = CommandLine.arguments[2] == "on"
func fail(_ message: String) -> Never {
    fputs(message + "\n", stderr)
    exit(1)
}
func read(_ domain: String, _ key: String) -> Any? {
    CFPreferencesAppSynchronize(domain as CFString)
    return CFPreferencesCopyAppValue(key as CFString, domain as CFString)
}
func json(_ object: Any) -> Data {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
        fail("Cannot encode settings; nothing further written.")
    }
    return data
}
func normalized(_ data: Data) -> Data? {
    guard let value = try? JSONSerialization.jsonObject(with: data) else { return nil }
    return try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
}
enum Value {
    case bool(Bool), string(String), int(Int), data(Data), absent
    var arguments: [String] {
        switch self {
        case .bool(let value): return ["-bool", value ? "true" : "false"]
        case .string(let value): return ["-string", value]
        case .int(let value): return ["-int", String(value)]
        case .data(let data): return ["-data", data.map { String(format: "%02x", $0) }.joined()]
        case .absent: return []
        }
    }
    func matches(_ current: Any?) -> Bool {
        switch self {
        case .bool(let value):
            guard let number = current as? NSNumber,
                  CFGetTypeID(number) == CFBooleanGetTypeID() else { return false }
            return number.boolValue == value
        case .string(let value): return current as? String == value
        case .int(let value):
            guard let number = current as? NSNumber,
                  CFGetTypeID(number) != CFBooleanGetTypeID() else { return false }
            return number.intValue == value
        case .data(let value):
            guard let data = current as? Data else { return false }
            return normalized(data) == normalized(value)
        case .absent: return current == nil
        }
    }
}
struct Setting {
    let domain: String
    let key: String
    let value: Value
    var matches: Bool { value.matches(read(domain, key)) }
}
func command(_ arguments: [String]) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/defaults")
    process.arguments = arguments
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do { try process.run() } catch { fail("Could not run defaults; no private values logged.") }
    process.waitUntilExit()
    guard process.terminationStatus == 0 else { fail("defaults failed; stop and restore the backup if needed.") }
}

// Custom provider credentials belong to VoiceInk's UI/Keychain, never this script.
// Validate the preference metadata only; leave every provider entry untouched.
let providerName = "Local cleanup"
if cleanupEnabled {
    guard let data = read(domain, "customAIProviders") as? Data,
          let providers = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]],
          providers.filter({ $0["name"] as? String == providerName }).count == 1,
          let provider = providers.first(where: { $0["name"] as? String == providerName }) else {
        fail("Add Local cleanup in VoiceInk first; see README's one-time provider step.")
    }
    guard let id = provider["id"] as? String, UUID(uuidString: id) != nil,
          provider["baseURL"] as? String == "http://127.0.0.1:8095/v1/chat/completions",
          provider["selectedModel"] as? String == "qwen4",
          provider["models"] as? [String] == ["qwen4"],
          providers.filter({ ($0["models"] as? [String])?.contains("qwen4") == true
              || $0["selectedModel"] as? String == "qwen4" }).count == 1 else {
        fail("Correct Local cleanup in VoiceInk; see README's one-time provider step.")
    }
    if verifyOnly { print("Verified: Local cleanup") }
}
var prompts = [[String: Any]]()
let promptTitle = "Local cleanup"
var promptID = ""
if cleanupEnabled {
    if let current = read(domain, "customPrompts") {
        guard let data = current as? Data,
              let decoded = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] else {
            fail("Cannot decode customPrompts; no settings written.")
        }
        prompts = decoded
    }
    let matches = prompts.indices.filter { prompts[$0]["title"] as? String == promptTitle }
    guard matches.count <= 1 else { fail("Duplicate Local cleanup prompts; no settings written.") }
    if let index = matches.first {
        guard let id = prompts[index]["id"] as? String, UUID(uuidString: id) != nil else {
            fail("Unexpected customPrompts format; no settings written.")
        }
        promptID = id
    } else { promptID = UUID().uuidString }
    guard let text = try? String(contentsOfFile: CommandLine.arguments[3], encoding: .utf8) else {
        fail("Cannot read generic cleanup prompt; no settings written.")
    }
    let prompt: [String: Any] = ["id": promptID, "title": promptTitle,
        "promptText": text.trimmingCharacters(in: .newlines), "useSystemInstructions": false]
    if let index = matches.first { prompts[index] = prompt } else { prompts.append(prompt) }
}

// Preserve all other mode fields without printing their contents or replacing the modes.
// ModeConfig stores a JSON-encoded [ModeConfig] as UserDefaults Data.
guard let data = read(domain, "modeConfigurationsV2") as? Data,
      var modes = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] else {
    fail("No readable modes. Create a default mode in VoiceInk, quit it, and rerun settings.sh.")
}
let defaults = modes.indices.filter { modes[$0]["isDefault"] as? Bool == true }
guard defaults.count == 1, let defaultIndex = defaults.first,
      let defaultID = modes[defaultIndex]["id"] as? String,
      UUID(uuidString: defaultID) != nil else {
    fail("Select exactly one default mode in VoiceInk, quit it, and rerun settings.sh.")
}
for index in modes.indices {
    guard modes[index]["id"] is String, modes[index]["name"] is String,
          modes[index]["isAIEnhancementEnabled"] is Bool else {
        fail("Unexpected mode format; no settings written. Review in VoiceInk.")
    }
    modes[index]["isAIEnhancementEnabled"] = false
    modes[index]["useScreenCapture"] = false
}
if cleanupEnabled {
    modes[defaultIndex]["isAIEnhancementEnabled"] = true
    modes[defaultIndex]["selectedAIProvider"] = "Custom"
    modes[defaultIndex]["selectedAIModel"] = "qwen4"
    modes[defaultIndex]["selectedPrompt"] = promptID
    modes[defaultIndex]["useClipboardContext"] = false
    modes[defaultIndex]["useSelectedTextContext"] = false
}
modes[defaultIndex]["isEnabled"] = true
modes[defaultIndex]["selectedTranscriptionModelName"] = "ggml-large-v3-turbo"
modes[defaultIndex]["selectedLanguage"] = "auto"

let fnShortcut: [String: Any] = [
    "kind": "modifierOnly",
    "keyCode": Int(kVK_Function),
    "modifierFlagsRawValue": NSEvent.ModifierFlags.function.rawValue
]
var settings = [Setting]()
func add(_ key: String, _ value: Value) { settings.append(Setting(domain: domain, key: key, value: value)) }
if cleanupEnabled {
    add("customPrompts", .data(json(prompts)))
    add("AutoLearnDictionaryProvider", .string("Custom"))
    add("AutoLearnDictionaryModel", .string("qwen4"))
}
add("IsAutoLearnDictionaryEnabled", .bool(cleanupEnabled))
add("modeConfigurationsV2", .data(json(modes)))
add("activeConfigurationId", .string(defaultID))
add("CurrentTranscriptionModel", .string("ggml-large-v3-turbo"))
add("SelectedLanguage", .string("auto"))
add("primaryRecordingShortcut", .string("custom"))
add("primaryRecordingShortcutMode", .string("hybrid"))
add("Shortcut_primaryRecording", .data(json(fnShortcut)))
add("Shortcut_primaryRecording_cleared", .absent)
add("secondaryRecordingShortcut", .string("none"))
add("Shortcut_secondaryRecording", .absent)
add("Shortcut_secondaryRecording_cleared", .bool(true))
for key in ["IsAudioCleanupEnabled", "IsTranscriptionCleanupEnabled",
            "enableAnnouncements", "SUEnableAutomaticChecks", "VoiceInkChecksForUpdatesOnLaunch"] {
    add(key, .bool(false))
}
// macOS 27 KeyboardSettings' Fn picker tags "Do Nothing" as UInt32 zero.
settings.append(Setting(domain: "com.apple.HIToolbox", key: "AppleFnUsageType", value: .int(0)))
let changes = settings.filter { !$0.matches }
if verifyOnly {
    for setting in settings {
        print("\(setting.matches ? "Verified" : "Mismatch"): \(setting.key)")
    }
    guard changes.isEmpty else { fail("Settings verification failed; rerun settings.sh after onboarding.") }
} else if changes.isEmpty {
    print("No change: all scripted settings already match.")
} else {
    let manager = FileManager.default
    let backupDirectory = manager.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/mac-dictation", isDirectory: true)
    do {
        try manager.createDirectory(at: backupDirectory, withIntermediateDirectories: true,
                                    attributes: [.posixPermissions: 0o700])
        try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: backupDirectory.path)
    } catch { fail("Cannot create private settings backup directory; no settings written.") }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyyMMdd-HHmmss"
    let backup = backupDirectory.appendingPathComponent("VoiceInk-\(formatter.string(from: Date()))-\(UUID().uuidString).plist")
    command(["export", domain, backup.path])
    do { try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: backup.path) }
    catch { fail("Cannot secure settings backup; no settings written.") }
    print("Restore (quit VoiceInk first): defaults import \(domain) \"$HOME/Library/Application Support/mac-dictation/\(backup.lastPathComponent)\"")
    fflush(stdout)
    for setting in changes {
        if case .absent = setting.value {
            command(["delete", setting.domain, setting.key])
        } else {
            command(["write", setting.domain, setting.key] + setting.value.arguments)
        }
        print("Set: \(setting.key)")
    }
    guard settings.allSatisfy({ $0.matches }) else { fail("Readback failed; restore the settings backup before retrying.") }
    print("Readback verified: all scripted settings match.")
}
SWIFT

if [[ "$verify" == apply ]]; then
    open /Applications/VoiceInk.app
    printf 'Manual step: enable Launch at Login in VoiceInk Settings > General.\n'
    printf 'After granting Microphone or Accessibility (including after rebuilds), quit and reopen VoiceInk so Fn works.\n'
fi
