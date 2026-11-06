# Changelog

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
