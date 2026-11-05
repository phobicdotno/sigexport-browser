"""Cross-platform helpers for the export pipeline (stdlib only, Python 3.10+).

Signal Desktop data directory per OS (verified against signal-export 3.9.3
sigexport/utils.py:source_location(), which only knows the first entry of each list):
  macOS   ~/Library/Application Support/Signal
  Linux   ~/.config/Signal ($XDG_CONFIG_HOME/Signal), Flatpak
          ~/.var/app/org.signal.Signal/config/Signal, Snap ~/snap/signal-desktop/current/.config/Signal
  Windows %APPDATA%\\Signal  (sigexport uses ~/AppData/Roaming/Signal)

How sigexport 3.9.3 gets the SQLCipher key (sigexport/crypto.py:get_key):
  - config.json has plaintext "key": used as-is on every OS (old Signal versions).
  - config.json has "encryptedKey" (Electron safeStorage):
      macOS:   `security find-generic-password -ws "Signal Safe Storage"` (Keychain prompt),
               PBKDF2-SHA1(saltysalt, 1003 iters) + AES-128-CBC, prefix v10.
      Linux:   config.json "safeStorageBackend": gnome_libsecret ->
               `secret-tool lookup application Signal`; kwallet/kwallet5/kwallet6 ->
               `kwallet-query kdewallet -f "Chromium Keys" -r "Chromium Safe Storage"`;
               PBKDF2 1 iter, prefix v11. Other backends (e.g. basic_text) or none: it prints
               help and needs --password.
      Windows: reads "Local State" os_crypt.encrypted_key (in the Signal dir), strips the
               "DPAPI" prefix, CryptUnprotectData via ctypes, then AES-256-GCM.
The DB is opened with sqlcipher3 dbapi2.connect() (read-write, not mode=ro), attachments are
read from <source>/attachments.noindex and stickers from <source>/stickers.noindex.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
WIN_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def signal_dir_candidates(platform: str | None = None, env: dict | None = None,
                          home: Path | None = None) -> list[Path]:
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform == "darwin":
        return [home / "Library" / "Application Support" / "Signal"]
    if platform == "win32":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return [base / "Signal"]
    xdg = env.get("XDG_CONFIG_HOME")
    cands = [Path(xdg) / "Signal"] if xdg else []
    cands += [home / ".config" / "Signal",
              home / ".var" / "app" / "org.signal.Signal" / "config" / "Signal",
              home / "snap" / "signal-desktop" / "current" / ".config" / "Signal"]
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def find_signal_dir(platform: str | None = None, env: dict | None = None,
                    home: Path | None = None) -> Path | None:
    for c in signal_dir_candidates(platform, env, home):
        if (c / "config.json").is_file():
            return c
    return None


def safe_component(name: str, maxlen: int = 40) -> str:
    """ASCII [A-Za-z0-9_] folder component, legal on Windows/macOS/Linux (same rule as the
    old perl s/[^A-Za-z0-9]+/_/g, plus reserved-name and empty-name handling)."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", name)[:maxlen]
    if not s.strip("_"):
        s = s or "_"
    if s.upper().split(".")[0] in WIN_RESERVED:
        s = "_" + s
    return s


def windows_problem(name: str) -> str | None:
    """Why `name` would be an illegal Windows path component, or None."""
    if WIN_ILLEGAL.search(name):
        return "illegal character"
    if name != name.rstrip(" ."):
        return "trailing dot/space"
    if name.upper().split(".")[0] in WIN_RESERVED:
        return "reserved name"
    return None


def free_gib(path: Path) -> float:
    return shutil.disk_usage(path).free / (1 << 30)


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


class KeepAwake:
    """Prevent idle sleep while the pipeline runs. No-op when unsupported."""

    def __init__(self):
        self.proc = None
        self.win = False

    def __enter__(self):
        try:
            if sys.platform == "darwin" and shutil.which("caffeinate"):
                self.proc = subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())])
            elif sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
                self.proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", "--who=sigexport-browser",
                     "--why=Signal export running", "--mode=block", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform == "win32":
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                self.win = True
        except Exception:
            self.proc = None
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                pass
        if self.win:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
        return False
