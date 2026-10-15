# Repository governance

This repository builds and configures unmodified upstream VoiceInk. It holds code only.
Never put recordings, transcripts, vocabulary, replacements, the owner's own prompts,
exported settings, models, or anything read from the owner's VoiceInk data in this repository, even in tests
or examples. The generic cleanup prompt in cleanup-prompt.txt is allowed.
Scripts write settings and never print private settings.
Read VoiceInk's SwiftData stores only from a snapshot copy; never write to the originals.
Never touch VoiceInk's Recordings, WhisperModels, or the Keychain.
Do not include account names, owner home-directory paths, hostnames, or addresses; use `$HOME`.

Work arrives as self-contained task prompts. Run shellcheck on every script and
`bash check-no-data.sh` before every commit.
Proceed without asking except before an irreversible step or a real security or privacy
risk, such as deleting owner data, publishing personal information, or loosening main's
protection: stop and ask in those cases.
