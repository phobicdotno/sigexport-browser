# Changelog

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
