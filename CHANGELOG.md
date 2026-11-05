# Changelog

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
