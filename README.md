# sigexport-browser

[![CI](https://github.com/phobicdotno/sigexport-browser/actions/workflows/ci.yml/badge.svg)](https://github.com/phobicdotno/sigexport-browser/actions/workflows/ci.yml)

Offline browser and deduplicated export pipeline for Signal Desktop chats (built on sigexport).

Everyone has experienced the insanely big Signal Desktop folder, but nobody wants to nuke their chat history. Now you can — without losing anything.

sigexport-browser exports every chat, removes duplicate media (a video forwarded to ten chats is stored once and linked from the other nine), verifies every file by hash, and gives you a fast offline chat browser with search and a media gallery.

<p align="center"><a href="docs/screenshots/wide_chat.png"><img src="docs/screenshots/wide_chat.png" width="640" alt="Chat view: chat list on the left, message thread with bubbles and reactions on the right"></a></p>

## Real-world result (one user, anonymized)

Measured from the Signal database before the full export finished:

| | |
|---|---|
| Signal Desktop attachments folder | 35 GiB, 32,106 files (incl. ~3 GB thumbnails/screenshots) |
| Attachment references | 19,003 |
| Unique content | ~12.9 GB (9,924 files) |
| Without dedup an export would be | ~49 GB |
| With dedup | ~13 GB |
| Saved | ~36 GB, mostly videos forwarded to many chats (1,442 duplicate groups spanning several chats) |
| Chats kept | all (~205) |

Final measured numbers will be added when the export completes. Run `tools/measure_duplicates.py` to get your own estimate.

## What you keep / what changes

You keep:
- every message
- every attachment Signal still had on disk
- reactions, quotes and call history

What changes:
- Once you clear Signal's attachments, old media no longer shows inside Signal Desktop. You only see it in the browser.
- The export is a snapshot. Re-run it to pick up new messages.
- Attachments Signal had already lost stay lost. This tool can't recover them.

## Safe order

1. Export (`pipeline/export_all.sh`).
2. Verify: the link check must pass, and if you upload a backup, `rclone check --one-way` must report 0 differences (the pipeline runs both for you).
3. Only then clear Signal's attachments.

## Features

**Viewer** (`viewer/`)
- chat list, message bubbles, reactions, quotes, call-history lines
- hover (desktop) or tap (touch) a reaction to see who reacted with which emoji
- Home reset: click the app title or the round avatar at the top left, or press Esc when nothing is open, to close searches, the media tab and the lightbox and jump every chat back to its newest messages
- square media grid with zoom slider, ctrl/cmd + mouse wheel and pinch zoom
- fullscreen lightbox
- global search with filters and jump-to-message
- English and Norwegian (nb) UI, themes via CSS variables
- works from `file://` with no server, and makes no network requests

**Pipeline** (`pipeline/`)
- resumable per-chat export with sigexport (state in `OUT_DIR/_state`)
- md5 media dedup across chats: the first copy wins, and the `chat.md`, `index.html` and `data.json` links of later copies are rewritten to point at it
- link check after dedup; a chat that fails the check is re-exported without dedup. Attachments that sigexport itself reported missing at export time (`missing_kind` set in `data.json`) are left out of the link check, so they don't block dedup, and are counted in the manifest column `missing_reported`
- optional `rclone copy` + `rclone check --one-way` per chat; local exports are never deleted
- stickers are exported once
- disk-space guards (pause below `MIN_FREE_GIB`, abort the current chat below `ABORT_FREE_GIB`)
- Keychain-block guard: it stops cleanly if sigexport hangs on the Keychain prompt
- manifest CSV with per-chat media counts, bytes saved, upload and check status

<table>
<tr>
<td align="center"><a href="docs/screenshots/wide_media.png"><img src="docs/screenshots/wide_media.png" width="400" alt="Media gallery: square thumbnail grid with zoom slider"></a></td>
<td align="center"><a href="docs/screenshots/reactions.png"><img src="docs/screenshots/reactions.png" width="400" alt="Reaction popover showing who reacted with which emoji"></a></td>
</tr>
<tr>
<td align="center"><a href="docs/screenshots/global_desktop.png"><img src="docs/screenshots/global_desktop.png" width="400" alt="Global search across all chats with results list"></a></td>
<td align="center"><a href="docs/screenshots/global_filters.png"><img src="docs/screenshots/global_filters.png" width="400" alt="Global search with the filter options open"></a></td>
</tr>
<tr>
<td align="center"><a href="docs/screenshots/lightbox.png"><img src="docs/screenshots/lightbox.png" width="280" alt="Fullscreen lightbox on phone with previous/next arrows"></a></td>
<td align="center"><a href="docs/screenshots/list.png"><img src="docs/screenshots/list.png" width="280" alt="Chat list on phone"></a></td>
</tr>
</table>

All screenshots use synthetic test data.

## Requirements

- macOS (tested on macOS 26). The pipeline uses macOS paths and `osascript`. The viewer runs in any modern browser.
- Signal Desktop
- [signal-export](https://github.com/carderne/signal-export) (`sigexport`) ≥ 3.9.3 on Python ≥ 3.10, e.g.
  `pipx install --python python3.13 signal-export==3.9.3`
  (older versions have an attachment-decryption crash)
- rclone (optional, only for upload)
- On macOS 26, Homebrew needs the Command Line Tools: `xcode-select --install`
- On the first run, macOS asks for your Keychain password for "Signal Safe Storage". Choose **Allow**.

## Quick start

```sh
git clone https://github.com/phobicdotno/sigexport-browser.git
cd sigexport-browser

# 1. read-only working copy of the Signal data (quits Signal first)
pipeline/prepare_source.sh                       # -> ~/SignalExport/_src

# 2. optional: estimate the savings
~/.local/pipx/venvs/signal-export/bin/python tools/measure_duplicates.py ~/SignalExport/_src

# 3. export everything (resumable: just run it again after an interruption)
SIGNAL_SRC=~/SignalExport/_src OUT_DIR=~/SignalExport pipeline/export_all.sh
#   optional upload + verification:
#   REMOTE=myremote:Signal-export SIGNAL_SRC=... pipeline/export_all.sh

# 4. build and open the browser
cp -R viewer ~/SignalExport/viewer
python3 ~/SignalExport/viewer/build_viewer.py      # root = parent folder by default
open ~/SignalExport/viewer/index.html
```

Settings: `SIGNAL_SRC`, `OUT_DIR` (default `~/SignalExport`), `REMOTE` (empty means no upload), `MIN_FREE_GIB` (6), `ABORT_FREE_GIB` (4), `TRANSFERS` (2), `SIGEXPORT`, `STATE_DIR`. Each one can also be passed as a flag; see `pipeline/export_all.sh --help`.

To personalise the viewer, copy `viewer/config.example.js` to `viewer/config.js`. See `viewer/themes/README.md` for themes.

## Install and usage per OS

The viewer build (`viewer/build_viewer.py`) is plain Python 3.10+ with no dependencies and is tested in CI on Linux, Windows and macOS. The shell pipeline above is macOS-first; on Linux and Windows run `sigexport` directly (or the pipeline once it supports your OS) and then build the viewer.

Signal Desktop data folder (pass it to `sigexport --source`):

| OS | Signal Desktop folder |
|---|---|
| macOS | `~/Library/Application Support/Signal` |
| Linux (deb/rpm/AUR) | `~/.config/Signal` |
| Linux (Flatpak) | `~/.var/app/org.signal.Signal/config/Signal` |
| Linux (Snap) | `~/snap/signal-desktop/current/.config/Signal` |
| Windows | `%APPDATA%\Signal` (`$env:APPDATA\Signal` in PowerShell) |

Quit Signal Desktop before exporting, and work on a copy of that folder if you can.

### macOS

```sh
brew install pipx rclone          # rclone only if you upload
pipx install --python python3.13 signal-export==3.9.3
# export with the pipeline (Quick start above), then:
python3 ~/SignalExport/viewer/build_viewer.py
open ~/SignalExport/viewer/index.html
```

### Linux

```sh
sudo apt install pipx rclone      # or dnf / pacman; rclone is optional
pipx install signal-export==3.9.3
SRC=~/.config/Signal              # Flatpak / Snap: see the table above
sigexport --source "$SRC" ~/SignalExport/001_all
cp -R viewer ~/SignalExport/viewer
python3 ~/SignalExport/viewer/build_viewer.py
xdg-open ~/SignalExport/viewer/index.html
```

On Linux, `sigexport` reads the database key from the desktop keyring (GNOME Keyring / KWallet). Unlock it first.

### Windows (PowerShell)

```powershell
winget install Python.Python.3.13 Rclone.Rclone   # rclone is optional
py -m pip install --user pipx; py -m pipx ensurepath   # then open a new terminal
pipx install signal-export==3.9.3
sigexport --source "$env:APPDATA\Signal" "$HOME\SignalExport\001_all"
Copy-Item -Recurse viewer "$HOME\SignalExport\viewer"
py "$HOME\SignalExport\viewer\build_viewer.py"
Start-Process "$HOME\SignalExport\viewer\index.html"
```

Keep the export folder short (for example `C:\SignalExport`). Windows paths over 260 characters break in some tools. The build handles them, but rclone and Explorer may not. Optional upload: `rclone copy "$HOME\SignalExport" myremote:Signal-export`.

### Opening the viewer

The viewer is a static page that loads from `file://` without a server or network:

- macOS: `open viewer/index.html`, or double-click it in Finder
- Linux: `xdg-open viewer/index.html`
- Windows: `Start-Process viewer\index.html`, or double-click it in Explorer

Every media path the build writes is relative, `/`-separated and percent-encoded. You can move or copy the whole export folder (with `viewer/` inside it) to another machine or OS and it still works. Rerun `build_viewer.py` after each new export. It swaps the new `data/` and `search/` folders in at the end, so an open viewer never sees half a build.

## How dedup works

After each chat is exported, `pipeline/dedup.py` hashes every file in its `media/` folder with md5. A global index (`media_index.tsv`) maps hash to the first file seen. When a later file has the same hash, and the first copy still exists and still matches, the links in that chat's `chat.md`, `index.html` and `data.json` are rewritten to a relative path such as `../../012_Other/Other/media/video.mp4`. Each file is rewritten atomically, and the duplicate is deleted only after that. A link check then resolves every media link. If any link is broken, the chat is re-exported without dedup.

## Privacy

- Everything stays on your machine. The pipeline only reads the Signal folder and writes to `OUT_DIR`.
- The viewer is static HTML/JS. It makes no network requests and loads no CDNs.
- Nothing is uploaded unless you set `REMOTE`.
- Logs and the manifest contain chat names, so keep `OUT_DIR/_state` private. The tools in this repo never print message text.

## Limitations

- macOS only for the pipeline (Signal paths, `df`, `osascript`).
- The export is a snapshot. New messages need a new run.
- Dedup works on exact bytes. A re-encoded copy of the same video is not detected.
- Large files are hashed in full, so the first run is I/O bound.
- Media that Signal no longer has on disk shows as unresolved in the viewer.

## Disclaimer

Not affiliated with or endorsed by Signal Messenger LLC or the signal-export project.
The export itself is done by [carderne/signal-export](https://github.com/carderne/signal-export). Many thanks to its authors.

## License

MIT, see [LICENSE](LICENSE).
