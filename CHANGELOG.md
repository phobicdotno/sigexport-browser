# Changelog

## v0.1.41 (build 20) — 2026-11-09
- Sync link check fix: linkcheck() now returns the set of broken (data.json, ref) targets and apply fails only on refs the merge newly broke (set taken before apply_chat); chats with pre-existing sigexport-missing attachments no longer fail and roll back; test for a chat with a pre-existing broken ref merging fine

## v0.1.40 (build 19) — 2026-11-09
- Opt-in Signal Desktop prune (`sync.py --prune-signal-days N` / `PRUNE_SIGNAL_DAYS`, `--prune-dry-run`): after a successful sync + `rclone check --one-way` only, moves old message attachment files to `_work/pruned/<date>/` (deleted after the summary) when each one decrypts with its localKey (sha256 = plaintextHash), its md5 is in media_index.tsv with a matching archive file, and the remote has it with the same md5 (`pipeline/prune_signal.py`); files shared with newer/other rows are kept; `pruned_files` / `pruned_bytes` in sync_status.json, counts only in sync.log; installer + sync_scheduled.sh default 30 days; README safety rules; synthetic encrypted-fixture tests; CI installs pycryptodome

## v0.1.39 (build 18) — 2026-11-09
- Upload excludes: tools/upload_excludes.txt is the single source of truth (sync.py --upload reads it and refuses to upload if viewer/sync_config.js, the helper token, is not listed; also covers _work/**, build temp dirs, *.pid, sync/upload state, log and lock files); test that the token file is excluded from rclone copy and check; viewer/build_stamp.js gitignored

## v0.1.38 (build 17) — 2026-11-09
- sync.py: chat matching fixed (dry run had 32 existing chats as "new"): conversation id via `_state/conv_map.tsv` first, then the real subfolder names inside NNN_*/ (multi-subfolder folders like 076 handled); ambiguous matches and phone-only folders (NNN_phone_*) are skipped and logged, never created or overwritten; only a cid known nowhere gets a new NNN (recorded in conv_map.tsv + folders.tsv)
- sync_export.py: builds/completes `_state/conv_map.tsv` (cid, NNN folder, subfolder) with the completeness_check.py rules (id_map.tsv, phone folders, fix_names folder name, exact date multiset, >=90% date overlap); `conv-map SRC OUT` mode; test for cid/rename, two-subfolder, phone-only and ambiguous folders

## v0.1.36 (build 16) — 2026-11-09
- tests/test_sync_helper.py: stub runs 5 s so the busy check cannot race a fast finish (Windows py3.10 CI)

## v0.1.35 (build 15) — 2026-11-09
- tools/sync_helper.py: 127.0.0.1-only helper (default port 8765) with POST /sync (starts pipeline/sync.py detached, 409 when busy via own child, flock on _state/sync.lock or exit 75) and GET /status (_state/sync_status.json); X-Sync-Token required, Host check, Origin only null/own, CORS + private-network preflight
- Viewer: Sync button next to the title, shown only when local viewer/sync_config.js exists; polls /status every 2 s with step and progress bar, "+N messages, +M media" when done, clean "helper not running" / bad-token / failed messages, en + nb strings, resumes a sync started elsewhere; neutral theme tokens only
- tools/install_sync.py / install_sync.sh: idempotent install + --uninstall of the token (sync_config.js, mode 600, kept across runs), helper LaunchAgent no.phobic.sigexport-browser.helper (KeepAlive), daily LaunchAgent no.phobic.sigexport-browser.sync (StartCalendarInterval 03:00) and ~/Applications/Signal Sync.app (osacompile, generic drawn icon, ad hoc re-signed)
- Signal Sync.app: asks the helper, falls back to running sync.py directly, notifications at start and finish (polls the status file)
- tools/sync_scheduled.sh: 03:00 wrapper running sync.py --upload --quit-signal, skips and logs when on battery below 30 % (pmset), logs to _state/sync_scheduled.log; viewer/sync_config.js gitignored and listed in tools/upload_excludes.txt
- README "Sync" section (button, Dock app, 03:00 job with the man launchd.plist wake rule, merge-only / never removes anything, uninstall); tests/test_sync_helper.py against tests/fixtures/sync_stub.py

## v0.1.29 (build 14) — 2026-11-09
- pipeline/sync.py: merge-only re-sync (`--out OUT [--upload] [--dry-run] [--quit-signal]`): full sigexport into OUT/_work/sync_tmp, chats matched to NNN by conversation id (id_map.tsv) then folder name (folders.tsv), new chats get the next NNN; only messages not already present (date+sender, same body within 60 s, or server timestamp) are inserted into data.json / chat.md, existing lines (phone-backup included) are never changed, missing old attachments are ignored; new media filed via central_media; hard-link backup, append-only + link check and restore per chat; viewer rebuild; optional rclone copy + check --one-way (REMOTE, copy only)
- sync.py contract: lock OUT/_state/sync.lock (PID, busy -> exit 75), atomic OUT/_state/sync_status.json after every step, OUT/_state/sync.log (counts and NNN only), disk guard (MIN_FREE_GIB / ABORT_FREE_GIB)
- pipeline/sync_export.py: runs sigexport in-process with the DB key from the login Keychain item `sigexport-browser` (never on a command line, in a file or printed); `sync.py --setup-key` stores it once (interactive "Always Allow" for Signal Safe Storage)
- prepare_source.py: `quit_signal=False` live copy of sql/ while Signal runs (db then WAL, no -shm, retried until db.sqlite and the WAL salts are unchanged; nothing written to the Signal folder); pipeline/tests/test_sync.py (append-only, phone lines kept, missing attachments, next NNN, dry run, lock/exit 75, status file, match rules)

## v0.1.25 (build 13) — 2026-11-06
- README: Requirements rewritten for the cross-platform Python pipeline (macOS/Linux/Windows with CI on all three, real runs on macOS 26 only; Python 3.10+, sigexport 3.9.x via pipx, Signal Desktop linked, optional rclone, Keychain / gnome-libsecret or kwallet / DPAPI key-store notes, MIN_FREE_GIB 8 / ABORT_FREE_GIB 3 disk guidance, any browser); Quick start and per-OS sections use export_all.py / prepare_source.py (work dir ~/SignalExport/_work/src), stale defaults (6/4), duplicate Windows section and the "macOS only" limitation fixed

## v0.1.24 (build 12) — 2026-11-06
- README: new "Phone backup (Android)" section (researched 2026-11-06): backup v2 format and recovery key, legacy `.backup` files deleted on upgrade (copy them off first), SignalBackups layout, copying with adb/MTP and checking the file count, decrypting with libsignal / SignalTool / signalbackup-tools, risks, sources
- README: merging phone-only history into the archive (dedup, central `Media/`) marked as planned, not built

## v0.1.22 (build 11) — 2026-11-06
- dedup.py/central_media.py: markdown link regexes accept CRLF line endings (Windows chat.md), fixes the Windows CI failures

## v0.1.21 (build 10) — 2026-11-06
- Central media layout (new default, `--media-layout central` / `MEDIA_LAYOUT`): pipeline/central_media.py files each unique file once in OUT_DIR/Media/<Type>/ (ext/MIME, stickers -> Pictures, `_<md5[:8]>` on name clash), rewrites chat.md/index.html/data.json to ../../Media/<Type>/<file>, link-checks, removes media/; `per-chat` keeps the old layout
- Crash-safe per file: hard-link backup + journal (_state/central_journal) before anything moves, move -> index -> rewrite -> delete copies, idempotent resume on the next run, rollback from backup on failure; state compatible with a migrated export (done.txt NNN, central media_index.tsv)
- Order check (port of verify_chats.py): existing NNN_ folders (also _dupe_chats/_incomplete) must match the regenerated chats.txt, else abort with exit 65
- Disk guard: per-chat size estimate from chat_bytes.tsv / hashes.tsv (sigexport and the DB expose no sizes), else MIN_FREE_GIB 8; chats that won't fit are skipped into retry.tsv; free space polled every 1.5 s during sigexport, terminate/kill below ABORT_FREE_GIB 3 and mark for retry; state stays under OUT_DIR
- Tests: central filing new/known md5, name-clash suffix, rewrite + link check + rollback, resume after a crash mid-chat, disk guard (mocked disk_usage), order check; viewer fixture tests/fixtures/export_central; README Media layout section and measured stats (33 chats, 17.9 GB raw, 11.55 GB duplicates removed, 6.0 GB after)

## v0.1.16 (build 9) — 2026-11-06
- pipeline/export_all.py: cross-platform (macOS, Linux, Windows) stdlib Python port of export_all.sh with the same env vars/flags, resume, manifest, dedup, link check, rclone copy + one-way check, disk guards and per-chat timeouts; keep-awake via caffeinate / systemd-inhibit / SetThreadExecutionState; Signal folder auto-detected per OS (incl. Flatpak and Snap)
- pipeline/prepare_source.py: cross-platform port; also copies "Local State" (needed for Windows DPAPI key), falls back to directory junctions on Windows; the .sh files are now thin wrappers
- Duplicate-name fix: chats.txt names are unique, folders.tsv records which folders each export produced, collisions.tsv reports names matching several chats, the same chat exported twice, comma names and Windows-illegal folder names
- dedup.py: links always written with forward slashes and Windows ./media\ links accepted and normalised; new unittest suite pipeline/tests/test_pipeline.py (synthetic data)

## v0.1.12 (build 8) — 2026-11-06
- CI actions pinned to checkout v7.0.1 and setup-python v7.1.0 (Node 24; clears the Node 20 deprecation warning)

## v0.1.11 (build 7) — 2026-11-06
- Auto-refresh: the build writes build_stamp.js last; an open viewer tab polls it every 30 s and reloads itself after a rebuild, keeping the open chat, scroll position (or bottom), media tab and search text; it waits while the lightbox, fullscreen or a text field is in use

## v0.1.10 (build 6) — 2026-11-06
- Single photos and videos in a bubble now take the media's real aspect ratio (80px to 60vh tall, cropped rather than padded), so wide screenshots no longer sit on top of a grey filler box

## v0.1.9 (build 5) — 2026-11-06
- build_viewer.py is portable: pathlib, utf-8 with LF output, media URLs always '/'-separated and percent-encoded (also for '\\' paths from sigexport), file:// URI fallback across Windows drives, long-path support on Windows
- build_viewer.py writes data.new/ and search.new/ and swaps them in (works on Windows, where a dir cannot be renamed over another), index_data.js via os.replace; viewer-folder check works on case-insensitive filesystems
- CI: GitHub Actions on ubuntu, windows and macos with Python 3.10 and 3.13. Runs the unittest suite and builds the viewer against a synthetic fixture (tests/fixtures/export)
- README: install and usage for macOS, Linux (incl. Flatpak/Snap Signal paths) and Windows (PowerShell, pipx, rclone), how to open the viewer per OS, CI badge

## v0.1.5 (build 4) — 2026-11-05
- README screenshots resized into a consistent grid (fixed sizes, click for full resolution)

## v0.1.4 (build 3) — 2026-11-05
- Home reset: clicking the app title or avatar (or Esc with nothing open) closes searches, media tab, lightbox and popovers and returns every chat to its newest messages
- Link check ignores attachments that sigexport reported missing at export time; new manifest column missing_reported
- Screenshots refreshed with new synthetic test names, plus a reactions screenshot

## v0.1.1 (build 2) — 2026-11-05
- Reaction hover/tap popover showing who reacted with which emoji

## v0.1.0 (build 1) — 2026-11-05
- Offline chat browser (viewer/): chat list, message bubbles, reactions, quotes, call-history lines
- Media gallery: square grid with zoom slider, ctrl/cmd+wheel and pinch zoom, fullscreen lightbox
- Global search with filters and jump-to-message; in-chat search
- English and Norwegian (nb) UI, theme overrides via themes/theme-local.css, works from file:// without network
- build_viewer.py: builds index and search data from sigexport output (--root/--out)
- pipeline/export_all.sh: resumable per-chat export, md5 media dedup with link rewriting, link check, optional rclone copy + one-way check, stickers exported once, Keychain-block guard, disk-space guards
- pipeline/prepare_source.sh: read-only working copy of the Signal Desktop folder
- pipeline/dedup.py: dedup / linkcheck / indexonly modes
- tools/measure_duplicates.py: estimate dedup savings (counts and sizes only)
