#!/usr/bin/env python3
"""Install (or uninstall) the re-sync triggers on macOS. Idempotent: run it again any time.

  install_sync.py [--out ~/SignalExport] [--remote myremote:Signal-archive] [--port 8765]
                  [--python /opt/homebrew/bin/python3.13] [--lang nb|en] [--rotate-token]
  install_sync.py --uninstall [--out ...]

Install does:
  1. OUT/viewer/sync_config.js  {port, token} (random token, kept across runs unless --rotate-token;
     local only: gitignored and listed in tools/upload_excludes.txt). Copies index.html/viewer.js/
     viewer.css from this repo into OUT/viewer so the Sync button exists.
  2. ~/Library/LaunchAgents/no.phobic.sigexport-browser.helper.plist  (tools/sync_helper.py, KeepAlive)
  3. ~/Library/LaunchAgents/no.phobic.sigexport-browser.sync.plist    (03:00 daily, tools/sync_scheduled.sh
     -> sync.py --upload --quit-signal, skipped on battery < 30 %)
     Both are (re)loaded with launchctl bootout/bootstrap gui/$UID.
  4. ~/Applications/Signal Sync.app (osacompile) with a generic icon.
Uninstall removes exactly those four things. Neither ever touches the archive itself.
"""
import argparse
import os
import plistlib
import re
import secrets
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LABEL_HELPER = "no.phobic.sigexport-browser.helper"
LABEL_SYNC = "no.phobic.sigexport-browser.sync"
AGENTS = Path.home() / "Library" / "LaunchAgents"
APP = Path.home() / "Applications" / "Signal Sync.app"
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

MSG = {
    "en": {"start": "Sync started", "direct": "Sync started (helper not running, ran directly)",
           "busy": "A sync is already running", "done": "+%s messages, +%s media", "fail": "Sync failed",
           "title": "Signal archive"},
    "nb": {"start": "Synkronisering startet", "direct": "Synkronisering startet (hjelperen kjørte ikke)",
           "busy": "En synkronisering kjører allerede", "done": "+%s meldinger, +%s media",
           "fail": "Synkronisering feilet", "title": "Signal-arkiv"},
}


def sh(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def uid():
    return os.getuid()


def bootout(label):
    sh(["launchctl", "bootout", "gui/%d/%s" % (uid(), label)])


def bootstrap(plist):
    r = sh(["launchctl", "bootstrap", "gui/%d" % uid(), str(plist)])
    if r.returncode != 0:
        sys.exit("launchctl bootstrap failed for %s: %s" % (plist.name, r.stderr.strip()))


def token_for(cfg, rotate):
    if cfg.exists() and not rotate:
        m = re.search(r"token\s*:\s*['\"]([A-Za-z0-9_\-]+)['\"]", cfg.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return secrets.token_urlsafe(32)


def detect_lang(out):
    try:
        m = re.search(r"lang\s*:\s*['\"](\w+)['\"]", (out / "viewer" / "config.js").read_text(encoding="utf-8"))
        return m.group(1) if m and m.group(1) in MSG else "en"
    except OSError:
        return "en"


# ---- icon: generic circular-arrows glyph on a rounded square, drawn in pure Python (no Signal logo)
def _png(w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def make_icon_png(path, n=512):
    import math
    c, R, ring, rad = n / 2, n * 0.40, n * 0.055, n * 0.225  # canvas centre, squircle half-size, stroke, ring radius
    def inside_tri(px, py, a, b, d):
        def s(p, q, r):
            return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
        d1, d2, d3 = s((px, py), a, b), s((px, py), b, d), s((px, py), d, a)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
    tris = []
    for start in (math.radians(20), math.radians(200)):  # arrowheads at the end of each arc
        ang = start
        tip = (c + rad * math.cos(ang), c - rad * math.sin(ang))
        tx, ty = -math.sin(ang), -math.cos(ang)  # clockwise tangent (screen coords)
        nx, ny = math.cos(ang), -math.sin(ang)
        hl, hw = n * 0.11, n * 0.09
        front = (tip[0] + tx * hl * 0.6, tip[1] + ty * hl * 0.6)
        back = (tip[0] - tx * hl * 0.4, tip[1] - ty * hl * 0.4)
        tris.append((front, (back[0] + nx * hw, back[1] + ny * hw), (back[0] - nx * hw, back[1] - ny * hw)))
    rows = []
    for y in range(n):
        row = bytearray()
        for x in range(n):
            dx, dy = abs(x + .5 - c), abs(y + .5 - c)
            # superellipse (squircle) mask
            q = (dx / R) ** 5 + (dy / R) ** 5
            if q > 1.02:
                row += b"\x00\x00\x00\x00"
                continue
            a = 255 if q < 0.98 else int(255 * (1.02 - q) / 0.04)
            t = (y / n)
            r, g, b = int(20 + 10 * t), int(184 - 70 * t), int(166 - 40 * t)  # teal gradient
            px, py = x + .5 - c, c - (y + .5)
            d = math.hypot(px, py)
            ang = math.degrees(math.atan2(py, px)) % 360
            on_arc = abs(d - rad) < ring / 2 and not (20 < ang < 60 or 200 < ang < 240)
            if on_arc or any(inside_tri(x + .5, y + .5, *tr) for tr in tris):
                r = g = b = 255
            row += bytes((r, g, b, a))
        rows.append(row)
    path.write_bytes(_png(n, n, rows))


def install_icon(app, work):
    png = work / "icon_512.png"
    make_icon_png(png)
    iconset = work / "applet.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()
    for s in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = min(s * scale, 512)
            sh(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / ("icon_%dx%d%s.png" % (s, s, "@2x" if scale == 2 else ""))) ])
    r = sh(["iconutil", "-c", "icns", str(iconset), "-o", str(app / "Contents" / "Resources" / "applet.icns")])
    shutil.rmtree(iconset, ignore_errors=True)
    png.unlink(missing_ok=True)
    # newer osacompile also writes Assets.car + CFBundleIconName, which win over applet.icns
    (app / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)
    sh(["/usr/libexec/PlistBuddy", "-c", "Delete :CFBundleIconName", str(app / "Contents" / "Info.plist")])
    sh(["codesign", "--force", "--sign", "-", str(app)])  # re-seal ad hoc after editing the bundle
    os.utime(app)
    return r.returncode == 0


def applescript(a, token, lang):
    m = MSG[lang]
    q = lambda s: '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    qs = lambda s: "'" + str(s).replace("'", "'\\''") + "'"  # shell single-quote
    helper = REPO / "tools" / "sync_helper.py"
    sync_py = REPO / "pipeline" / "sync.py"
    direct = "mkdir -p %s && nohup %s %s --out %s%s >> %s 2>&1 < /dev/null &" % (
        qs(a.out / "_state"), qs(a.python), qs(sync_py), qs(a.out), " --upload" if a.remote else "",
        qs(a.out / "_state" / "sync_launch.log"))
    if a.remote:
        direct = "export REMOTE=%s PATH=%s PRUNE_SIGNAL_DAYS=%d; %s" % (
            qs(a.remote), qs(PATH_ENV), max(0, a.prune_signal_days), direct)
    post = "curl -s -m 10 -o /dev/null -w '%%{http_code}' -X POST -H %s http://127.0.0.1:%d/sync || true" % (
        qs("X-Sync-Token: " + token), a.port)
    status = "%s %s --print-status --out %s --since " % (qs(a.python), qs(helper), qs(a.out))
    return """-- Generated by tools/install_sync.py; re-run the installer instead of editing.
on run
	set t0 to do shell script "date +%%s"
	set code to do shell script %(post)s
	if code is "202" then
		display notification %(start)s with title %(title)s
	else if code is "409" then
		display notification %(busy)s with title %(title)s
	else
		do shell script %(direct)s
		display notification %(direct_msg)s with title %(title)s
	end if
	repeat 2880 times -- poll up to 8 h
		delay 10
		set statusLine to do shell script %(status)s & (t0 - 30)
		set AppleScript's text item delimiters to tab
		set f to text items of statusLine
		set AppleScript's text item delimiters to ""
		set syncState to item 1 of f
		if syncState is "done" then
			display notification (%(done_fmt)s) with title %(title)s
			exit repeat
		else if syncState is "failed" then
			display notification (item 5 of f) with title %(fail)s
			exit repeat
		end if
	end repeat
end run
""" % {"post": q(post), "start": q(m["start"]), "busy": q(m["busy"]), "title": q(m["title"]),
       "direct": q(direct), "direct_msg": q(m["direct"]), "status": q(status), "fail": q(m["fail"]),
       "done_fmt": '"+" & (item 2 of f) & " " & %s & ", +" & (item 3 of f) & " " & %s' % (
           q(m["done"].split(", ")[0].split(" ", 1)[1]), q(m["done"].split(", ")[1].split(" ", 1)[1]))}


def install(a):
    out = a.out
    viewer = out / "viewer"
    state = out / "_state"
    if not viewer.is_dir():
        sys.exit("no viewer folder at %s - build the viewer first" % viewer)
    state.mkdir(parents=True, exist_ok=True)
    lang = a.lang or detect_lang(out)

    # 1. token + viewer files
    cfg = viewer / "sync_config.js"
    token = token_for(cfg, a.rotate_token)
    cfg.write_text("// LOCAL ONLY - never commit or upload. Written by tools/install_sync.py.\n"
                   "window.SYNC_CONFIG = {port: %d, token: '%s'};\n" % (a.port, token), encoding="utf-8")
    os.chmod(cfg, 0o600)
    for f in ("index.html", "viewer.js", "viewer.css"):
        shutil.copy2(REPO / "viewer" / f, viewer / f)

    # 2. helper LaunchAgent
    AGENTS.mkdir(parents=True, exist_ok=True)
    env = {"PATH": PATH_ENV}
    if a.remote:
        env["REMOTE"] = a.remote
        if a.prune_signal_days > 0:  # prune only ever runs after --upload + rclone check passed
            env["PRUNE_SIGNAL_DAYS"] = str(a.prune_signal_days)
    hp = AGENTS / (LABEL_HELPER + ".plist")
    args = [a.python, str(REPO / "tools" / "sync_helper.py"), "--out", str(out), "--port", str(a.port),
            "--python", a.python, "--sync", str(REPO / "pipeline" / "sync.py")] + (["--upload"] if a.remote else [])
    hp.write_bytes(plistlib.dumps({
        "Label": LABEL_HELPER, "ProgramArguments": args, "RunAtLoad": True, "KeepAlive": True,
        "ProcessType": "Background", "EnvironmentVariables": env,
        "StandardOutPath": str(state / "sync_helper.log"), "StandardErrorPath": str(state / "sync_helper.log")}))
    # 3. daily 03:00 LaunchAgent (missed runs fire once on wake, see man launchd.plist)
    sp = AGENTS / (LABEL_SYNC + ".plist")
    senv = dict(env, OUT=str(out), PY=a.python, SYNC_PY=str(REPO / "pipeline" / "sync.py"),
                SYNC_ARGS="--upload --quit-signal" if a.remote else "--quit-signal", MIN_BATTERY=str(a.min_battery))
    sp.write_bytes(plistlib.dumps({
        "Label": LABEL_SYNC, "ProgramArguments": ["/bin/bash", str(REPO / "tools" / "sync_scheduled.sh")],
        "StartCalendarInterval": {"Hour": 3, "Minute": 0}, "ProcessType": "Background",
        "EnvironmentVariables": senv,
        "StandardOutPath": str(state / "sync_scheduled.log"), "StandardErrorPath": str(state / "sync_scheduled.log")}))
    for label, p in ((LABEL_HELPER, hp), (LABEL_SYNC, sp)):
        bootout(label)
        bootstrap(p)

    # 4. Dock app
    src = state / "signal_sync.applescript"
    src.write_text(applescript(a, token, lang), encoding="utf-8")
    os.chmod(src, 0o600)
    APP.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(APP, ignore_errors=True)
    r = sh(["osacompile", "-o", str(APP), str(src)])
    if r.returncode != 0:
        sys.exit("osacompile failed: " + r.stderr)
    icon_ok = install_icon(APP, state)

    print("Installed:")
    print("  token          %s (mode 600, local only)" % cfg)
    print("  helper         %s -> http://127.0.0.1:%d" % (hp, a.port))
    print("  daily 03:00    %s (%s)" % (sp, senv["SYNC_ARGS"]))
    print("  Dock app       %s%s" % (APP, "" if icon_ok else " (default icon)"))
    print("Next steps:")
    print("  - Reload the viewer (%s/index.html): the Sync button sits next to the title." % viewer)
    print("  - Dock: open ~/Applications in Finder (Cmd+Shift+H, then Applications) and drag")
    print("    'Signal Sync' onto the Dock. First run: allow notifications if macOS asks.")
    print("  - Check: launchctl print gui/%d/%s | grep -A3 'calendar'" % (uid(), LABEL_SYNC))
    print("  - Logs: %s/sync*.log" % state)
    print("  - Remove: %s --uninstall" % Path(__file__).resolve())


def uninstall(a):
    for label in (LABEL_HELPER, LABEL_SYNC):
        bootout(label)
        (AGENTS / (label + ".plist")).unlink(missing_ok=True)
    shutil.rmtree(APP, ignore_errors=True)
    (a.out / "viewer" / "sync_config.js").unlink(missing_ok=True)
    (a.out / "_state" / "signal_sync.applescript").unlink(missing_ok=True)
    print("Removed both LaunchAgents, %s and the token. The archive was not touched." % APP)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.environ.get("OUT_DIR", "~/SignalExport"))
    ap.add_argument("--remote", default=os.environ.get("REMOTE", ""), help="rclone remote; empty = no --upload")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--python", default="/opt/homebrew/bin/python3.13")
    ap.add_argument("--lang", choices=sorted(MSG), help="notification language (default: from viewer/config.js)")
    ap.add_argument("--min-battery", type=int, default=30)
    ap.add_argument("--prune-signal-days", type=int, default=30,
                    help="sync.py PRUNE_SIGNAL_DAYS for the helper, app and 03:00 job (0 = off; needs --remote)")
    ap.add_argument("--rotate-token", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    if sys.platform != "darwin":
        sys.exit("install_sync.py is macOS only (launchd + osacompile)")
    a.out = Path(a.out).expanduser().resolve()
    uninstall(a) if a.uninstall else install(a)


if __name__ == "__main__":
    main()
