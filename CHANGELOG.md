# Changelog

## v0.1.55 (build 30) — 2026-11-10
- Real chat names in the viewer: sigexport's `fix_names` names each chat folder `''.join(alnum of emoji.demojize(name))`, so the chat list, sender names and search index showed e.g. `JaneredheartDoe` (emoji turned into words, spaces dropped). `build_viewer.py` now takes the real Signal name from `_state/chats.txt` (line N = chat NNN) and `_state/folders.tsv`, strips Signal's bidi isolates, and uses it when its folder key matches the chat folder (or it is the only chat in `NNN_<safe name>`); senders and reactors with that folder name get it too; folders and media paths are unchanged. `sync_export.py` writes the real name as a 3rd `_cids.tsv` column and `sync.py` stores it in `folders.tsv` for new chats. Test with made-up names.

## v0.1.54 (build 30) — 2026-11-10
- Archive host deployment: `tools/install_sync.py --archive-url URL [--archive-token-file F]` gives the helper, the 03:00 job and the Dock app's direct fallback ARCHIVE_URL / ARCHIVE_TOKEN_FILE and `--upload` (the host merges and uploads; `--remote` not needed; prune still gated on the host's clean merge + Jotta check); new `deploy/signal-archive-api.service` (archive_server.py as signalarchive on 127.0.0.1:8766, tokens in /etc/signal-archive mode 600, RCLONE_CONFIG there, ProtectSystem=strict with only /srv/signal-archive writable); tests for the installer env/AppleScript and the unit

## v0.1.53 (build 29) — 2026-11-10
- Archive on another machine (behind `--archive-url` / `ARCHIVE_URL`, off by default): new `tools/archive_server.py` (stdlib, Bearer token file, optional viewer token limited to /status, /log, /sync-request) with GET /state, /media_index, /status, /log and POST /ingest (strictly validated tar: only `<chat>/{data.json,chat.md,index.html,media/…}` + the four sync .tsv files, no absolute/`..`/links/devices/sync_config, size + count limits; runs the existing merge, viewer rebuild and watermarks on the host), /upload (host's Jotta copy + check of the owed files), /media_md5 (refused unless the run merged and uploaded clean), /status, /sync-request; `sync.py` archive mode (DB snapshot, precheck against the host's watermarks cached in `_work/`, export, tar to /ingest, /upload, prune only after a clean host merge AND Jotta check, verified against the host's media_index, archive md5s and Jotta md5s via `prune_signal.py --archive-url`); status mirrored to the host; sync_helper polls ARCHIVE_URL/sync-request; viewer sync_config.js may set `base` and `syncPath` for the hosted Sync button; sync.py upload code split into `upload_list` / `upload_owed` / `record_owed` shared by both modes; tests for round trip, malicious tars, remote precheck, prune gating and the sync-request flow

## v0.1.52 (build 29) — 2026-11-09
- Fix the viewer stuck on "new version – press F5": sync rebuilt the viewer with the repo's build_viewer.py, which stamped the repo VERSION while the deployed `OUT/viewer/viewer.js` kept an older CODE_VERSION (sync never copied viewer code), so F5 reloaded the same old file forever. Each sync (not dry runs) now calls `install_sync.copy_viewer_files(OUT/viewer)` when the repo VERSION differs from `OUT/viewer/VERSION` (never config.js, sync_config.js or themes) and rebuilds with the deployed `OUT/viewer/build_viewer.py`; the stamp reads `OUT/viewer/VERSION`, so stamp and CODE_VERSION cannot disagree; the viewer asks for F5 only when the stamp is NEWER than its CODE_VERSION (never on equal/older, so no loop).

## v0.1.51 (build 29) — 2026-11-09
- Precheck: the deferred chats' pending list keeps only the messages the merge actually deferred (matched by timestamp; it held every recent message with a missing attachment, 96 and 80 for 1 deferred each), and the attachment checks use the message_attachments primary key instead of a JOIN that scanned the table (no-change precheck 11.6 s -> 0.4 s).

## v0.1.50 (build 28) — 2026-11-09
- No-change sync in seconds: a new `precheck` step (`sync_export.py precheck`, no sigexport parsing) runs one aggregate query (count, max received_at, max rowid per conversation) against `_state/sync_watermarks.tsv`; with nothing new it ends `done` / "no changes" and skips export, merge, viewer, upload and prune (unless an earlier upload still owes files). Deferred chats are no longer re-exported every run: the watermark row now keeps the pending message ids (`msgid@sent_at`), and the chat is re-exported only once a pending attachment's file exists in `attachments.noindex`, the message is deleted, or it is past DEFER_DAYS + 1 day (then merged with `missing_kind`); old rows without the list are re-exported once. Changed chats are filtered in fetch_data's SQL (`WHERE conversationId IN (...)` via a cursor proxy), so other chats are never read or JSON-parsed (one small chat: 2.7-3.6 s -> 1.0-1.4 s export). Tests: no-change run skips export, deferred chat waits for its attachment, pending list carried over, SQL filter.

## v0.1.49 (build 27) — 2026-11-09
- Fix incremental upload failing every run (rc=1, prune skipped): rclone rejects `--exclude` together with `--files-from` ("the usage of --files-from overrides all other filters"), so `rclone copy/check --files-from` no longer gets the exclude args; the list is already built from the exclude-filtered diff and `upload_files` now refuses (fail closed, no rclone call) if any listed path matches tools/upload_excludes.txt; tests assert no filter flags beside `--files-from` and run real rclone copy + check against a local dir remote (CI installs rclone on Linux)

## v0.1.48 (build 26) — 2026-11-09
- Fast sync: incremental export (per-conversation DB fingerprint count:max(received_at):max(rowid) read from the same in-process cursor, stored in `_state/sync_watermarks.tsv`; only changed, new and deferred conversations go through sigexport via the id-based fetch_data filter, failed chats lose their watermark so they are retried; full export with `--full`, without watermarks, or weekly — edits/reactions are only picked up there and merge-only never rewrites existing lines anyway); incremental upload (stat diff of the archive before/after the run, viewer outputs by md5, minus tools/upload_excludes.txt; `rclone copy/check --one-way --files-from --no-traverse`; a failed upload's list is carried to the next run); whole-archive `rclone check` only with `--full-check` (03:00 job, installer default); `step=X secs=Y` per step in sync.log; viewer rebuild stays full (~40 s, global chat numbering); prune fetches remote md5s only for the candidates' archive files and compares paths NFC-normalised (macOS NFD vs Jottacloud NFC, æøå names were never pruned); tests

## v0.1.47 (build 25) — 2026-11-09
- Sync: a new message whose attachment sigexport lists but Signal Desktop has not downloaded yet (file absent from the export) no longer rolls back the whole chat with "link check: 1 new broken refs" (root cause of NNN 186/070 failing); messages younger than 30 days are deferred to a later sync (logged as deferred=N, `deferred_messages` in sync_status.json), older ones are added with the attachment flagged `missing_kind: absent_at_sync`; failed-chat log lines now name the check and counts (MergeCheck: …) and, for OS errors, only type + errno, never paths or chat names; tests

## v0.1.46 (build 24) — 2026-11-09
- Viewer version label: tiny muted "v0.1.46" (10px, theme --sub colour) centred under the avatar, tooltip "v0.1.46 (build 24) · data 09.11 20:05"; build_viewer.py writes `__VIEWER_VERSION` (repo VERSION or viewer/VERSION, else "dev") and `__BUILD_TIME` into build_stamp.js; tools/install_sync.py `copy_viewer_files()` sets `CODE_VERSION` in the copied viewer.js, writes viewer/VERSION and also copies build_viewer.py; when the polled stamp's version differs from the running viewer.js the label turns accent "new version – press F5" / "ny versjon – trykk F5" (click reloads)

## v0.1.45 (build 23) — 2026-11-09
- Viewer single-file Download saves straight to disk on file://: helper POST /file-ticket?chat=N&ref=R (token, ref must be an ok attachment listed in viewer/data/N.js, resolved inside OUT/Media or the chat's folder) returns a 60 s single-use ticket, GET /file?ticket=T streams the file with Content-Disposition attachment; filename*=UTF-8'' original name, Content-Type, Content-Length; zip and file tickets are not interchangeable; viewer falls back to new tab + ⌘S hint when the helper is down or slow (1.5 s), plain <a download> over http(s); tests for ticket, traversal, unlisted ref, header and expiry; headless Chrome check on synthetic fixture: file downloads, no new tab

## v0.1.44 (build 22) — 2026-11-09
- Viewer sync bar forgiving when the helper is briefly unreachable (restart/KeepAlive relaunch): retries /status with backoff (1 s doubling to 8 s) showing "Connecting to the sync helper…" / "Kobler til synk-hjelperen…", clears by itself when it answers (a click made while it was down is replayed), and only after 60 s unreachable shows the plain hint "Synk-hjelperen svarer ikke. Åpne «Signal Sync»-appen eller kjør install_sync.sh på nytt."

## v0.1.43 (build 21) — 2026-11-09
- Viewer download: Download button (icon, tooltip, key D, en/nb) in the lightbox top bar and on file cards; saves the original file under its attachment name (else the stored name) via <a href download target=_blank>; on file:// Chrome ignores download= (opaque origins, tested headless) so the file opens in a new tab with a one-time "⌘S / right-click → Save" hint, never navigating the viewer away
- Media tab "Download all": with the helper, POST /zip-ticket (token) returns a 60 s single-use ticket and GET /media.zip?chat=N&ticket=T streams a zip (ZIP_STORED, original names, deduped) of that chat's photos/videos, only files listed in viewer/data/N.js and resolved inside OUT/Media or the chat's folder; without the helper over http(s): sequential downloads with a "Download N files?" confirm above 20; on file:// without helper a hint to install it

## v0.1.42 (build 21) — 2026-11-09
- Sync log in the viewer: helper GET /log?lines=N (token + Origin checks, max 500) returns the current/last run of _state/sync.log from the last step=disk line; /status adds failed_chats (parsed from the log) when the engine does not write it; status bar "Show log"/"Vis logg" opens a monospace panel (copy button, refresh every 2 s while running, failed lines red, rc=0/state=done green); friendlier failure text "N chats failed – nothing was lost, they were rolled back"

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
