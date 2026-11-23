"""
Nexus Grid - a local, all-in-one launcher for Steam, Epic, GOG, Ubisoft and any manual .exe

Run:      python game_launcher.py   (PySide6 + psutil are installed automatically on first run)
Tip:      rename to game_launcher.pyw to launch without a console window.

Everything lives in Documents/NexusGrid (Config, Data, Apps, Games, Logs + a README).
"""
import base64
import ctypes
import difflib
import hashlib
import json
import datetime
import io
import math
import shutil
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
import zipfile
import zlib
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, asdict, fields
from pathlib import Path

# --------------------------------------------------------------------------- #
# No console window: if started with python.exe, restart under pythonw.exe
# --------------------------------------------------------------------------- #
def _hide_console():
    if os.name != "nt" or os.environ.get("GAME_LAUNCHER_NOCONSOLE"):
        return
    exe = Path(sys.executable)
    if exe.name.lower() != "python.exe":
        return  # already pythonw / a frozen exe
    pyw = exe.with_name("pythonw.exe")
    if not pyw.exists():
        return
    try:
        env = dict(os.environ, GAME_LAUNCHER_NOCONSOLE="1")
        # DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([str(pyw), os.path.abspath(__file__), *sys.argv[1:]], env=env,
                         creationflags=0x00000008 | 0x08000000 | 0x00000200, close_fds=True)
    except Exception:
        return  # fall back to running normally with the console
    sys.exit(0)


_hide_console()


# --------------------------------------------------------------------------- #
# Auto-install requirements (first run only)
# --------------------------------------------------------------------------- #
def _bootstrap():
    import importlib
    import importlib.util

    needed = {"PySide6": "PySide6", "psutil": "psutil"}
    missing = [pkg for mod, pkg in needed.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return

    has_console = sys.stdout is not None
    out = None if has_console else subprocess.DEVNULL
    flags = 0x08000000 if (os.name == "nt" and not has_console) else 0  # CREATE_NO_WINDOW
    if has_console:
        print(f"First run: installing {', '.join(missing)} (this can take a minute)...")

    def run(cmd):
        subprocess.check_call(cmd, stdout=out, stderr=out, creationflags=flags)

    try:
        if importlib.util.find_spec("pip") is None:
            run([sys.executable, "-m", "ensurepip", "--upgrade"])
        base = [sys.executable, "-m", "pip", "install", *missing]
        try:
            run(base)
        except subprocess.CalledProcessError:
            run(base + ["--user"])
    except Exception as e:
        msg = (f"Couldn't install {', '.join(missing)} automatically ({e}).\n"
               f"Install them manually with:  pip install {' '.join(missing)}")
        if has_console:
            print(msg)
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "Nexus Grid", 0x10)
        sys.exit(1)
    importlib.invalidate_caches()


_bootstrap()

from PySide6.QtCore import (
    QAbstractListModel, QEasingCurve, QEvent, QFileInfo, QMimeData, QModelIndex, QObject, QPoint, QPointF,
    QPropertyAnimation, QRect, QRectF, QRunnable, QSettings, QSize, QSortFilterProxyModel, Qt, QThreadPool,
    QStandardPaths, QTimer, QUrl, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QContextMenuEvent, QDesktopServices, QFont, QFontMetrics, QIcon, QImage, QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFileIconProvider, QInputDialog, QTreeView,
    QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QAbstractButton, QButtonGroup, QGridLayout, QPlainTextEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QSlider, QStyle,
    QStyledItemDelegate, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget,
)

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

try:
    import psutil
except ImportError:
    psutil = None

# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _documents_dir():
    try:
        p = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        if p:
            return Path(p)
    except Exception:
        pass
    return Path.home() / "Documents"


# Documents/NexusGrid/
#   README.md  Config/settings.ini  Data/(library.json, folders.json, covers/, thumbs/)
#   Apps/YouTube/ (+ any app folder with a main.py)   Games/ (drop games here)   Logs/
NG_ROOT = _documents_dir() / "NexusGrid"
CONFIG_DIR = NG_ROOT / "Config"
CONFIG_FILE = CONFIG_DIR / "settings.ini"
DATA_DIR = NG_ROOT / "Data"
APPS_DIR = NG_ROOT / "Apps"
GAMES_DIR = NG_ROOT / "Games"
LOG_DIR = NG_ROOT / "Logs"
OLD_DIR = Path(os.getenv("APPDATA", Path.home())) / "GameLauncher"      # where older versions kept everything
COVERS = DATA_DIR / "covers"
THUMBS = DATA_DIR / "thumbs"          # small cached copies of covers (fast to load)
LIBRARY_FILE = DATA_DIR / "library.json"
FOLDERS_FILE = DATA_DIR / "folders.json"
for _d in (CONFIG_DIR, DATA_DIR, COVERS, THUMBS, APPS_DIR, GAMES_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

README_TEXT = """NEXUS GRID
==========

Everything Nexus Grid uses lives in this folder (Documents/NexusGrid):

  Config/settings.ini   your settings (the Settings screen edits this; safe to back up)
  Data/profile.json     your profile: name, bio, avatar colour, achievements
  Data/roadmap.json     the status you gave each Roadmap item
  Data/library.json     your game list, favourites, notes, playtime
  Data/folders.json     your folders (e.g. one per creator)
  Data/covers, thumbs   cover art and its fast-loading thumbnail cache (thumbs can be deleted any time)
  Apps/                 apps shown on Big Picture's "Apps" shelf (YouTube is included)
  Games/                drop game folders here; Scan offers to add them
  Logs/                 error.log, crash.log, qt.log - send these if something breaks

APPS
----
Apps/YouTube is the YouTube app (real youtube.com in Windows' Edge WebView2, with controller support).
  - X + B together on the controller closes it. Settings > YouTube has more options.
  - Copy Apps/YouTube/config.example.json to config.json to change its settings.
Any other folder in Apps/ that contains a main.py is shown as an extra tile on the Apps shelf
(started with Python; its output is written to app.log inside that folder).

GAMES
-----
  - Settings > Library > Saved game folders lists the folders Scan checks (Games/ is included).
  - "+ Add game" adds a single .exe, or imports a whole folder of games.
  - Folders: "+ New folder", then drag games onto it or right-click > Move to folder.

KEYBOARD
--------
  Enter open/play    Backspace back    Ctrl+F search    Ctrl+D favourite    F10 Big Picture    F11 fullscreen
  In Big Picture: arrows move, Enter plays, I details, F favourite, Tab side panel, Esc menu/back.

CONTROLLER (Xbox / XInput)
--------------------------
  Library: D-pad/left stick move, A open, B back, X favourite, Y sort, LB/RB filter, Start options, Select menu.
  Profile: your name and title, bio, G score, level, 28 achievements, activity feed, stats charts, badges and titles,
    a showcase of pinned games/achievements, weekly goals and a Year in Review (Big Picture: the avatar, or the side panel > Profile;
    desktop: the Profile button). Text entry in Big Picture uses an on-screen keyboard (A type, X delete, Y shift,
    Start done, B cancel). Search is the magnifier in Big Picture's icon bar (or press / on a keyboard).
  Big Picture: D-pad/stick move (Up reaches the icon bar, Down the shortcut tiles), A play/open, B back,
    Y details, X favourite, Start options, Select menu, LB/RB jump 5 tiles,
    click the RIGHT STICK to open the side panel (Friends, Messages, Games, Apps, Settings).
    In the panel's Settings, left/right changes a value (accent colour, animations, ...), A toggles.

ROADMAP
-------
Big Picture side panel > Roadmap (or Select > Roadmap, or Settings > Open the Roadmap) lists every idea for
what to build next, grouped by where it would live, with a status you control: Idea, Planned, In progress, Done.
Left/Right changes a status, X stars a priority, LB/RB filters. Anything still marked "SOON" in the app (side panel
entries, home shortcut tiles, extra profile tabs, game page chips, extra Apps tiles, extra settings) opens its
Roadmap entry when you press A on it.

MOVING TO A NEW PC
------------------
Copy the whole NexusGrid folder to the same place on the new PC (game paths are stored as full paths,
so games must be in the same locations; covers and settings come along).
"""


def write_docs():
    """Create the README and short notes in the folders (only if missing, so your edits stay)."""
    try:
        for path, text in (
            (NG_ROOT / "README.md", README_TEXT),
            (GAMES_DIR / "Put your games here.txt",
             "Drop game folders in here. Press Scan in Nexus Grid and it offers to add them.\n"),
            (APPS_DIR / "About apps.txt",
             "Each folder here with a main.py becomes a tile on Big Picture's Apps shelf.\n"
             "YouTube/ is the bundled YouTube app. X + B closes an app on the controller.\n"),
        ):
            if not path.exists():
                path.write_text(text, encoding="utf-8")
    except Exception:
        pass


def migrate_old_data():
    """One time: copy the library, covers and YouTube sign-in from the old %APPDATA%/GameLauncher folder
    (the old files are left in place as a backup)."""
    flag = DATA_DIR / ".migrated"
    if flag.exists():
        return
    try:
        if OLD_DIR.exists() and OLD_DIR.resolve() != DATA_DIR.resolve():
            old_lib = OLD_DIR / "library.json"
            if old_lib.exists() and not LIBRARY_FILE.exists():
                raw = json.loads(old_lib.read_text(encoding="utf-8"))
                old_cov = str(OLD_DIR / "covers")
                for g in raw:
                    c = g.get("cover") or ""
                    if c.startswith(old_cov):
                        g["cover"] = str(COVERS / Path(c).name)
                LIBRARY_FILE.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            if (OLD_DIR / "folders.json").exists() and not FOLDERS_FILE.exists():
                shutil.copy2(OLD_DIR / "folders.json", FOLDERS_FILE)
            if (OLD_DIR / "covers").exists():
                for f in (OLD_DIR / "covers").iterdir():
                    if f.is_file() and not (COVERS / f.name).exists():
                        shutil.copy2(f, COVERS / f.name)
            old_sign = OLD_DIR / "youtube_app" / ".webview_data"          # keep a YouTube sign-in
            new_sign = APPS_DIR / "YouTube" / ".webview_data"
            if old_sign.exists() and not new_sign.exists():
                new_sign.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(old_sign, new_sign, ignore_dangling_symlinks=True)
    except Exception:
        pass
    try:
        flag.write_text("done", encoding="utf-8")
    except Exception:
        pass


def make_settings():
    """Settings live in Config/settings.ini; the first time, copy over the old registry settings."""
    fresh = not CONFIG_FILE.exists()
    cfg = QSettings(str(CONFIG_FILE), QSettings.Format.IniFormat)
    if fresh:
        try:
            old = QSettings("GameLauncher", "GameLauncher")
            for k in old.allKeys():
                cfg.setValue(k, old.value(k))
            cfg.sync()
        except Exception:
            pass
    return cfg


migrate_old_data()
write_docs()

ACCENT = "#8b5cf6"
ACCENT_PRESETS = {"Violet": "#8b5cf6", "Indigo": "#6366f1", "Blue": "#3b82f6", "Magenta": "#d946ef",
                  "Teal": "#14b8a6", "Orange": "#f97316"}


def as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
GAME_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class Game:
    id: str
    name: str
    store: str
    launch: str                 # exe path or a URI like steam://rungameid/123
    install_dir: str = ""
    cover: str = ""
    custom_cover: bool = False
    favourite: bool = False
    last_played: float = 0.0
    launches: int = 0
    playtime_min: int = 0       # from Steam's local config
    tracked_sec: float = 0.0    # measured by this launcher (non-Steam games)
    art_tried: float = 0.0      # when we last looked online for cover art
    controller: str = ""        # full | partial | none | unknown ('' = not checked yet)
    controller_checked: float = 0.0
    controller_ok: bool = False  # user said: don't warn me about this game
    folder: str = ""             # id of the user folder this game lives in ('' = top level)
    notes: str = ""              # your notes (game page)
    status: str = ""             # Playing / Finished / Backlog / Dropped ('' = none)


@dataclass
class Folder:
    """A user-made folder (e.g. one per creator). Games point at it through Game.folder."""
    id: str
    name: str


def load_folders():
    try:
        raw = json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
        return [Folder(id=str(f["id"]), name=str(f["name"])) for f in raw]
    except Exception:
        return []


def save_folders(folders):
    tmp = FOLDERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(f) for f in folders], indent=2), encoding="utf-8")
    tmp.replace(FOLDERS_FILE)


# --- start with Windows -------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "NexusGrid"


def startup_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
        return True
    except OSError:
        return False


def set_startup(on, hidden=False):
    """Add/remove the Windows 'run at login' entry. Returns False if it couldn't be changed."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                exe = Path(sys.executable)
                pyw = exe.with_name("pythonw.exe")
                exe = pyw if pyw.exists() else exe
                cmd = f'"{exe}" "{os.path.abspath(__file__)}"' + (" --tray" if hidden else "")
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def load_library():
    if not LIBRARY_FILE.exists():
        return []
    try:
        raw = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        names = {f.name for f in fields(Game)}
        return [Game(**{k: v for k, v in item.items() if k in names}) for item in raw]
    except Exception:
        return []


def save_library(games):
    tmp = LIBRARY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(g) for g in games], indent=2), encoding="utf-8")
    tmp.replace(LIBRARY_FILE)


# --------------------------------------------------------------------------- #
# Store scanners
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text):
    """Minimal Valve KeyValues parser (enough for libraryfolders.vdf / appmanifest)."""
    stack, key = [{}], None
    for m in _TOKEN.finditer(text):
        s, brace = m.groups()
        if brace == "{":
            d = {}
            stack[-1][key] = d
            stack.append(d)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = s.replace("\\\\", "\\")
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return stack[0]


def _reg_value(hive, path, name):
    try:
        with winreg.OpenKey(hive, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def steam_root():
    if not winreg:
        return None
    for hive, key, val in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        v = _reg_value(hive, key, val)
        if v and Path(v).exists():
            return Path(v)
    return None


def _steam_cover(root, appid):
    cached = COVERS / f"steam_{appid}.jpg"
    if cached.exists():
        return str(cached)
    base = root / "appcache" / "librarycache"
    flat = base / f"{appid}_library_600x900.jpg"
    if flat.exists():
        return str(flat)
    folder = base / appid
    if folder.is_dir():
        for f in folder.rglob("library_600x900.jpg"):
            return str(f)
    return ""


def steam_playtimes(root):
    """Read per-game playtime (minutes) and last-played from Steam's localconfig.vdf."""
    files = sorted((root / "userdata").glob("*/config/localconfig.vdf"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return {}
    try:
        node = parse_vdf(files[0].read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    for key in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps"):
        nxt = {}
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() == key.lower():
                    nxt = v
                    break
        node = nxt
    out = {}
    for appid, v in (node.items() if isinstance(node, dict) else []):
        if not isinstance(v, dict):
            continue
        low = {k.lower(): val for k, val in v.items()}
        try:
            out[appid] = (int(low.get("playtime", 0)), float(low.get("lastplayed", 0)))
        except (TypeError, ValueError):
            continue
    return out


def scan_steam():
    root = steam_root()
    if not root:
        return []
    libs = {root}
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        data = parse_vdf(vdf.read_text(encoding="utf-8", errors="ignore"))
        for v in data.get("libraryfolders", {}).values():
            if isinstance(v, dict) and v.get("path"):
                libs.add(Path(v["path"]))
    games = []
    for lib in libs:
        for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
            try:
                st = parse_vdf(acf.read_text(encoding="utf-8", errors="ignore")).get("AppState", {})
                appid, name = st.get("appid"), st.get("name")
                if not appid or not name:
                    continue
                if appid == "228980" or name.startswith(("Steamworks Common", "Proton", "Steam Linux Runtime")):
                    continue
                games.append(Game(
                    id=f"steam:{appid}", name=name, store="Steam",
                    launch=f"steam://rungameid/{appid}",
                    install_dir=str(lib / "steamapps" / "common" / st.get("installdir", "")),
                    cover=_steam_cover(root, appid),
                ))
            except Exception:
                continue
    pt = steam_playtimes(root)
    for g in games:
        info = pt.get(g.id.split(":")[1])
        if info:
            g.playtime_min, g.last_played = info[0], max(g.last_played, info[1])
    return games


def scan_epic():
    manifests = Path(os.getenv("ProgramData", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    games = []
    if not manifests.is_dir():
        return games
    for item in manifests.glob("*.item"):
        try:
            d = json.loads(item.read_text(encoding="utf-8", errors="ignore"))
            cats = d.get("AppCategories") or []
            if cats and "games" not in cats:
                continue
            name, app = d.get("DisplayName"), d.get("AppName")
            if not name or not app:
                continue
            uri = (f"com.epicgames.launcher://apps/{d.get('CatalogNamespace', '')}"
                   f"%3A{d.get('CatalogItemId', '')}%3A{app}?action=launch&silent=true")
            games.append(Game(id=f"epic:{app}", name=name, store="Epic",
                              launch=uri, install_dir=d.get("InstallLocation", "")))
        except Exception:
            continue
    return games


def scan_gog():
    if not winreg:
        return []
    games = []
    base = r"SOFTWARE\WOW6432Node\GOG.com\Games"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return games
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            name = _reg_value(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "gameName")
            exe = _reg_value(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "exe")
            path = _reg_value(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "path")
            if name and exe:
                games.append(Game(id=f"gog:{sub}", name=name, store="GOG",
                                  launch=exe, install_dir=path or str(Path(exe).parent)))
    return games


def scan_ubisoft():
    if not winreg:
        return []
    games = []
    base = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return games
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            folder = _reg_value(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "InstallDir")
            if folder and Path(folder).exists():
                name = Path(folder.rstrip("/\\")).name
                games.append(Game(id=f"ubisoft:{sub}", name=name, store="Ubisoft",
                                  launch=f"uplay://launch/{sub}/0", install_dir=folder))
    return games


# --------------------------------------------------------------------------- #
# Folder import: find the real game .exe inside each game folder
# --------------------------------------------------------------------------- #
# .exe files that are never the game itself (installers, crash reporters, runtimes...)
SKIP_EXE = re.compile(
    r"(unins|uninstall|setup|install|crash|report|helper|redist|vc_?redist|dxsetup|dxwebsetup|"
    r"dotnet|webview|updater|update|notification|zsync|python|7z|ffmpeg|prereq|oalinst|"
    r"physx|easyanticheat|battleye|cefsharp|subprocess|diagnostic|config(?:ure|urator)?\b)",
    re.I,
)
# sub-folders that only hold engine/runtime files (Ren'Py's lib/, redistributables, etc.)
SKIP_DIRS = {
    "lib", "renpy", "redist", "_redist", "redistributables", "_commonredist", "vcredist",
    "directx", "dotnet", "mono", "monobleedingedge", "crashpad", "engine", "saves", "logs",
    "__macosx", "$recycle.bin", "node_modules", ".git",
}


def find_exes(folder, max_depth=3):
    """Return candidate game .exe files in `folder`, best guess first."""
    folder = Path(folder)
    key = re.sub(r"[^a-z0-9]", "", folder.name.lower())
    found = []

    def walk(d, depth):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_file() and e.name.lower().endswith(".exe"):
                    if not SKIP_EXE.search(e.name):
                        stem = re.sub(r"[^a-z0-9]", "", Path(e.name).stem.lower())
                        ratio = difflib.SequenceMatcher(None, key, stem).ratio()
                        found.append((depth, -round(ratio, 1), -e.stat().st_size, e.path))
                elif e.is_dir(follow_symlinks=False) and depth < max_depth \
                        and e.name.lower() not in SKIP_DIRS:
                    walk(e.path, depth + 1)
            except OSError:
                continue

    walk(folder, 0)
    found.sort()   # shallowest first, then closest to the folder name, then biggest file
    return [Path(f[3]) for f in found]


def discover_games(parent):
    """Look through a folder of games. Returns [(game_folder, [exe, ...]), ...]."""
    parent = Path(parent)
    out = []
    if find_exes(parent, max_depth=0):      # exe sits right in this folder -> it is a game itself
        return [(parent, find_exes(parent))]
    try:
        subs = sorted((p for p in parent.iterdir()
                       if p.is_dir() and p.name.lower() not in SKIP_DIRS), key=lambda p: p.name.lower())
    except OSError:
        subs = []
    for sub in subs:
        exes = find_exes(sub)
        if exes:
            out.append((sub, exes))
    if not out:                      # the chosen folder might itself be one game
        exes = find_exes(parent)
        if exes:
            out.append((parent, exes))
    return out


def clean_name(folder_name):
    name = re.sub(r"[-_.]+(?:pc|win|windows|win64|win32|public)\b", "", folder_name, flags=re.I)
    name = re.sub(r"[-_]+", " ", name).strip()
    return name or folder_name


def norm_path(p):
    return os.path.normcase(os.path.normpath(str(p)))


def manual_id(path):
    return f"manual:{hashlib.md5(norm_path(path).encode()).hexdigest()[:10]}"


def scan_all():
    games = []
    for fn in (scan_steam, scan_epic, scan_gog, scan_ubisoft):
        try:
            games += fn()
        except Exception:
            pass
    return games


def merge(existing, scanned):
    """Keep user data (favourites, playtime, custom covers) and manual games."""
    old = {g.id: g for g in existing}
    out = []
    for g in scanned:
        o = old.get(g.id)
        if o:
            g.favourite, g.launches, g.tracked_sec = o.favourite, o.launches, o.tracked_sec
            g.art_tried = o.art_tried
            g.folder = o.folder
            g.notes, g.status = o.notes, o.status
            g.controller, g.controller_checked, g.controller_ok = o.controller, o.controller_checked, o.controller_ok
            g.last_played = max(g.last_played, o.last_played)
            if o.custom_cover and o.cover and os.path.exists(o.cover):
                g.cover, g.custom_cover = o.cover, True
            elif not g.cover and o.cover and os.path.exists(o.cover):
                g.cover = o.cover
        out.append(g)
    out += [g for g in existing if g.store == "Manual"]
    return out


# --------------------------------------------------------------------------- #
# Background work
# --------------------------------------------------------------------------- #
class Bridge(QObject):
    scan_done = Signal(object)
    cover_done = Signal(str, str)
    running = Signal(object)
    watch_done = Signal(str, bool)
    art_done = Signal(str, str, bool)   # game id, image path ('' if none), looked-up-ok
    art_finished = Signal()
    controller_done = Signal(str, str)


class ScanTask(QRunnable):
    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge

    def run(self):
        self.bridge.scan_done.emit(scan_all())


class CoverTask(QRunnable):
    """Download missing Steam covers once, then they live on disk forever."""

    def __init__(self, bridge, items):
        super().__init__()
        self.bridge, self.items = bridge, items

    def run(self):
        for gid, appid in self.items:
            dest = COVERS / f"steam_{appid}.jpg"
            for name in ("library_600x900.jpg", "header.jpg"):
                url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{name}"
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    data = urllib.request.urlopen(req, timeout=10).read()
                    dest.write_bytes(data)
                    self.bridge.cover_done.emit(gid, str(dest))
                    break
                except Exception:
                    continue


# --------------------------------------------------------------------------- #
# Online cover art for non-Steam games (SteamGridDB, then Steam's store as fallback)
# --------------------------------------------------------------------------- #
SGDB_API = "https://www.steamgriddb.com/api/v2"
_UA = {"User-Agent": "Mozilla/5.0"}


def search_name(name):
    """Turn 'a new place 0.33.0' / 'Indulgences 0.9+(Ver.4)' into a clean title to search for."""
    n = re.sub(r"[\(\[].*?[\)\]]", " ", name)
    n, hadver = re.subn(r"\bv?\d+(?:\.\d+)+\+?", " ", n, flags=re.I)
    n = re.sub(r"\b(?:pc|win(?:dows)?(?:32|64)?|public|demo|build|ver(?:sion)?\.?\s*\d*)\b", " ", n, flags=re.I)
    n = re.sub(r"\b0\d{2,}\b", " ", n)                  # build numbers like 0351
    n = re.sub(r"[_\-]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip(" +.-")
    if hadver:
        n = re.sub(r"\s[a-z]$", "", n, flags=re.I)     # stray letter left from e.g. "0.22_d"
    return n or name


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _http_json(url, key=""):
    headers = dict(_UA)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8"))


def find_art_url(name, key="", fetch=_http_json):
    """Return (url or '', reached_internet). Portrait 600x900-style art preferred."""
    q = search_name(name)
    reached = False
    if key:
        try:
            res = fetch(f"{SGDB_API}/search/autocomplete/{urllib.parse.quote(q, safe='')}", key)
            reached = True
            hits = (res.get("data") or [])[:6]
            best = max(hits, key=lambda d: _sim(q, d.get("name", "")), default=None)
            if best and _sim(q, best.get("name", "")) >= 0.75:
                grids = fetch(f"{SGDB_API}/grids/game/{best['id']}?dimensions=600x900,342x482,660x930"
                              "&types=static&nsfw=any&humor=any", key)
                items = grids.get("data") or []
                if items and items[0].get("url"):
                    return items[0]["url"], True
        except Exception:
            pass
    try:
        res = fetch("https://store.steampowered.com/api/storesearch/?term="
                    f"{urllib.parse.quote(q, safe='')}&l=english&cc=us")
        reached = True
        items = (res.get("items") or [])[:5]
        best = max(items, key=lambda d: _sim(q, d.get("name", "")), default=None)
        if best and _sim(q, best.get("name", "")) >= 0.85:
            return (f"https://cdn.cloudflare.steamstatic.com/steam/apps/{best['id']}/library_600x900.jpg"), True
    except Exception:
        pass
    return "", reached


class ArtTask(QRunnable):
    """Looks up and downloads real cover art for a list of (game_id, name)."""

    def __init__(self, bridge, items, key):
        super().__init__()
        self.bridge, self.items, self.key = bridge, items, key

    def run(self):
        for gid, name in self.items:
            path, reached = "", False
            try:
                url, reached = find_art_url(name, self.key)
                if url:
                    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
                    ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".jpg"
                    dest = COVERS / f"art_{hashlib.md5(gid.encode()).hexdigest()[:10]}{ext}"
                    data = urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=20).read()
                    dest.write_bytes(data)
                    path = str(dest)
            except Exception:
                path = ""
            self.bridge.art_done.emit(gid, path, reached)
        self.bridge.art_finished.emit()


# --------------------------------------------------------------------------- #
# Controller-support lookup (Steam store data, matched by name for non-Steam games)
# --------------------------------------------------------------------------- #
def steam_controller_support(appid, fetch=_http_json):
    """'full' / 'partial' / 'none' for a Steam app, or '' if Steam has no data for it."""
    res = fetch(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english")
    node = res.get(str(appid)) or {}
    if not node.get("success"):
        return ""
    d = node.get("data") or {}
    cs = str(d.get("controller_support", "")).lower()
    if cs in ("full", "partial"):
        return cs
    for c in d.get("categories") or []:
        desc = str(c.get("description", "")).lower()
        if "full controller" in desc:
            return "full"
        if "controller" in desc:
            return "partial"
    return "none"


def lookup_controller(g, fetch=_http_json):
    """-> 'full' | 'partial' | 'none' | 'unknown', or '' when the internet couldn't be reached."""
    appid = None
    try:
        if g.store == "Steam":
            appid = g.id.split(":")[1]
        else:
            q = search_name(g.name)
            res = fetch("https://store.steampowered.com/api/storesearch/?term="
                        f"{urllib.parse.quote(q, safe='')}&l=english&cc=us")
            items = (res.get("items") or [])[:5]
            best = max(items, key=lambda d: _sim(q, d.get("name", "")), default=None)
            if best and _sim(q, best.get("name", "")) >= 0.85:
                appid = best["id"]
        if appid:
            r = steam_controller_support(appid, fetch)
            if r:
                return r
    except Exception:
        return ""                     # network trouble: don't cache, try again later
    if g.install_dir and (Path(g.install_dir) / "renpy").is_dir():
        return "partial"              # Ren'Py games have built-in gamepad navigation
    return "unknown"


class ControllerTask(QRunnable):
    def __init__(self, bridge, games):
        super().__init__()
        self.bridge, self.games = bridge, games

    def run(self):
        for g in self.games:
            self.bridge.controller_done.emit(g.id, lookup_controller(g))
            time.sleep(0.4)           # be polite to Steam's store


class ControllerWarning(QDialog):
    """'This game doesn't have controller support - are you sure?' (works with the controller too)."""

    def __init__(self, parent, text):
        super().__init__(parent)
        self.setWindowTitle("Controller support")
        self.setModal(True)
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)
        lay.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)   # never smaller than its content
        label = QLabel(text)
        label.setWordWrap(True)
        lay.addWidget(label)
        self.remember = QCheckBox("Don't ask again for this game")
        lay.addWidget(self.remember)
        hint = QLabel("D-pad: choose   \u2022   A: select   \u2022   B: cancel", objectName="status")
        lay.addWidget(hint)
        row = QHBoxLayout()
        yes, no = QPushButton("Yes"), QPushButton("No")
        for b in (yes, no):
            b.setFixedSize(110, 38)
        yes.clicked.connect(self.accept)
        no.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(yes)
        row.addWidget(no)
        lay.addLayout(row)
        no.setFocus()                     # safe default


class WatchTask(QRunnable):
    """Waits for a launched game to appear, then for it to fully exit."""

    def __init__(self, bridge, gid, install_dir, pid=None, interval=2):
        super().__init__()
        self.bridge, self.gid, self.dir, self.pid = bridge, gid, install_dir, pid
        self.interval = interval

    def _alive(self):
        try:
            if self.pid and psutil.pid_exists(self.pid):
                proc = psutil.Process(self.pid)
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    return True
                if any(c.is_running() for c in proc.children(recursive=True)):
                    return True
        except Exception:
            pass
        if self.dir:
            try:
                for proc in psutil.process_iter(["exe"]):
                    exe = proc.info.get("exe")
                    if exe and os.path.normcase(exe).startswith(self.dir):
                        return True
            except Exception:
                pass
        return False

    def run(self):
        start, seen, misses = time.time(), False, 0
        while True:
            if self._alive():
                seen, misses = True, 0
            elif seen:
                misses += 1          # grace period (~8s) so launcher->game hand-offs don't end early
                if misses >= max(2, round(8 / self.interval)):
                    break
            elif time.time() - start > 150:   # game never showed up
                break
            time.sleep(self.interval)
        self.bridge.watch_done.emit(self.gid, seen)


class TrackTask(QRunnable):
    """Finds which games have a running process inside their install folder."""

    def __init__(self, bridge, items):
        super().__init__()
        self.bridge, self.items = bridge, items

    def run(self):
        running = set()
        try:
            dirs = [(gid, os.path.normcase(os.path.abspath(d)).rstrip("\\/") + os.sep) for gid, d in self.items]
            for proc in psutil.process_iter(["exe"]):
                exe = proc.info.get("exe")
                if not exe:
                    continue
                exe = os.path.normcase(exe)
                for gid, d in dirs:
                    if exe.startswith(d):
                        running.add(gid)
        except Exception:
            pass
        self.bridge.running.emit(running)


def logo_pixmap(size=None):
    pm = QPixmap()
    pm.loadFromData(base64.b64decode(LOGO_B64), "PNG")
    if size and not pm.isNull():
        pm = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return pm


def app_icon():
    icon = QIcon()
    base = logo_pixmap()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(base.scaled(sz, sz, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))
    return icon


class Splash(QWidget):
    """Small, quick startup card: logo + name, fades out on its own."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(320, 190)
        self.logo = logo_pixmap(96)
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2, scr.center().y() - self.height() // 2)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, QColor("#1b1233"))
        grad.setColorAt(1, QColor("#0d0919"))
        p.setBrush(grad)
        p.setPen(QPen(QColor(ACCENT), 1.2))
        p.drawRoundedRect(r, 18, 18)
        p.drawPixmap((self.width() - self.logo.width()) // 2, 18, self.logo)
        f = QFont("Segoe UI", 15, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        p.setFont(f)
        p.setPen(QColor("#e4dcff"))
        p.drawText(QRectF(0, 122, self.width(), 30), Qt.AlignmentFlag.AlignCenter, "NEXUS GRID")
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor("#9a8fba"))
        p.drawText(QRectF(0, 152, self.width(), 18), Qt.AlignmentFlag.AlignCenter, "loading your library\u2026")

    def fade_out(self, then):
        self.anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.anim.setDuration(250)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(lambda: (self.close(), then()))
        self.anim.start()


def make_exe_cover(exe, name):
    """Build a cover from the .exe's own icon (on a coloured gradient) and cache it as a PNG."""
    dest = COVERS / f"exe_{hashlib.md5(norm_path(exe).encode()).hexdigest()[:10]}.png"
    if dest.exists():
        return str(dest)
    try:
        pm = QFileIconProvider().icon(QFileInfo(str(exe))).pixmap(256, 256)
    except Exception:
        return ""
    if pm.isNull():
        return ""
    W, H = 600, 900
    hue = 240 + int(hashlib.md5(name.encode()).hexdigest()[:4], 16) % 80
    out = QPixmap(W, H)
    p = QPainter(out)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    grad = QLinearGradient(0, 0, W, H)
    grad.setColorAt(0, QColor.fromHsl(hue, 90, 70))
    grad.setColorAt(1, QColor.fromHsl((hue + 40) % 360, 110, 28))
    p.fillRect(0, 0, W, H, grad)
    size = 340 if pm.width() >= 128 else 220
    icon = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap((W - icon.width()) // 2, (H - icon.height()) // 2 - 80, icon)
    p.end()
    out.save(str(dest), "PNG")
    return str(dest)


def exe_for(g):
    """Best .exe to take an icon from: the launch exe, else the best guess inside the install folder."""
    if g.launch and "://" not in g.launch and g.launch.lower().endswith(".exe") and os.path.isfile(g.launch):
        return g.launch
    if g.install_dir and os.path.isdir(g.install_dir):
        found = find_exes(g.install_dir, max_depth=2)
        if found:
            return str(found[0])
    return ""


def playtime_text(g):
    hours = g.playtime_min / 60 + g.tracked_sec / 3600
    if hours <= 0:
        return ""
    return f"{int(hours * 60)} min" if hours < 1 else f"{hours:.1f} h"


# --------------------------------------------------------------------------- #
# Model / proxy / delegate
# --------------------------------------------------------------------------- #
GAME_MIME = "application/x-nexusgrid-games"


def ago(ts):
    if not ts:
        return "never played"
    d = max(0, time.time() - ts)
    if d < 3600:
        return f"{max(1, int(d / 60))} min ago"
    if d < 86400:
        return f"{int(d / 3600)} h ago"
    if d < 86400 * 60:
        return f"{int(d / 86400)} d ago"
    return f"{int(d / (86400 * 30))} mo ago"


class GameModel(QAbstractListModel):
    """Rows = user folders first, then games. Every row's data() is a Folder or a Game."""

    def __init__(self):
        super().__init__()
        self.games = []
        self.folders = []
        self.rev = 0
        self._finfo, self._finfo_rev = {}, -1

    # -- rows
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.folders) + len(self.games)

    def item_at(self, row):
        nf = len(self.folders)
        return self.folders[row] if row < nf else self.games[row - nf]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.item_at(index.row())
        if role == GAME_ROLE:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        return None

    # -- drag a game onto a folder card
    def flags(self, index):
        f = super().flags(index)
        if index.isValid() and index.row() >= len(self.folders):
            f |= Qt.ItemFlag.ItemIsDragEnabled
        return f

    def mimeTypes(self):
        return [GAME_MIME]

    def mimeData(self, indexes):
        nf = len(self.folders)
        ids = [self.games[i.row() - nf].id for i in indexes if i.isValid() and i.row() >= nf]
        md = QMimeData()
        md.setData(GAME_MIME, "\n".join(ids).encode("utf-8"))
        return md

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    # -- data
    def set_games(self, games):
        self.beginResetModel()
        self.games = games
        self.rev += 1
        self.endResetModel()

    def set_folders(self, folders):
        self.beginResetModel()
        self.folders = folders
        self.rev += 1
        self.endResetModel()

    def touch(self, game):
        """Game changed: repaint its card and every folder card (their collages may change)."""
        self.rev += 1
        nf = len(self.folders)
        try:
            row = nf + self.games.index(game)
        except ValueError:
            return
        i = self.index(row)
        self.dataChanged.emit(i, i)
        if nf:
            self.dataChanged.emit(self.index(0), self.index(nf - 1))

    def touch_folders(self):
        self.rev += 1
        if self.folders:
            self.dataChanged.emit(self.index(0), self.index(len(self.folders) - 1))

    def add(self, game):
        row = len(self.folders) + len(self.games)
        self.beginInsertRows(QModelIndex(), row, row)
        self.games.append(game)
        self.rev += 1
        self.endInsertRows()

    def remove(self, game):
        row = len(self.folders) + self.games.index(game)
        self.beginRemoveRows(QModelIndex(), row, row)
        self.games.remove(game)
        self.rev += 1
        self.endRemoveRows()

    def folder_games(self, fid):
        """Games inside a folder, A-Z (cached until something changes)."""
        if self._finfo_rev != self.rev:
            info = {}
            for g in sorted(self.games, key=lambda x: x.name.lower()):
                if g.folder:
                    info.setdefault(g.folder, []).append(g)
            self._finfo, self._finfo_rev = info, self.rev
        return self._finfo.get(fid, [])


class GameProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.store = "All"
        self.recent = False
        self.folder = ""          # id of the folder being viewed ('' = top level)
        self.setDynamicSortFilter(True)

    def configure(self, text=None, store=None, recent=None, folder=None):
        if text is not None:
            self.text = text.lower().strip()
        if store is not None:
            self.store = store
        if recent is not None:
            self.recent = recent
        if folder is not None:
            self.folder = folder
        self.invalidate()

    def filterAcceptsRow(self, row, parent):
        item = self.sourceModel().item_at(row)
        flat = bool(self.text) or self.store != "All"      # searching / filtering = look at everything
        if isinstance(item, Folder):
            return not flat and not self.folder            # folder cards only on the plain top level
        g = item
        if self.text and self.text not in g.name.lower():
            return False
        if self.store == "Favourites":
            ok = g.favourite
        else:
            ok = self.store in ("All", g.store)
        if not ok:
            return False
        if self.folder:                                    # inside a folder: only its games
            return g.folder == self.folder
        return True if flat else not g.folder

    def lessThan(self, left, right):
        a, b = left.data(GAME_ROLE), right.data(GAME_ROLE)
        fa, fb = isinstance(a, Folder), isinstance(b, Folder)
        if fa != fb:
            return fa
        if fa:
            return a.name.lower() < b.name.lower()
        if a.favourite != b.favourite:
            return a.favourite
        if self.recent and a.last_played != b.last_played:
            return a.last_played > b.last_played
        return a.name.lower() < b.name.lower()


THUMB_BUCKETS = (160, 240, 360, 520)      # cached widths; the next size up is used so art stays sharp


class ThumbTask(QRunnable):
    """Builds (or loads) one cached thumbnail off the UI thread."""

    def __init__(self, cache, key, cover, width):
        super().__init__()
        self.cache, self.key, self.cover, self.w = cache, key, cover, width

    def run(self):
        img = QImage()
        try:
            dest = THUMBS / f"{self.key}.jpg"
            if dest.exists():
                img = QImage(str(dest))
            if img.isNull():
                src = QImage(self.cover)
                if not src.isNull():
                    w, h = self.w, int(self.w * 1.5)
                    src = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
                    out = QImage(w, h, QImage.Format.Format_RGB32)
                    out.fill(QColor("#140e26"))
                    p = QPainter(out)
                    p.drawImage((w - src.width()) // 2, (h - src.height()) // 2, src)
                    p.end()
                    out.save(str(dest), "JPG", 90)
                    img = out
        except Exception:
            img = QImage()
        self.cache.ready.emit(self.key, img)


class ThumbCache(QObject):
    """Small 2:3 JPEG copies of every cover on disk + a size-limited in-memory cache.
    Loading and resizing happens on 2 background threads, so scrolling never stalls."""
    ready = Signal(str, QImage)
    changed = Signal()
    BUDGET = 192 * 1024 * 1024

    def __init__(self):
        super().__init__()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(2)
        self.pm = OrderedDict()          # key -> QPixmap (least recently used first)
        self.bytes = 0
        self.pending, self.bad, self.keys = set(), set(), {}
        self._tick = QTimer(self)
        self._tick.setSingleShot(True)
        self._tick.setInterval(40)
        self._tick.timeout.connect(self.changed.emit)
        self.ready.connect(self._ready)
        self.prune()

    def prune(self):
        try:
            files = sorted(THUMBS.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
            if len(files) > 4000:
                for f in files[:len(files) - 3000]:
                    f.unlink(missing_ok=True)
        except Exception:
            pass

    def forget(self, cover):
        """The image behind this path changed: recompute its keys."""
        for ck in [c for c in self.keys if c[0] == cover]:
            del self.keys[ck]

    def get(self, cover, px):
        """Cached thumbnail for `cover` at least `px` wide, or None while it is being prepared."""
        bucket = next((b for b in THUMB_BUCKETS if b >= px), THUMB_BUCKETS[-1])
        ck = (cover, bucket)
        key = self.keys.get(ck)
        if key is None:
            try:
                st = os.stat(cover)
                key = hashlib.md5(f"{cover}|{st.st_mtime_ns}|{st.st_size}|{bucket}".encode()).hexdigest()[:20]
            except OSError:
                key = ""
            self.keys[ck] = key
        if not key or key in self.bad:
            return None
        pm = self.pm.get(key)
        if pm is not None:
            self.pm.move_to_end(key)
            return pm
        if key not in self.pending:
            self.pending.add(key)
            self.pool.start(ThumbTask(self, key, cover, bucket))
        for b in THUMB_BUCKETS:               # a different size already in memory beats a blank card
            k2 = self.keys.get((cover, b))
            if k2 and k2 in self.pm:
                return self.pm[k2]
        return None

    def _ready(self, key, img):
        self.pending.discard(key)
        if img.isNull():
            self.bad.add(key)
            return
        pm = QPixmap.fromImage(img)
        self.pm[key] = pm
        self.bytes += pm.width() * pm.height() * 4
        while self.bytes > self.BUDGET and len(self.pm) > 1:
            _, old = self.pm.popitem(last=False)
            self.bytes -= old.width() * old.height() * 4
        if not self._tick.isActive():
            self._tick.start()                # coalesce: repaint once for a burst of finished thumbs


class CoverPainter:
    """Draws a game's cover (cropped to fill) from the thumbnail cache, or a coloured placeholder."""

    def __init__(self, thumbs):
        self.thumbs = thumbs
        self._ph = {}

    def _draw_app(self, p, target, g):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QLinearGradient(target.topLeft(), target.bottomRight())
        bg.setColorAt(0, QColor("#2c2c2c"))
        bg.setColorAt(1, QColor("#0f0f0f"))
        p.fillRect(target, bg)
        w = target.width() * 0.62
        h = w * 0.7
        r = QRectF(target.center().x() - w / 2, target.center().y() - h / 2 - target.height() * 0.05, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ff0033"))
        p.drawRoundedRect(r, h * 0.3, h * 0.3)
        tri = QPainterPath()
        tri.moveTo(r.left() + w * 0.40, r.top() + h * 0.28)
        tri.lineTo(r.left() + w * 0.40, r.bottom() - h * 0.28)
        tri.lineTo(r.left() + w * 0.69, r.center().y())
        tri.closeSubpath()
        p.setBrush(QColor("white"))
        p.drawPath(tri)
        p.setFont(_px_font(target.height() * 0.075, True))
        p.setPen(QColor("white"))
        p.drawText(QRectF(target.left(), r.bottom() + target.height() * 0.04, target.width(), target.height() * 0.1),
                   Qt.AlignmentFlag.AlignCenter, g.name)
        p.restore()

    def _draw_soon(self, p, target, g):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QLinearGradient(target.topLeft(), target.bottomRight())
        bg.setColorAt(0, QColor("#2b2939"))
        bg.setColorAt(1, QColor("#14121e"))
        p.fillRect(target, bg)
        p.setFont(_px_font(target.height() * 0.12, True))
        p.setPen(QColor(255, 255, 255, 205))
        p.drawText(target.adjusted(target.width() * 0.08, 0, -target.width() * 0.08, 0),
                   Qt.TextFlag.TextWordWrap.value | Qt.AlignmentFlag.AlignCenter.value, g.name)
        w, h = target.width() * 0.5, target.height() * 0.1
        chip = QRectF(target.center().x() - w / 2, target.bottom() - target.height() * 0.2, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(chip, h / 2, h / 2)
        p.setFont(_px_font(h * 0.62, True, 1))
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "SOON")
        p.restore()

    def draw(self, p, target, g):
        if g.id == "app:youtube":
            self._draw_app(p, target, g)
            return
        if g.store == "Soon":
            self._draw_soon(p, target, g)
            return
        pm = None
        if g.cover:
            try:
                dpr = p.device().devicePixelRatioF()
            except Exception:
                dpr = 1.0
            pm = self.thumbs.get(g.cover, int(target.width() * dpr))
        if pm is None:
            pm = self._ph.get(g.name)
            if pm is None:
                if len(self._ph) > 400:
                    self._ph.clear()
                pm = self._ph[g.name] = CardDelegate._placeholder(g.name, 200, 300)
        sw, sh = pm.width(), pm.height()
        scale = max(target.width() / sw, target.height() / sh)
        cw, ch = target.width() / scale, target.height() / scale
        p.drawPixmap(target, pm, QRectF((sw - cw) / 2, (sh - ch) / 2, cw, ch))


def make_backdrop(thumbs, g):
    """A tiny, heavily blurred copy of the cover for full-screen backgrounds."""
    pm = thumbs.get(g.cover, 160) if g.cover else None
    if pm is None:
        return None
    return pm.scaled(64, 96, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation) \
             .scaled(14, 21, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


class CardDelegate(QStyledItemDelegate):
    LIST_H = 64

    def __init__(self, model, covers):
        super().__init__()
        self.model = model
        self.covers = covers
        self.tile_w = 200
        self.list_mode = False
        self.row_w = 600
        self.big = False                 # Big Picture: selected card pops out
        self.show_playtime = True
        self._cache = {}
        self._name_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        self._sub_font = QFont("Segoe UI", 8)
        self._list_name = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        self._list_sub = QFont("Segoe UI", 9)

    def set_tile_width(self, w):
        if w == self.tile_w:
            return False
        self.tile_w = w
        self._cache.clear()
        return True

    def sizeHint(self, option, index):
        if self.list_mode:
            return QSize(self.row_w, self.LIST_H)
        return QSize(self.tile_w + 16, int(self.tile_w * 1.5) + 16)

    @staticmethod
    def _placeholder(name, w, h):
        hue = 240 + int(hashlib.md5(name.encode()).hexdigest()[:4], 16) % 80
        pm = QPixmap(w, h)
        p = QPainter(pm)
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0, QColor.fromHsl(hue, 120, 90))
        grad.setColorAt(1, QColor.fromHsl((hue + 40) % 360, 140, 40))
        p.fillRect(0, 0, w, h, grad)
        letters = "".join(wd[0] for wd in name.split()[:2]).upper() or "?"
        f = QFont("Segoe UI", max(14, w // 4), QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(QRectF(0, 0, w, h * 0.75), Qt.AlignmentFlag.AlignCenter, letters)
        p.end()
        return pm

    @staticmethod
    def _folder_glyph(p, r, color):
        """A simple folder icon fitted in rect r."""
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        tab = QRectF(r.left(), r.top() + r.height() * 0.12, r.width() * 0.42, r.height() * 0.2)
        body = QRectF(r.left(), r.top() + r.height() * 0.25, r.width(), r.height() * 0.62)
        p.drawRoundedRect(tab, 4, 4)
        p.drawRoundedRect(body, 6, 6)
        p.restore()

    # -- painting
    def paint(self, p, opt, idx):
        item = idx.data(GAME_ROLE)
        if self.list_mode:
            return self._paint_list(p, opt, item)
        if isinstance(item, Folder):
            return self._paint_folder(p, opt, item)
        return self._paint_game(p, opt, item)

    def _card_rect(self, opt):
        base = QRectF(opt.rect).adjusted(8, 8, -8, -8)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if self.big and selected:
            r = base.adjusted(-8, -8, 8, 8)
        elif hover:
            r = base.adjusted(-4, -4, 4, 4)
        else:
            r = base
        return base, r, hover, selected

    def _border(self, p, r, hover, selected):
        p.setClipping(False)
        if hover or selected:
            if self.big and selected:      # soft glow so you can see where the controller is
                glow = QColor(ACCENT)
                glow.setAlpha(70)
                p.setPen(QPen(glow, 9))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r, 13, 13)
            p.setPen(QPen(QColor(ACCENT), 3 if self.big else 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 12, 12)

    def _paint_game(self, p, opt, g):
        base, r, hover, selected = self._card_rect(opt)
        p.save()
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.setClipPath(path)

        self.covers.draw(p, r, g)

        band = QRectF(r.left(), r.bottom() - 70, r.width(), 70)
        grad = QLinearGradient(band.topLeft(), band.bottomLeft())
        grad.setColorAt(0, QColor(0, 0, 0, 0))
        grad.setColorAt(1, QColor(0, 0, 0, 220))
        p.fillRect(band, grad)

        p.setPen(QColor("white"))
        p.setFont(self._name_font)
        name = QFontMetrics(self._name_font).elidedText(g.name, Qt.TextElideMode.ElideRight, int(r.width() - 20))
        p.drawText(QRectF(r.left() + 10, r.bottom() - 40, r.width() - 20, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        p.setPen(QColor(255, 255, 255, 150))
        p.setFont(self._sub_font)
        pt = playtime_text(g) if self.show_playtime else ""
        p.drawText(QRectF(r.left() + 10, r.bottom() - 21, r.width() - 20, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   g.store + (f"  \u2022  {pt}" if pt else ""))

        if g.favourite:
            p.setPen(QColor("#ffd54a"))
            p.setFont(QFont("Segoe UI", 14))
            p.drawText(QRectF(r.right() - 32, r.top() + 4, 28, 26), Qt.AlignmentFlag.AlignCenter, "\u2605")

        self._border(p, r, hover, selected)
        p.restore()

    def _paint_folder(self, p, opt, f):
        base, r, hover, selected = self._card_rect(opt)
        games = self.model.folder_games(f.id)
        p.save()
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.setClipPath(path)

        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        bg.setColorAt(0, QColor("#2a1d4e"))
        bg.setColorAt(1, QColor("#140e26"))
        p.fillRect(r, bg)
        stripe = QColor(ACCENT)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 5), stripe)

        area = QRectF(r.left() + 12, r.top() + 16, r.width() - 24, r.height() - 86)
        if games:
            gap = 6
            cw, ch = int((area.width() - gap) / 2), int((area.height() - gap) / 2)
            for i in range(4):
                cell = QRectF(area.left() + (i % 2) * (cw + gap), area.top() + (i // 2) * (ch + gap), cw, ch)
                cp = QPainterPath()
                cp.addRoundedRect(cell, 8, 8)
                p.save()
                p.setClipPath(cp, Qt.ClipOperation.IntersectClip)
                if i < len(games):
                    self.covers.draw(p, cell, games[i])
                else:
                    p.fillRect(cell, QColor(255, 255, 255, 18))
                p.restore()
        else:
            size = min(area.width(), area.height()) * 0.7
            self._folder_glyph(p, QRectF(area.center().x() - size / 2, area.center().y() - size / 2, size, size),
                               QColor(255, 255, 255, 60))

        p.setPen(QColor("white"))
        p.setFont(self._name_font)
        name = QFontMetrics(self._name_font).elidedText(f.name, Qt.TextElideMode.ElideRight, int(r.width() - 20))
        p.drawText(QRectF(r.left() + 10, r.bottom() - 52, r.width() - 20, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        p.setPen(QColor(255, 255, 255, 150))
        p.setFont(self._sub_font)
        n = len(games)
        p.drawText(QRectF(r.left() + 10, r.bottom() - 28, r.width() - 20, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"Folder  \u2022  {n} game" + ("" if n == 1 else "s"))
        self._border(p, r, hover, selected)
        p.restore()

    def _paint_list(self, p, opt, item):
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        r = QRectF(opt.rect).adjusted(4, 3, -4, -3)
        p.save()
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2a1f4a") if (selected or hover) else QColor("#150f26"))
        p.drawRoundedRect(r, 10, 10)
        if selected:
            p.setPen(QPen(QColor(ACCENT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 10, 10)

        th_h = r.height() - 12
        th = QRectF(r.left() + 8, r.top() + 6, th_h * 2 / 3, th_h)
        folder = isinstance(item, Folder)
        if folder:
            self._folder_glyph(p, th.adjusted(-2, 4, 2, -2), QColor(ACCENT))
        else:
            cp = QPainterPath()
            cp.addRoundedRect(th, 5, 5)
            p.save()
            p.setClipPath(cp)
            self.covers.draw(p, th, item)
            p.restore()

        x = th.right() + 14
        right_pad = 150
        p.setPen(QColor("white"))
        p.setFont(self._list_name)
        name = QFontMetrics(self._list_name).elidedText(item.name, Qt.TextElideMode.ElideRight,
                                                        int(r.right() - x - right_pad))
        p.drawText(QRectF(x, r.top() + 8, r.right() - x - right_pad, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        p.setPen(QColor(255, 255, 255, 140))
        p.setFont(self._list_sub)
        if folder:
            n = len(self.model.folder_games(item.id))
            sub = f"Folder  \u2022  {n} game" + ("" if n == 1 else "s")
        else:
            bits = [item.store]
            pt = playtime_text(item) if self.show_playtime else ""
            if pt:
                bits.append(pt)
            bits.append(ago(item.last_played) if item.last_played else "not played yet")
            sub = "  \u2022  ".join(bits)
        p.drawText(QRectF(x, r.top() + 32, r.right() - x - right_pad, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)

        if not folder:
            if item.controller in ("full", "partial"):
                p.setFont(QFont("Segoe UI", 8))
                pill = QRectF(r.right() - 140, r.top() + (r.height() - 22) / 2, 88, 22)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(139, 92, 246, 70))
                p.drawRoundedRect(pill, 11, 11)
                p.setPen(QColor("#d9ccff"))
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter, "Controller")
            if item.favourite:
                p.setPen(QColor("#ffd54a"))
                p.setFont(QFont("Segoe UI", 15))
                p.drawText(QRectF(r.right() - 40, r.top(), 32, r.height()), Qt.AlignmentFlag.AlignCenter, "\u2605")
        p.restore()


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
def build_style(ACCENT):
    return f"""
QWidget {{ background: #0e0a18; color: #ece8f8; font-family: "Segoe UI"; font-size: 13px; }}
QLineEdit, QComboBox {{ background: #1a1330; border: 1px solid #32254f; border-radius: 8px; padding: 7px 10px; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: #1a1330; selection-background-color: {ACCENT}; border: 0; }}
QPushButton {{ background: #261c42; border: 2px solid transparent; border-radius: 8px; padding: 0px 16px; min-height: 28px; }}
QPushButton:hover {{ background: {ACCENT}; }}
QPushButton:focus {{ border-color: #c4b5fd; background: #3a2a63; }}
QListView {{ background: transparent; border: 0; outline: 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3d2d63; border-radius: 5px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QSlider::groove:horizontal {{ height: 4px; background: #32254f; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QMenu {{ background: #1a1330; border: 1px solid #32254f; padding: 4px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QLabel#status {{ color: #9a8fba; }}
QLabel#brand {{ color: #c4b5fd; font-size: 15px; font-weight: 700; letter-spacing: 3px; }}
QListWidget {{ background: #130e22; border: 1px solid #32254f; border-radius: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4d3a7c; background: #1a1330; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{ background: #1a1330; color: #ece8f8; border: 1px solid {ACCENT}; padding: 4px; }}
QTabWidget::pane {{ border: 1px solid #32254f; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: #1a1330; padding: 8px 18px; margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
QTabBar::tab:selected {{ background: {ACCENT}; }}
QLabel#crumb {{ font-size: 17px; font-weight: 700; color: #e4dcff; }}
QLabel#bpbrand {{ color: #e4dcff; font-size: 22px; font-weight: 800; letter-spacing: 5px; }}
QLabel#bpclock {{ color: #c4b5fd; font-size: 20px; font-weight: 600; }}
QLabel#bpinfo {{ color: #9a8fba; font-size: 14px; }}
QLabel#section {{ color: #c4b5fd; font-size: 12px; font-weight: 700; letter-spacing: 1px; }}
QPushButton:checked {{ background: {ACCENT}; }}
QPushButton#playbtn {{ background: {ACCENT}; font-size: 16px; font-weight: 700; padding: 0px 30px; }}
QPushButton#playbtn:focus {{ border-color: #ffffff; }}
QPushButton#ytclose {{ background: #c4302b; font-weight: 700; }}
QLabel#statkey {{ color: #9a8fba; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#statval {{ color: #ece8f8; font-size: 14px; font-weight: 600; }}
QLabel#soonchip {{ background: rgba(255, 255, 255, 16); border: 1px dashed rgba(255, 255, 255, 70); border-radius: 10px; color: rgba(255, 255, 255, 150); padding: 5px 10px; font-size: 12px; }}
QPlainTextEdit {{ background: rgba(26, 19, 48, 210); border: 1px solid #32254f; border-radius: 8px; padding: 6px; }}
"""


STYLE = build_style(ACCENT)


# --------------------------------------------------------------------------- #
# Controller (XInput via ctypes - no extra packages) and keyboard helpers
# --------------------------------------------------------------------------- #
class _XPad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _XState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint32), ("Gamepad", _XPad)]


# XInput button bits
BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
BTN_START, BTN_BACK = 0x0010, 0x0020
BTN_LB, BTN_RB = 0x0100, 0x0200
BTN_A, BTN_B, BTN_X, BTN_Y = 0x1000, 0x2000, 0x4000, 0x8000
BTN_L3, BTN_R3 = 0x0040, 0x0080          # stick clicks


class XInput:
    """Reads Xbox / XInput-compatible controllers (Windows). Safe no-op elsewhere."""

    def __init__(self):
        self.fn = None
        if os.name == "nt":
            for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
                try:
                    fn = ctypes.WinDLL(dll).XInputGetState
                    fn.argtypes = [ctypes.c_uint32, ctypes.POINTER(_XState)]
                    fn.restype = ctypes.c_uint32
                    self.fn = fn
                    break
                except (OSError, AttributeError):
                    continue
        self.slots, self._next_scan = [], 0.0

    def _read(self, i):
        st = _XState()
        return st.Gamepad if self.fn(i, ctypes.byref(st)) == 0 else None

    def read(self):
        """(buttons, lt, rt, lx, ly, rx, ry) merged over connected pads, or None if no pad."""
        if not self.fn:
            return None
        now = time.time()
        if now >= self._next_scan:            # look for newly plugged-in pads every 2s
            self._next_scan = now + 2.0
            self.slots = [i for i in range(4) if self._read(i) is not None]
        btn = lt = rt = lx = ly = rx = ry = 0
        alive = False
        for i in list(self.slots):
            g = self._read(i)
            if g is None:
                self.slots.remove(i)
                continue
            alive = True
            btn |= g.wButtons
            lt, rt = max(lt, g.bLeftTrigger), max(rt, g.bRightTrigger)
            if abs(g.sThumbLX) + abs(g.sThumbLY) > abs(lx) + abs(ly):
                lx, ly = g.sThumbLX, g.sThumbLY
            if abs(g.sThumbRX) + abs(g.sThumbRY) > abs(rx) + abs(ry):
                rx, ry = g.sThumbRX, g.sThumbRY
        return (btn, lt, rt, lx, ly, rx, ry) if alive else None


def send_key(widget, key):
    for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(widget, QKeyEvent(t, key, Qt.KeyboardModifier.NoModifier))


class SearchBox(QLineEdit):
    go_grid = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Down:
            self.go_grid.emit()
            return
        if e.key() == Qt.Key.Key_Escape:
            if self.text():
                self.clear()
            else:
                self.go_grid.emit()
            return
        super().keyPressEvent(e)


class GridView(QListView):
    resized = Signal()
    typed = Signal(str)
    escaped = Signal()
    context_requested = Signal(QPoint, bool)     # position, came from keyboard
    dropped = Signal(object, object)             # proxy index dropped on, mime data
    can_drop = None                              # callable(index) -> bool, set by the launcher

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def _drop_target(self, e):
        if not e.mimeData().hasFormat(GAME_MIME):
            return None
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid() and self.can_drop and self.can_drop(idx):
            return idx
        return None

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(GAME_MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._drop_target(e) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        idx = self._drop_target(e)
        if idx is None:
            e.ignore()
            return
        self.dropped.emit(idx, e.mimeData())
        e.acceptProposedAction()

    def keyPressEvent(self, e):
        txt = e.text()
        blocked = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                   | Qt.KeyboardModifier.MetaModifier)
        if txt and txt.isprintable() and not (e.modifiers() & blocked):
            self.typed.emit(txt)          # start typing anywhere = search
            return
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace):
            self.escaped.emit()           # back out of a folder / clear the search
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        self.context_requested.emit(e.pos(), e.reason() == QContextMenuEvent.Reason.Keyboard)


def _ease_out_cubic(x):
    return 1 - (1 - x) ** 3


def _ease_in_cubic(x):
    return x ** 3


def _ease_out_back(x):
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _clamp(x):
    return max(0.0, min(1.0, x))


class TransitionOverlay(QWidget):
    """Full-window iris transition: a glowing ring sweeps out and covers the screen, the logo pulses
    while the mode switches underneath, then the ring opens up again to reveal the new mode."""
    COVER_END, REVEAL_START = 0.36, 0.72

    def __init__(self, parent, title, subtitle, on_mid, on_done):
        super().__init__(parent)
        self.setGeometry(parent.rect())
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.title, self.subtitle = title, subtitle
        self.on_mid, self.on_done = on_mid, on_done
        self.logo = logo_pixmap(180)
        self.t, self._mid = 0.0, False
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(1600)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self._finish)
        self.show()
        self.raise_()
        self.setFocus()                 # keyboard input goes here (and is ignored) while it plays
        self.anim.start()

    def _tick(self, v):
        self.t = float(v)
        if not self._mid and self.t >= self.COVER_END:
            self._mid = True                    # screen is fully covered: switch modes underneath
            try:
                self.on_mid()
            finally:
                self.setGeometry(self.parentWidget().rect())
                self.raise_()
        self.update()

    def _finish(self):
        self.hide()
        self.deleteLater()
        self.on_done()

    def paintEvent(self, _):
        t, w, h = self.t, self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        c = QPointF(w / 2, h / 2)
        R = math.hypot(w, h) / 2 + 30
        full = QPainterPath()
        full.addRect(QRectF(0, 0, w, h))
        ring = True
        if t < self.COVER_END:                                    # ring grows, covering the screen
            r_edge = R * _ease_out_cubic(t / self.COVER_END)
            shape = QPainterPath()
            shape.addEllipse(c, r_edge, r_edge)
        elif t < self.REVEAL_START:                               # fully covered
            r_edge, shape, ring = R, full, False
        else:                                                     # iris opens
            r_edge = R * _ease_in_cubic((t - self.REVEAL_START) / (1 - self.REVEAL_START))
            hole = QPainterPath()
            hole.addEllipse(c, r_edge, r_edge)
            shape = full.subtracted(hole)

        p.save()
        p.setClipPath(shape)
        bg = QRadialGradient(c, R)
        bg.setColorAt(0, QColor("#2b1659"))
        bg.setColorAt(0.6, QColor("#150c2e"))
        bg.setColorAt(1, QColor("#070411"))
        p.fillRect(self.rect(), bg)
        p.setPen(QPen(QColor(139, 92, 246, 34), 1))
        step, off = 64, (t * 140) % 64
        x = -off
        while x < w:
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            x += step
        y = -off
        while y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
            y += step
        p.restore()

        if ring and r_edge > 1:
            glow = QColor(ACCENT)
            glow.setAlpha(70)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(glow, 16))
            p.drawEllipse(c, r_edge, r_edge)
            p.setPen(QPen(QColor("#c4b5fd"), 3))
            p.drawEllipse(c, r_edge, r_edge)

        alpha = _clamp((t - 0.16) / 0.14) * (1 - _clamp((t - 0.70) / 0.14))
        if alpha > 0:
            sc = 0.78 + 0.22 * _ease_out_back(_clamp((t - 0.16) / 0.4))
            if t > 0.70:
                sc += 0.4 * _clamp((t - 0.70) / 0.3)
            size = 180 * sc
            lg = QRectF(c.x() - size / 2, c.y() - size / 2 - 30, size, size)
            p.setOpacity(alpha)
            p.drawPixmap(lg, self.logo, QRectF(self.logo.rect()))
            pen = QPen(QColor("#c4b5fd"), 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            spin = lg.adjusted(-26, -26, 26, 26)
            start = int(-t * 720 * 16)
            p.drawArc(spin, start, 100 * 16)
            p.drawArc(spin, start + 180 * 16, 60 * 16)
            f = QFont("Segoe UI", 30, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 12)
            p.setFont(f)
            p.setPen(QColor("#ece8f8"))
            p.drawText(QRectF(0, lg.bottom() + 44, w, 50), Qt.AlignmentFlag.AlignCenter, self.title)
            f2 = QFont("Segoe UI", 11)
            f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
            p.setFont(f2)
            p.setPen(QColor("#9a8fba"))
            p.drawText(QRectF(0, lg.bottom() + 94, w, 24), Qt.AlignmentFlag.AlignCenter, self.subtitle)
        p.end()


class SettingsDialog(QDialog):
    """Settings in tabs. Every control saves immediately."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Settings")
        self.resize(700, 580)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._general(), "General")
        self.tabs.addTab(self._library(), "Library")
        self.tabs.addTab(self._appearance(), "Appearance")
        self.tabs.addTab(self._controller(), "Controller")
        self.tabs.addTab(self._covers(), "Covers")
        self.tabs.addTab(self._youtube(), "YouTube")
        self.tabs.addTab(self._soon(), "Coming soon")
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    # ---- helpers
    def _page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(20, 18, 20, 18)
        l.setSpacing(12)
        return w, l

    def _section(self, layout, text):
        layout.addWidget(QLabel(text.upper(), objectName="section"))

    def _set(self, key, value):
        self.win.settings.setValue(key, value)
        self.win.apply_settings(key)

    def _check(self, layout, key, text, default, checked=None):
        cb = QCheckBox(text)
        cb.setChecked(self.win.cfg_bool(key, default) if checked is None else checked)
        cb.toggled.connect(lambda on, k=key: self._set(k, on))
        layout.addWidget(cb)
        return cb

    def _combo(self, layout, label, key, options, default):
        row = QHBoxLayout()
        row.addWidget(QLabel(label), 1)
        cb = QComboBox()
        for text, val in options:
            cb.addItem(text, val)
        cb.setCurrentIndex(max(0, cb.findData(str(self.win.settings.value(key, default)))))
        cb.currentIndexChanged.connect(lambda i, k=key, c=cb: self._set(k, c.itemData(i)))
        row.addWidget(cb)
        layout.addLayout(row)
        return cb

    # ---- pages
    def _general(self):
        w, l = self._page()
        self._section(l, "Closing")
        self._combo(l, "When I close the window", "close_action",
                    [("Quit Nexus Grid completely", "quit"), ("Keep running in the system tray", "tray")], "quit")
        self._section(l, "Startup")
        self.win.settings.setValue("startup", startup_enabled())      # the registry is the truth
        self._check(l, "startup", "Start Nexus Grid when Windows starts", False, checked=startup_enabled())
        self._check(l, "startup_minimized", "\u2026and start hidden in the system tray", False)
        self._check(l, "start_bp", "Open in Big Picture mode", False)
        self._check(l, "scan_on_start", "Scan for games when it starts", True)
        self._check(l, "auto_import", "Offer to add new games found in my saved folders", False)
        row = QHBoxLayout()
        open_ng = QPushButton("Open the NexusGrid folder")
        open_ng.clicked.connect(lambda: os.startfile(str(NG_ROOT)))
        row.addWidget(open_ng)
        open_rm = QPushButton("Open the Roadmap")
        open_rm.clicked.connect(lambda: (self.accept(), QTimer.singleShot(0, self.win.open_roadmap)))
        row.addWidget(open_rm)
        row.addStretch(1)
        l.addLayout(row)
        self._section(l, "Playing")
        self._check(l, "hide_on_launch", "Hide the launcher while a game is running", True)
        l.addStretch(1)
        return w

    def _library(self):
        w, l = self._page()
        self._section(l, "Display")
        self._combo(l, "Default sort", "sort", [("A\u2013Z", "az"), ("Recently played", "recent")], "az")
        self._check(l, "show_playtime", "Show playtime on cards", True)
        self._combo(l, "Opening a game in the library", "open_action",
                    [("Show its game page", "page"), ("Launch it right away", "launch")], "page")
        self._section(l, "Saved game folders (checked when you press Scan)")
        self.flist = QListWidget()
        self.flist.addItems(self.win.library_folders())
        l.addWidget(self.flist, 1)
        row = QHBoxLayout()
        add, rem, chk = QPushButton("Add folders\u2026"), QPushButton("Remove selected"), QPushButton("Check for new games now")
        add.clicked.connect(self._add_folders)
        rem.clicked.connect(self._remove_folders)
        chk.clicked.connect(lambda: self.win.import_from(
            [f for f in self.win.library_folders() if os.path.isdir(f)], announce=True))
        for b in (add, rem, chk):
            row.addWidget(b)
        l.addLayout(row)
        return w

    def _add_folders(self):
        picked = self.win.pick_folders()
        folders = self.win.library_folders()
        for d in picked:
            if d not in folders:
                folders.append(d)
        self.win.settings.setValue("library_folders", folders)
        self.flist.clear()
        self.flist.addItems(folders)

    def _remove_folders(self):
        gone = {i.text() for i in self.flist.selectedItems()}
        folders = [f for f in self.win.library_folders() if f not in gone]
        self.win.settings.setValue("library_folders", folders)
        self.flist.clear()
        self.flist.addItems(folders)

    def _appearance(self):
        w, l = self._page()
        self._section(l, "Look")
        self._combo(l, "Accent colour", "accent", [(n, v) for n, v in ACCENT_PRESETS.items()], ACCENT)
        self._combo(l, "Default view", "view_mode", [("Grid", "grid"), ("List", "list")], "grid")
        self._check(l, "splash", "Show the startup splash screen", True)
        self._section(l, "Big Picture")
        self._check(l, "animations", "Play the transition animation", True)
        row = QHBoxLayout()
        row.addWidget(QLabel("Card size in Big Picture"), 1)
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(180, 380)
        sl.setValue(self.win.cfg_int("bp_tile", 250))
        sl.setFixedWidth(220)
        sl.valueChanged.connect(lambda v: self._set("bp_tile", v))
        row.addWidget(sl)
        l.addLayout(row)
        l.addStretch(1)
        return w

    def _controller(self):
        w, l = self._page()
        self._section(l, "Controller")
        self._check(l, "pad_enabled", "Use a connected controller to navigate", True)
        self._check(l, "pad_warn", "Warn me before launching a game that has no controller support", True)
        note = QLabel("Works with Xbox and other XInput controllers. PlayStation / Switch pads need "
                      "Steam Input or DS4Windows to appear as Xbox controllers.")
        note.setWordWrap(True)
        note.setObjectName("status")
        l.addWidget(note)
        l.addStretch(1)
        return w

    def _youtube(self):
        w, l = self._page()
        self._section(l, "YouTube")
        self._check(l, "yt_windowed", "Open YouTube in a window instead of fullscreen", False)
        row = QHBoxLayout()
        for text, fn in (("Open YouTube folder", self._open_yt_folder), ("Open log", self._open_yt_log),
                         ("Sign out / reset YouTube", self._clear_youtube)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        l.addLayout(row)
        note = QLabel("YouTube opens as its own full-screen app: the real youtube.com in Windows' Edge WebView2 "
                      "(so Google sign-in works like it does in Edge), with its own controller support.\n\n"
                      "Press X + B together on the controller (or click \u201cClose\u201d in its bottom bar) "
                      "to close it and come back here.\n\n"
                      "The first launch installs a few Python packages and can take a minute. To keep a sign-in "
                      "from your old YouTube folder, copy its \u201c.webview_data\u201d folder into the YouTube "
                      "folder above while YouTube is closed.")
        note.setWordWrap(True)
        note.setObjectName("status")
        l.addWidget(note)
        l.addStretch(1)
        return w

    def _open_yt_folder(self):
        try:
            ensure_youtube_app()
            os.startfile(str(YT_DIR))
        except Exception as e:
            QMessageBox.warning(self, "YouTube", str(e))

    def _open_yt_log(self):
        log = YT_DIR / "app.log"
        if log.exists():
            os.startfile(str(log))
        else:
            QMessageBox.information(self, "YouTube", "No log yet - open YouTube once first.")

    def _clear_youtube(self):
        if self.win.yt_running():
            QMessageBox.information(self, "YouTube", "Close YouTube first, then try again.")
            return
        if QMessageBox.question(self, "YouTube", "Sign out of YouTube and delete its saved browser data?") \
                == QMessageBox.StandardButton.Yes:
            shutil.rmtree(YT_DIR / ".webview_data", ignore_errors=True)

    def _soon(self):
        w, l = self._page()
        self._section(l, "Planned settings (not built yet)")
        for r in ROADMAP:
            if r[1] == "Settings":
                cb = QCheckBox(f"{r[2]}  \u2013  {r[3]}")
                cb.setEnabled(False)
                l.addWidget(cb)
        btn = QPushButton("Open the Roadmap")
        btn.clicked.connect(lambda: (self.accept(), QTimer.singleShot(0, self.win.open_roadmap)))
        l.addWidget(btn)
        l.addStretch(1)
        return w

    def _covers(self):
        w, l = self._page()
        self._section(l, "Cover art")
        self._check(l, "auto_art", "Automatically look for cover art for new games", True)
        l.addWidget(QLabel("SteamGridDB API key (free \u2013 finds art for non-Steam games)"))
        self.key = QLineEdit(str(self.win.settings.value("sgdb_key", "") or ""))
        self.key.setPlaceholderText("paste your key here")
        self.key.editingFinished.connect(lambda: self.win.set_sgdb_key_value(self.key.text()))
        l.addWidget(self.key)
        row = QHBoxLayout()
        get = QPushButton("Get a free key")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.steamgriddb.com/profile/preferences/api")))
        fetch = QPushButton("Fetch missing art")
        fetch.clicked.connect(lambda: self.win.fetch_art())
        refetch = QPushButton("Re-fetch all art")
        refetch.clicked.connect(lambda: self.win.fetch_art(force=True))
        for b in (get, fetch, refetch):
            row.addWidget(b)
        l.addLayout(row)
        l.addStretch(1)
        return w


STATUS_CHOICES = [("No status", ""), ("Playing", "Playing"), ("Finished", "Finished"),
                  ("Backlog", "Backlog"), ("Dropped", "Dropped")]


def _px_font(px, bold=False, spacing=0):
    f = QFont("Segoe UI")
    f.setPixelSize(max(8, int(px)))
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def paint_backdrop(p, rect, tiny, opacity, game=None):
    """Blurred cover as a full-area background (or a soft gradient when there is no cover)."""
    if opacity <= 0:
        return
    p.save()
    p.setOpacity(opacity)
    if tiny is not None:
        sw, sh = tiny.width(), tiny.height()
        scale = max(rect.width() / sw, rect.height() / sh)
        cw, ch = rect.width() / scale, rect.height() / scale
        p.drawPixmap(rect, tiny, QRectF((sw - cw) / 2, (sh - ch) / 2, cw, ch))
    elif game is not None:
        hue = 240 + int(hashlib.md5(game.name.encode()).hexdigest()[:4], 16) % 80
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, QColor.fromHsl(hue, 120, 70))
        grad.setColorAt(1, QColor.fromHsl((hue + 40) % 360, 140, 30))
        p.fillRect(rect, grad)
    p.restore()


PROFILE_FILE = DATA_DIR / "profile.json"
AVATAR_FILE = DATA_DIR / "avatar.png"

# (id, name, description, points, stat, target)
ACHIEVEMENTS = [
    ("first_steps", "First Steps", "Launch your first game", 10, "launches", 1),
    ("regular", "Regular", "Launch games 25 times", 20, "launches", 25),
    ("frequent", "Frequent Flyer", "Launch games 100 times", 40, "launches", 100),
    ("collector_10", "Collector", "Have 10 games in your library", 15, "games", 10),
    ("collector_50", "Curator", "Have 50 games in your library", 30, "games", 50),
    ("collector_100", "Hoarder", "Have 100 games in your library", 50, "games", 100),
    ("hours_1", "Warming Up", "Play for 1 hour in total", 10, "hours", 1),
    ("hours_10", "Getting Hooked", "Play for 10 hours in total", 20, "hours", 10),
    ("hours_50", "Dedicated", "Play for 50 hours in total", 40, "hours", 50),
    ("hours_100", "Centurion", "Play for 100 hours in total", 75, "hours", 100),
    ("hours_500", "Marathoner", "Play for 500 hours in total", 150, "hours", 500),
    ("fav_1", "Favourite Things", "Favourite a game", 5, "favourites", 1),
    ("fav_10", "Top Ten", "Favourite 10 games", 20, "favourites", 10),
    ("folder_1", "Organised", "Create a folder", 10, "folders", 1),
    ("folder_5", "Filing Cabinet", "Create 5 folders", 25, "folders", 5),
    ("finished_1", "Credits Rolled", "Mark a game as Finished", 15, "finished", 1),
    ("finished_10", "Completionist", "Mark 10 games as Finished", 50, "finished", 10),
    ("notes_1", "Note to Self", "Write a note on a game page", 10, "notes", 1),
    ("stores_3", "Multi-Store", "Have games from 3 different stores", 20, "stores", 3),
    ("art_1", "Artist", "Set your own cover image on a game", 10, "custom_covers", 1),
    ("bio", "About Me", "Write your profile bio", 5, "bio_set", 1),
    ("bp_1", "Big Picture", "Open Big Picture mode", 10, "bp_opens", 1),
    ("yt_1", "Couch Mode", "Open YouTube from Big Picture", 10, "yt_opens", 1),
    ("accent_1", "Make It Yours", "Change the accent colour", 5, "accent_changes", 1),
    ("streak_3", "On a Roll", "Play 3 days in a row", 20, "streak", 3),
    ("streak_7", "Week Warrior", "Play 7 days in a row", 50, "streak", 7),
    ("night_owl", "Night Owl", "Launch a game between midnight and 5 am", 15, "night_launches", 1),
    ("master", "Nexus Master", "Unlock 20 achievements", 100, "unlocked", 20),
]
AVATAR_COLORS = ["", "#ef4444", "#f59e0b", "#10b981", "#14b8a6", "#3b82f6", "#d946ef", "#64748b"]   # '' = accent


# ---- weekly goals (4 per week, picked from this pool by week number so they vary)
# (id, title, stat, target, reward G, tier)
GOAL_DEFS = [
    ("diff3", "Play 3 different games", "games_played", 3, 10, "variety"),
    ("diff5", "Play 5 different games", "games_played", 5, 15, "variety"),
    ("hours3", "Play for 3 hours", "hours", 3, 10, "time"),
    ("hours8", "Play for 8 hours", "hours", 8, 15, "time"),
    ("days3", "Play on 3 different days", "days_active", 3, 10, "habit"),
    ("days5", "Play on 5 different days", "days_active", 5, 15, "habit"),
    ("launch6", "Launch games 6 times", "launches", 6, 10, "action"),
    ("new1", "Play a game for the first time", "new_games", 1, 10, "action"),
    ("fav2", "Favourite 2 games", "favs", 2, 10, "action"),
]
GOAL_BY_ID = {g[0]: g for g in GOAL_DEFS}
GOAL_ALL_BONUS = 20
LEVEL_TITLES = [(1, "Newcomer"), (3, "Rising Star"), (5, "Veteran"), (8, "Legend"), (12, "Mythic")]


def _iso(d):
    return d.isoformat()


def week_monday(today=None):
    d = datetime.date.fromtimestamp(today or time.time())
    return d - datetime.timedelta(days=d.weekday())


class Profile:
    """Your Nexus Grid profile: name, bio, avatar, titles, showcase, achievements, goals and activity (Data/profile.json)."""

    def __init__(self):
        self.name = os.environ.get("USERNAME") or os.environ.get("USER") or "Player"
        self.bio, self.avatar_color, self.has_avatar, self.title = "", "", False, "lvl:1"
        self.created = time.time()
        self.unlocked, self.counters, self.play_days = {}, {}, []
        self.showcase_games, self.showcase_ach = [], []
        self.playlog, self.daily, self.activity = {}, {}, []      # per-day play seconds by game / counters / feed
        self.goal_done, self.known_ids, self.known_init = {}, [], False
        self.longest_session, self.tracking_since = 0.0, None
        self._dirty, self._last_save = False, 0.0
        self.fresh = not PROFILE_FILE.exists()
        self.load()

    # ---- storage
    def load(self):
        try:
            raw = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        self.name = str(raw.get("name") or self.name)
        self.bio = str(raw.get("bio") or "")
        self.avatar_color = str(raw.get("avatar_color") or "")
        self.has_avatar = bool(raw.get("has_avatar")) and AVATAR_FILE.exists()
        self.title = str(raw.get("title") or "lvl:1")
        self.created = float(raw.get("created") or self.created)
        self.unlocked = {str(k): float(v) for k, v in (raw.get("unlocked") or {}).items()}
        self.counters = {str(k): int(v) for k, v in (raw.get("counters") or {}).items()}
        self.play_days = [str(d) for d in (raw.get("play_days") or [])]
        self.showcase_games = [str(x) for x in (raw.get("showcase_games") or [])][:5]
        self.showcase_ach = [str(x) for x in (raw.get("showcase_ach") or [])][:3]
        self.playlog = {str(d): {str(g): float(s) for g, s in v.items()} for d, v in (raw.get("playlog") or {}).items()}
        self.daily = {str(d): {str(k): int(n) for k, n in v.items()} for d, v in (raw.get("daily") or {}).items()}
        self.activity = [e for e in (raw.get("activity") or []) if isinstance(e, dict)][-300:]
        self.goal_done = {str(w): [str(x) for x in v] for w, v in (raw.get("goal_done") or {}).items()}
        self.known_init = "known_ids" in raw
        self.known_ids = [str(x) for x in (raw.get("known_ids") or [])]
        self.longest_session = float(raw.get("longest_session") or 0)
        self.tracking_since = raw.get("tracking_since")

    def save(self):
        try:
            for d in sorted(self.playlog)[:-800]:
                del self.playlog[d]
            for d in sorted(self.daily)[:-800]:
                del self.daily[d]
            data = {"name": self.name, "bio": self.bio, "avatar_color": self.avatar_color, "has_avatar": self.has_avatar,
                    "title": self.title, "created": self.created, "unlocked": self.unlocked, "counters": self.counters,
                    "play_days": self.play_days[-800:], "showcase_games": self.showcase_games,
                    "showcase_ach": self.showcase_ach, "playlog": self.playlog, "daily": self.daily,
                    "activity": self.activity[-300:], "goal_done": self.goal_done, "known_ids": self.known_ids,
                    "longest_session": self.longest_session, "tracking_since": self.tracking_since}
            tmp = PROFILE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(PROFILE_FILE)
            self._dirty, self._last_save = False, time.time()
        except Exception:
            pass

    def flush(self, force=False):
        """Save if something changed (play time ticks every 10 s, so don't write the file every time)."""
        if self._dirty and (force or time.time() - self._last_save > 60):
            self.save()

    # ---- recording
    def bump(self, key, n=1):
        self.counters[key] = self.counters.get(key, 0) + n
        self.save()

    def add_event(self, kind, text, gid="", now=None):
        self.activity.append({"t": now or time.time(), "k": kind, "x": text, "g": gid})
        del self.activity[:-300]
        self._dirty = True

    def bump_day(self, key, n=1, now=None):
        day = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
        d = self.daily.setdefault(day, {})
        d[key] = d.get(key, 0) + n
        self._dirty = True

    def add_play(self, gid, sec, now=None):
        now = now or time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        d = self.playlog.setdefault(day, {})
        d[gid] = round(d.get(gid, 0.0) + sec, 1)
        if self.tracking_since is None:
            self.tracking_since = now
        self._dirty = True

    def record_launch(self, now=None):
        now = now or time.time()
        lt = time.localtime(now)
        day = time.strftime("%Y-%m-%d", lt)
        if day not in self.play_days:
            self.play_days.append(day)
        if lt.tm_hour < 5:
            self.counters["night_launches"] = self.counters.get("night_launches", 0) + 1
        self.bump_day("launches", 1, now)
        self.save()

    def note_games(self, games):
        """Games that are new since last time (the very first call just remembers what you already have)."""
        real = [g for g in games if g.store not in ("App", "Soon")]
        ids = {g.id for g in real}
        if not self.known_init:
            self.known_init, self.known_ids = True, sorted(ids)
            self._dirty = True
            return []
        known = set(self.known_ids)
        new = [g for g in real if g.id not in known]
        if new:
            self.known_ids = sorted(known | ids)
            self._dirty = True
        return new

    # ---- streaks / stats
    def streak(self, today=None):
        """Consecutive days with a launch, counting back from today (or yesterday)."""
        days = set(self.play_days) | {d for d, v in self.playlog.items() if sum(v.values()) >= 60}
        d = datetime.date.fromtimestamp(today or time.time())
        if d.isoformat() not in days:
            d -= datetime.timedelta(days=1)
        n = 0
        while d.isoformat() in days:
            n += 1
            d -= datetime.timedelta(days=1)
        return n

    def longest_streak(self, year=None):
        days = sorted(set(self.play_days) | {d for d, v in self.playlog.items() if sum(v.values()) >= 60})
        if year:
            days = [d for d in days if d.startswith(str(year))]
        best = run = 0
        prev = None
        for d in days:
            cur = datetime.date.fromisoformat(d)
            run = run + 1 if prev and (cur - prev).days == 1 else 1
            best, prev = max(best, run), cur
        return best

    def stats(self, games, folders):
        real = [g for g in games if g.store not in ("App", "Soon")]
        return {
            "games": len(real),
            "hours": sum(g.playtime_min / 60 + g.tracked_sec / 3600 for g in real),
            "launches": sum(g.launches for g in real),
            "favourites": sum(1 for g in real if g.favourite),
            "folders": len(folders),
            "finished": sum(1 for g in real if g.status == "Finished"),
            "notes": sum(1 for g in real if g.notes.strip()),
            "stores": len({g.store for g in real}),
            "custom_covers": sum(1 for g in real if g.custom_cover),
            "bio_set": 1 if self.bio.strip() else 0,
            "bp_opens": self.counters.get("bp_opens", 0),
            "yt_opens": self.counters.get("yt_opens", 0),
            "accent_changes": self.counters.get("accent_changes", 0),
            "night_launches": self.counters.get("night_launches", 0),
            "streak": self.streak(),
            "unlocked": len(self.unlocked),
        }

    def daily_hours(self, n=14, today=None):
        end = datetime.date.fromtimestamp(today or time.time())
        out = []
        for i in range(n - 1, -1, -1):
            d = (end - datetime.timedelta(days=i)).isoformat()
            out.append((d, sum(self.playlog.get(d, {}).values()) / 3600))
        return out

    def range_stats(self, start, days):
        """Play stats for `days` days from date `start`: hours, per-game seconds, active days, launches, new, favs."""
        per, active, counts = {}, 0, {"launches": 0, "new": 0, "favs": 0}
        for i in range(days):
            d = (start + datetime.timedelta(days=i)).isoformat()
            tot = 0.0
            for gid, sec in self.playlog.get(d, {}).items():
                per[gid] = per.get(gid, 0.0) + sec
                tot += sec
            if tot >= 60 or d in self.play_days:
                active += 1
            for k, v in self.daily.get(d, {}).items():
                if k in counts:
                    counts[k] += v
        return {"hours": sum(per.values()) / 3600, "per": per, "days_active": active,
                "games_played": sum(1 for s in per.values() if s >= 60), "launches": counts["launches"],
                "new_games": counts["new"], "favs": counts["favs"]}

    # ---- weekly goals
    def week_goals(self, today=None):
        monday = week_monday(today)
        n = monday.toordinal() // 7
        pick = [("diff3", "diff5")[n % 2], ("hours3", "hours8")[(n // 2) % 2], ("days3", "days5")[(n // 3) % 2],
                ("launch6", "new1", "fav2")[n % 3]]
        st = self.range_stats(monday, 7)
        done = set(self.goal_done.get(_iso(monday), []))
        goals = []
        for gid in pick:
            g = GOAL_BY_ID[gid]
            prog = st.get(g[2], 0)
            goals.append({"id": gid, "title": g[1], "target": g[3], "reward": g[4], "progress": min(prog, g[3]),
                          "done": gid in done or prog >= g[3]})
        left = 7 - (datetime.date.fromtimestamp(today or time.time()) - monday).days
        return {"monday": monday, "goals": goals, "days_left": left, "all_done": all(x["done"] for x in goals)}

    def evaluate_goals(self, today=None):
        """Record newly completed goals this week. Returns [(title, reward)] (bonus included)."""
        wk = self.week_goals(today)
        key = _iso(wk["monday"])
        done = self.goal_done.setdefault(key, [])
        new = []
        for g in wk["goals"]:
            if g["done"] and g["id"] not in done:
                done.append(g["id"])
                new.append((g["title"], g["reward"]))
        if wk["all_done"] and "all" not in done:
            done.append("all")
            new.append(("All weekly goals complete", GOAL_ALL_BONUS))
        if not done:
            self.goal_done.pop(key, None)
        if new:
            self.save()
        return new

    def goal_points(self):
        pts = 0
        for done in self.goal_done.values():
            for gid in done:
                pts += GOAL_ALL_BONUS if gid == "all" else GOAL_BY_ID[gid][4] if gid in GOAL_BY_ID else 0
        return pts

    def goals_completed(self):
        return sum(1 for done in self.goal_done.values() for gid in done if gid != "all")

    def perfect_weeks(self):
        return sum(1 for done in self.goal_done.values() if "all" in done)

    # ---- achievements / level
    def evaluate(self, games, folders):
        """Unlock whatever is now earned. Returns the newly unlocked achievements (tuples)."""
        new = []
        changed = True
        while changed:
            changed = False
            st = self.stats(games, folders)
            for a in ACHIEVEMENTS:
                if a[0] not in self.unlocked and st.get(a[4], 0) >= a[5]:
                    self.unlocked[a[0]] = time.time()
                    new.append(a)
                    changed = True
        if new or not PROFILE_FILE.exists():
            self.save()
        return new

    def points(self):
        return sum(a[3] for a in ACHIEVEMENTS if a[0] in self.unlocked) + self.goal_points()

    def level_info(self):
        """(level, fraction towards next level, points into this level, points needed for this level)."""
        pts = self.points()
        lvl = int((pts / 25) ** 0.5) + 1
        lo, hi = 25 * (lvl - 1) ** 2, 25 * lvl ** 2
        return lvl, (pts - lo) / (hi - lo), pts - lo, hi - lo

    def progress(self, a, stats):
        return min(stats.get(a[4], 0), a[5])

    # ---- titles
    def titles(self):
        lvl = self.level_info()[0]
        out = [{"id": f"lvl:{n}", "name": name, "how": f"Reach level {n}", "unlocked": lvl >= n} for n, name in LEVEL_TITLES]
        out += [{"id": f"ach:{a[0]}", "name": a[1], "how": a[2], "unlocked": a[0] in self.unlocked} for a in ACHIEVEMENTS]
        out += [{"id": "goal:getter", "name": "Goal Getter", "how": "Complete 10 weekly goals",
                 "unlocked": self.goals_completed() >= 10},
                {"id": "goal:champion", "name": "Weekly Champion", "how": "Finish all goals in 3 weeks",
                 "unlocked": self.perfect_weeks() >= 3}]
        return out

    def title_name(self):
        for t in self.titles():
            if t["id"] == self.title and t["unlocked"]:
                return t["name"]
        return "Newcomer"

    # ---- year in review
    def year_stats(self, year, games):
        by_id = {g.id: g for g in games}
        per, weekday, stores = {}, [0.0] * 7, {}
        total, days = 0.0, 0
        for day, v in self.playlog.items():
            if not day.startswith(str(year)):
                continue
            tot = sum(v.values())
            total += tot
            if tot >= 60:
                days += 1
            weekday[datetime.date.fromisoformat(day).weekday()] += tot
            for gid, sec in v.items():
                per[gid] = per.get(gid, 0.0) + sec
                g = by_id.get(gid)
                if g:
                    stores[g.store] = stores.get(g.store, 0.0) + sec
        top = sorted(((by_id[gid], sec) for gid, sec in per.items() if gid in by_id), key=lambda x: -x[1])
        busiest = max(self.playlog.items(), key=lambda kv: sum(kv[1].values()), default=None) if any(
            d.startswith(str(year)) for d in self.playlog) else None
        launches = sum(v.get("launches", 0) for d, v in self.daily.items() if d.startswith(str(year)))
        ach_year = sum(1 for ts in self.unlocked.values() if time.localtime(ts).tm_year == year)
        return {"hours": total / 3600, "games": len(top), "top": top[:5], "weekday": [h / 3600 for h in weekday],
                "active_days": days, "launches": launches, "achievements": ach_year,
                "longest_streak": self.longest_streak(year), "stores": sorted(stores.items(), key=lambda x: -x[1]),
                "busiest": (busiest[0], sum(busiest[1].values()) / 3600) if busiest and sum(busiest[1].values()) else None}



def _draw_text(p, rect, text, px, color="white", bold=False, align=Qt.AlignmentFlag.AlignLeft, spacing=0):
    p.setFont(_px_font(px, bold, spacing))
    p.setPen(QColor(color) if isinstance(color, str) else color)
    fm = QFontMetrics(p.font())
    p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter,
               fm.elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(rect.width()))))


def _draw_wrapped(p, rect, text, px, color, max_lines=None):
    p.setFont(_px_font(px))
    p.setPen(QColor(color) if isinstance(color, str) else color)
    p.save()
    p.setClipRect(rect)
    p.drawText(rect, Qt.TextFlag.TextWordWrap.value | Qt.AlignmentFlag.AlignTop.value, text)
    p.restore()


def trophy_color(points, unlocked=True):
    if not unlocked:
        return QColor(110, 110, 128)
    return QColor("#f5c542") if points >= 75 else QColor("#d3dae8") if points >= 30 else QColor("#d39a62")


def draw_trophy(p, c, size, color):
    """A little trophy cup (drawn, so no icon fonts needed)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, max(1.6, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(color)
    s = size
    cup = QPainterPath()
    cup.moveTo(c.x() - s * 0.3, c.y() - s * 0.38)
    cup.lineTo(c.x() + s * 0.3, c.y() - s * 0.38)
    cup.lineTo(c.x() + s * 0.22, c.y() + s * 0.06)
    cup.quadTo(c.x(), c.y() + s * 0.26, c.x() - s * 0.22, c.y() + s * 0.06)
    cup.closeSubpath()
    p.drawPath(cup)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for sign in (-1, 1):                                  # handles
        h = QPainterPath()
        h.moveTo(c.x() + sign * s * 0.3, c.y() - s * 0.3)
        h.cubicTo(c.x() + sign * s * 0.52, c.y() - s * 0.3, c.x() + sign * s * 0.5, c.y() - s * 0.02,
                  c.x() + sign * s * 0.24, c.y() + s * 0.0)
        p.drawPath(h)
    p.drawLine(QPointF(c.x(), c.y() + s * 0.18), QPointF(c.x(), c.y() + s * 0.36))
    p.drawLine(QPointF(c.x() - s * 0.2, c.y() + s * 0.38), QPointF(c.x() + s * 0.2, c.y() + s * 0.38))
    p.restore()


def paint_avatar(p, rect, win):
    """Round avatar: your picture if you chose one, otherwise a coloured circle with your initial."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    pm = win.avatar_pixmap()
    if pm is not None:
        path = QPainterPath()
        path.addEllipse(rect)
        p.setClipPath(path)
        p.drawPixmap(rect, pm, QRectF(pm.rect()))
    else:
        col = QColor(win.profile.avatar_color or ACCENT)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, col.lighter(130))
        grad.setColorAt(1, col.darker(150))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(rect)
        p.setFont(_px_font(rect.height() * 0.5, True))
        p.setPen(QColor("white"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, (win.profile.name[:1] or "P").upper())
    p.restore()


class ToastWidget(QWidget):
    """Xbox-style 'Achievement unlocked' pop-up, bottom-left, queued."""
    W, H = 560, 96

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.resize(self.W, self.H)
        self.queue, self.cur, self.t = [], None, 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(4600)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self._next)
        self.hide()

    def push(self, title, sub, points):
        self.queue.append((title, sub, points))
        if self.cur is None:
            self._next()

    def _next(self):
        if not self.queue:
            self.cur = None
            self.hide()
            return
        self.cur = self.queue.pop(0)
        self.t = 0.0
        self._place()
        self.show()
        self.raise_()
        self.anim.start()

    def _place(self):
        pw, ph = self.parentWidget().width(), self.parentWidget().height()
        t = self.t
        slide = _ease_out_cubic(_clamp(t / 0.08)) if t < 0.9 else 1 - _ease_out_cubic(_clamp((t - 0.9) / 0.1))
        self.move(int(pw * 0.03), int(ph - self.H - 34 + (1 - slide) * (self.H + 60)))

    def _tick(self, v):
        self.t = float(v)
        self._place()
        self.update()

    def paintEvent(self, _):
        if self.cur is None:
            return
        title, sub, pts = self.cur
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        r = QRectF(2, 2, self.W - 4, self.H - 4)
        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        bg.setColorAt(0, QColor(30, 26, 42, 248))
        bg.setColorAt(1, QColor(16, 14, 24, 250))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1.2))
        p.setBrush(bg)
        p.drawRoundedRect(r, 20, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(r.left() + 1, r.top() + 14, 5, r.height() - 28), 2.5, 2.5)
        ic = QRectF(r.left() + 22, r.top() + 14, 68, 68)
        col = trophy_color(pts if pts else 80)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawEllipse(ic)
        draw_trophy(p, ic.center(), 46, col)
        _draw_text(p, QRectF(ic.right() + 18, r.top() + 10, 330, 22), "ACHIEVEMENT UNLOCKED", 13,
                   QColor(ACCENT).lighter(150), True, spacing=2)
        _draw_text(p, QRectF(ic.right() + 18, r.top() + 32, 340, 30), title, 24, "white", True)
        _draw_text(p, QRectF(ic.right() + 18, r.top() + 62, 340, 22), sub, 15, QColor(255, 255, 255, 160))
        if pts:
            _draw_text(p, QRectF(r.right() - 120, r.top() + 24, 100, 48), f"+{pts} G", 26, QColor("#f5c542"), True,
                       Qt.AlignmentFlag.AlignRight)
        p.end()


class OskOverlay(QWidget):
    """Full-window text entry with an on-screen keyboard (controller, mouse or physical keyboard)."""
    WIDE = {"SPACE": 4.0, "DONE": 2.2, "\u21b5": 1.5, "?123": 1.7, "abc": 1.7, "\u21e7": 1.5, "\u232b": 1.5}

    def __init__(self, parent):
        super().__init__(parent)
        self.active = False
        self.title, self.text, self.cb, self.multiline, self.maxlen = "", "", None, False, 200
        self.layer, self.shift, self.sel, self._caret = 0, False, [1, 0], True
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._blink = QTimer(self)
        self._blink.setInterval(520)
        self._blink.timeout.connect(self._toggle_caret)
        self.hide()

    def _toggle_caret(self):
        self._caret = not self._caret
        self.update()

    def rows(self):
        end = ["?123" if not self.layer else "abc", ",", "SPACE", "."] + (["\u21b5"] if self.multiline else []) + ["DONE"]
        if self.layer:
            return [list("1234567890"), list("!@#$%^&*()"), list("-_=+/:;\"'?"), list("~`|<>[]{}") + ["\u232b"], end]
        return [list("1234567890"), list("qwertyuiop"), list("asdfghjkl'"), ["\u21e7"] + list("zxcvbnm") + ["\u232b"], end]

    def ask(self, title, initial="", callback=None, multiline=False, maxlen=200):
        self.title, self.text, self.cb = title, initial or "", callback
        self.multiline, self.maxlen = multiline, maxlen
        self.layer, self.shift, self.sel, self._caret = 0, False, [1, 0], True
        self.active = True
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._blink.start()

    def finish(self, ok):
        cb, text = self.cb, self.text
        self.active = False
        self._blink.stop()
        self.hide()
        self.cb = None
        self.parentWidget().focus_grid()
        if cb:
            cb(text if ok else None)

    def _panel(self):
        W, H = self.width(), self.height()
        w, h = min(W * 0.74, 1320), min(H * 0.7, 700)
        return QRectF((W - w) / 2, (H - h) / 2, w, h)

    def _layout(self):
        pr = self._panel()
        pad, gap = pr.width() * 0.025, 8.0
        top = pr.top() + pr.height() * (0.37 if self.multiline else 0.3)
        rows = self.rows()
        unit = min((pr.width() - 2 * pad - gap * (len(r) - 1)) / sum(self.WIDE.get(t, 1.0) for t in r) for r in rows)
        kh = (pr.bottom() - pad - top - gap * (len(rows) - 1)) / len(rows)
        out = []
        for ri, r in enumerate(rows):
            total = sum(self.WIDE.get(t, 1.0) * unit for t in r) + gap * (len(r) - 1)
            x, y, rects = pr.center().x() - total / 2, top + ri * (kh + gap), []
            for t in r:
                w = self.WIDE.get(t, 1.0) * unit
                rects.append(QRectF(x, y, w, kh))
                x += w + gap
            out.append(rects)
        return out

    def label(self, tok):
        if tok == "SPACE":
            return "space"
        if tok == "DONE":
            return "Done"
        if len(tok) == 1 and tok.isalpha() and self.shift:
            return tok.upper()
        return tok

    # ---- editing
    def _add(self, s):
        if len(self.text) + len(s) <= self.maxlen:
            self.text += s
        self.update()

    def _back(self):
        self.text = self.text[:-1]
        self.update()

    def move(self, dx, dy):
        rows, lay = self.rows(), self._layout()
        r, c = self.sel
        r = min(r, len(rows) - 1)
        c = min(c, len(rows[r]) - 1)
        if dx:
            c = (c + dx) % len(rows[r])
        if dy:
            cx = lay[r][c].center().x()
            r = max(0, min(len(rows) - 1, r + dy))
            c = min(range(len(rows[r])), key=lambda i: abs(lay[r][i].center().x() - cx))
        self.sel = [r, c]
        self.update()

    def press(self):
        rows = self.rows()
        r = min(self.sel[0], len(rows) - 1)
        c = min(self.sel[1], len(rows[r]) - 1)
        tok = rows[r][c]
        if tok == "SPACE":
            self._add(" ")
        elif tok == "DONE":
            self.finish(True)
            return
        elif tok == "\u21b5":
            self._add("\n")
        elif tok == "\u232b":
            self._back()
        elif tok == "\u21e7":
            self.shift = not self.shift
        elif tok in ("?123", "abc"):
            self.layer = 1 - self.layer
            self.sel = [min(r, 4), 0]
        else:
            self._add(tok.upper() if (self.shift and tok.isalpha()) else tok)
            if self.shift and tok.isalpha():
                self.shift = False
        self.update()

    def pad(self, fire, d, pressed):
        if fire:
            self.move(d[0], d[1])
        if pressed & BTN_A:
            self.press()
        if pressed & BTN_X:
            self._back()
        if pressed & BTN_Y:
            self.shift = not self.shift
            self.update()
        if pressed & BTN_START:
            self.finish(True)
        elif pressed & BTN_B:
            self.finish(False)

    def keyPressEvent(self, e):
        k = e.key()
        K = Qt.Key
        if k == K.Key_Escape:
            self.finish(False)
        elif k in (K.Key_Return, K.Key_Enter):
            if self.multiline and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._add("\n")
            else:
                self.finish(True)
        elif k == K.Key_Backspace:
            self._back()
        elif e.text() and e.text().isprintable():
            self._add(e.text())
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        pt = e.position()
        if not self._panel().contains(pt):
            self.finish(False)
            return
        for ri, rects in enumerate(self._layout()):
            for ci, rc in enumerate(rects):
                if rc.contains(pt):
                    self.sel = [ri, ci]
                    self.press()
                    return

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        W, H = self.width(), self.height()
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, 185))
        pr = self._panel()
        p.setPen(QPen(QColor(255, 255, 255, 40), 1.2))
        p.setBrush(QColor(18, 16, 28, 248))
        p.drawRoundedRect(pr, 22, 22)
        pad = pr.width() * 0.025
        _draw_text(p, QRectF(pr.left() + pad, pr.top() + pad * 0.6, pr.width() * 0.6, pr.height() * 0.09), self.title,
                   pr.height() * 0.05, "white", True)
        hint = "A type   X delete   Y shift   Start done   B cancel"
        _draw_text(p, QRectF(pr.right() - pr.width() * 0.5 - pad, pr.top() + pad * 0.6, pr.width() * 0.5, pr.height() * 0.09),
                   hint, pr.height() * 0.028, QColor(255, 255, 255, 120), False, Qt.AlignmentFlag.AlignRight)
        fh = pr.height() * (0.24 if self.multiline else 0.13)
        field = QRectF(pr.left() + pad, pr.top() + pr.height() * 0.13, pr.width() - 2 * pad, fh)
        p.setPen(QPen(QColor(ACCENT), 2))
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(field, 12, 12)
        shown = self.text + ("\u2502" if self._caret else " ")
        px = pr.height() * (0.046 if self.multiline else 0.058)
        if self.multiline:
            _draw_wrapped(p, field.adjusted(14, 10, -14, -8), shown, px, "white")
        else:
            p.setFont(_px_font(px))
            fm = QFontMetrics(p.font())
            p.setPen(QColor("white"))
            p.drawText(field.adjusted(16, 0, -16, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(shown, Qt.TextElideMode.ElideLeft, int(field.width() - 32)))
        count = f"{len(self.text)}/{self.maxlen}"
        _draw_text(p, QRectF(field.right() - 140, field.bottom() + 2, 130, 20), count, pr.height() * 0.026,
                   QColor(255, 255, 255, 110), False, Qt.AlignmentFlag.AlignRight)
        rows, lay = self.rows(), self._layout()
        kf = _px_font(pr.height() * 0.058, True)
        for ri, rects in enumerate(lay):
            for ci, rc in enumerate(rects):
                tok = rows[ri][ci]
                sel = self.sel == [ri, min(ci, len(rects) - 1)] and self.sel[0] == ri
                p.setPen(Qt.PenStyle.NoPen)
                if sel:
                    glow = QColor(ACCENT)
                    glow.setAlpha(90)
                    p.setBrush(glow)
                    p.drawRoundedRect(rc.adjusted(-4, -4, 4, 4), 12, 12)
                    p.setBrush(QColor(ACCENT))
                elif tok == "DONE":
                    p.setBrush(QColor(ACCENT).darker(160))
                else:
                    p.setBrush(QColor(255, 255, 255, 40 if tok not in self.WIDE else 26))
                if tok == "\u21e7" and self.shift:
                    p.setBrush(QColor("#c4b5fd"))
                p.drawRoundedRect(rc, 10, 10)
                p.setFont(kf)
                p.setPen(QColor("white"))
                p.drawText(rc, Qt.AlignmentFlag.AlignCenter, self.label(tok))
        p.end()


class _Scroll:
    """An animated scroll offset for a list."""

    def __init__(self, widget):
        self.v, self.w = 0.0, widget
        self.anim = QVariantAnimation(widget)
        self.anim.setDuration(240)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._set)

    def _set(self, v):
        self.v = float(v)
        self.w.update()

    def reset(self):
        self.anim.stop()
        self.v = 0.0

    def to(self, target, animate=True):
        target = max(0.0, target)
        if animate:
            self.anim.stop()
            self.anim.setStartValue(self.v)
            self.anim.setEndValue(target)
            self.anim.start()
        else:
            self.anim.stop()
            self.v = target

    def follow(self, y, h, view_h, total, animate=True):
        """Scroll just enough to keep the row at [y, y+h) visible."""
        t = self.v
        if y < self.v:
            t = y
        elif y + h > self.v + view_h:
            t = y + h - view_h
        self.to(min(t, max(0.0, total - view_h)), animate)


class ListPicker(QWidget):
    """Full-window list chooser (controller, mouse or keyboard): pick a game, an achievement, ..."""
    CLEAR = object()

    def __init__(self, parent):
        super().__init__(parent)
        self.active, self.title, self.items, self.cb = False, "", [], None
        self.sel, self.allow_clear = 0, False
        self.scroll = _Scroll(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

    def ask(self, title, items, callback, allow_clear=False, current=None):
        self.title, self.items, self.cb, self.allow_clear = title, items, callback, allow_clear
        self.sel = next((i for i, it in enumerate(items) if it[2] == current), 0)
        self.scroll.reset()
        self.active = True
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._follow(False)

    def finish(self, value=None, chosen=True):
        cb = self.cb
        self.active = False
        self.hide()
        self.cb = None
        self.parentWidget().focus_grid()
        if chosen and cb:
            cb(value)

    def _geom(self):
        W, H = self.width(), self.height()
        w, h = min(W * 0.5, 860), H * 0.82
        panel = QRectF((W - w) / 2, (H - h) / 2, w, h)
        return panel, H * 0.06, panel.top() + H * 0.12, panel.bottom() - H * 0.07

    def _follow(self, animate=True):
        panel, rh, top, bottom = self._geom()
        self.scroll.follow(self.sel * rh, rh, bottom - top, len(self.items) * rh, animate)

    def move(self, dy):
        if self.items:
            self.sel = max(0, min(len(self.items) - 1, self.sel + dy))
            self._follow()
            self.update()

    def choose(self):
        if self.items:
            self.finish(self.items[self.sel][2])

    def pad(self, fire, d, pressed):
        if fire and d[1]:
            self.move(d[1])
        if pressed & BTN_LB:
            self.move(-8)
        if pressed & BTN_RB:
            self.move(8)
        if pressed & BTN_A:
            self.choose()
        if pressed & BTN_Y and self.allow_clear:
            self.finish(ListPicker.CLEAR)
        if pressed & BTN_B:
            self.finish(chosen=False)

    def keyPressEvent(self, e):
        K, k = Qt.Key, e.key()
        if k == K.Key_Up:
            self.move(-1)
        elif k == K.Key_Down:
            self.move(1)
        elif k == K.Key_PageUp:
            self.move(-8)
        elif k == K.Key_PageDown:
            self.move(8)
        elif k in (K.Key_Return, K.Key_Enter):
            self.choose()
        elif k == K.Key_Delete and self.allow_clear:
            self.finish(ListPicker.CLEAR)
        elif k == K.Key_Escape:
            self.finish(chosen=False)
        elif e.text() and e.text().isalpha():                         # type a letter to jump
            ch = e.text().lower()
            order = list(range(self.sel + 1, len(self.items))) + list(range(0, self.sel + 1))
            for i in order:
                if self.items[i][0].lower().startswith(ch):
                    self.sel = i
                    self._follow()
                    self.update()
                    break
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        panel, rh, top, bottom = self._geom()
        pt = e.position()
        if not panel.contains(pt):
            self.finish(chosen=False)
            return
        if top <= pt.y() <= bottom:
            i = int((pt.y() - top + self.scroll.v) // rh)
            if 0 <= i < len(self.items):
                self.sel = i
                self.choose()

    def wheelEvent(self, e):
        self.move(-1 if e.angleDelta().y() > 0 else 1)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        W, H = self.width(), self.height()
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, 185))
        panel, rh, top, bottom = self._geom()
        p.setPen(QPen(QColor(255, 255, 255, 40), 1.2))
        p.setBrush(QColor(18, 16, 28, 248))
        p.drawRoundedRect(panel, 20, 20)
        pad = panel.width() * 0.04
        _draw_text(p, QRectF(panel.left() + pad, panel.top() + H * 0.025, panel.width() - 2 * pad, H * 0.06), self.title,
                   H * 0.032, "white", True)
        p.save()
        p.setClipRect(QRectF(panel.left(), top - 2, panel.width(), bottom - top + 4))
        for i, (label, sub, value) in enumerate(self.items):
            y = top + i * rh - self.scroll.v
            if y > bottom or y + rh < top:
                continue
            r = QRectF(panel.left() + pad * 0.6, y, panel.width() - pad * 1.2, rh * 0.92)
            if i == self.sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 34))
                p.drawRoundedRect(r, 10, 10)
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(QRectF(r.left(), r.top() + r.height() * 0.2, 4, r.height() * 0.6), 2, 2)
            _draw_text(p, QRectF(r.left() + pad * 0.6, r.top(), r.width() * 0.6, r.height()), label, H * 0.0235,
                       "white" if i == self.sel else QColor(255, 255, 255, 210), i == self.sel)
            _draw_text(p, QRectF(r.left() + r.width() * 0.55, r.top(), r.width() * 0.43, r.height()), sub, H * 0.0165,
                       QColor(255, 255, 255, 140), False, Qt.AlignmentFlag.AlignRight)
        p.restore()
        if not self.items:
            _draw_text(p, QRectF(panel.left(), panel.center().y() - 20, panel.width(), 40), "Nothing to choose from yet.",
                       H * 0.024, QColor(255, 255, 255, 150), False, Qt.AlignmentFlag.AlignHCenter)
        hint = "A choose   B cancel" + ("   Y remove" if self.allow_clear else "") + "   LB RB jump"
        _draw_text(p, QRectF(panel.left() + pad, panel.bottom() - H * 0.055, panel.width() - 2 * pad, H * 0.04), hint,
                   H * 0.0165, QColor(255, 255, 255, 130))
        p.end()


class ProfilePage(QWidget):
    """Your profile: avatar, name + title, G score and level, bio, then tabs: Overview, Achievements, Activity,
    Stats, Badges & titles, Showcase, Goals and Year in Review."""
    LEFT_BUTTONS = 4
    ACT_STYLE = {"session": ("\u25B6", "#3b82f6"), "achievement": ("\u2605", "#f5c542"), "added": ("+", "#22c55e"),
                 "favourite": ("\u2665", "#ec4899"), "finished": ("\u2713", "#22c55e"), "level": ("\u25B2", "#8b5cf6"),
                 "goal": ("\u25CE", "#14b8a6"), "first": ("\u25C6", "#f59e0b")}

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.tab, self.zone, self.left_i = 0, "left", 0
        self.ov_i, self.ach_i, self.act_i, self.title_i, self.sc_i = 0, 0, 0, 0, 0
        self.ach_sc, self.act_sc, self.title_sc = _Scroll(self), _Scroll(self), _Scroll(self)
        self.stats, self.top_games, self.ov_games, self.ov_header = {}, [], [], "MOST PLAYED"
        self.recent, self.rows, self.act_rows, self.act_events = [], [], [], []
        self.titles, self.goals, self.year, self.daily, self._tiny = [], {}, {}, [], None
        self._hits = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        win.thumbs.changed.connect(self._thumbs_changed)

    def _kind(self):
        return PROFILE_TABS[self.tab][1]

    def _thumbs_changed(self):
        if self.isVisible():
            self._tiny = None
            self.update()

    # ---- data
    def show_profile(self):
        self.tab, self.zone, self.left_i = 0, "left", 0
        self.ov_i = self.ach_i = self.act_i = self.title_i = self.sc_i = 0
        for sc in (self.ach_sc, self.act_sc, self.title_sc):
            sc.reset()
        self.refresh()

    @staticmethod
    def _day_label(lt):
        today = time.localtime()
        if (lt.tm_year, lt.tm_yday) == (today.tm_year, today.tm_yday):
            return "Today"
        y = time.localtime(time.time() - 86400)
        if (lt.tm_year, lt.tm_yday) == (y.tm_year, y.tm_yday):
            return "Yesterday"
        return time.strftime("%a %d %b", lt)

    def refresh(self):
        w = self.win
        pr = w.profile
        self.stats = pr.stats(w.model.games, w.model.folders)
        real = [g for g in w.model.games if g.store not in ("App", "Soon")]
        by_id = {g.id: g for g in real}
        by_time = sorted(real, key=lambda g: (g.playtime_min / 60 + g.tracked_sec / 3600, g.launches), reverse=True)
        self.top_games = [g for g in by_time if (g.playtime_min or g.tracked_sec or g.launches)][:5]
        pinned = [by_id[i] for i in pr.showcase_games if i in by_id]
        self.ov_games, self.ov_header = (pinned, "SHOWCASE") if pinned else (self.top_games, "MOST PLAYED")
        self.ov_i = min(self.ov_i, max(0, len(self.ov_games) - 1))
        self.recent = sorted(((a, ts) for a in ACHIEVEMENTS for ts in [pr.unlocked.get(a[0])] if ts),
                             key=lambda x: x[1], reverse=True)[:3]
        rows = []
        for a in ACHIEVEMENTS:
            ts = pr.unlocked.get(a[0])
            prog = pr.progress(a, self.stats)
            rows.append((a, ts, prog, prog / a[5]))
        rows.sort(key=lambda r: (0, -r[1]) if r[1] else (1, -r[3]))
        self.rows = rows
        self.act_events = list(reversed(pr.activity))
        arows, last = [], None
        for e in self.act_events:
            lt = time.localtime(e.get("t", 0))
            day = time.strftime("%Y-%m-%d", lt)
            if day != last:
                arows.append(("head", self._day_label(lt)))
                last = day
            arows.append(("ev", e))
        self.act_rows = arows
        self.act_i = min(self.act_i, max(0, len(self.act_events) - 1))
        self.titles = pr.titles()
        self.title_i = min(self.title_i, len(self.titles) - 1)
        self.goals = pr.week_goals()
        self.year = pr.year_stats(time.localtime().tm_year, w.model.games)
        self.daily = pr.daily_hours(14)
        self._tiny = None
        self.update()

    def left_labels(self):
        return ["Edit name", "Edit bio", "Avatar colour",
                "Remove picture" if self.win.profile.has_avatar else "Choose picture\u2026"]

    # ---- navigation
    def move(self, dx, dy):
        k = self._kind()
        if self.zone == "left":
            if dy:
                self.left_i = max(0, min(self.LEFT_BUTTONS - 1, self.left_i + dy))
            elif dx > 0:
                self.zone = "content"
        else:
            if k == "overview":
                if dx < 0 and self.ov_i == 0:
                    self.zone = "left"
                elif dx:
                    self.ov_i = max(0, min(max(0, len(self.ov_games) - 1), self.ov_i + dx))
            elif k == "showcase":
                self._showcase_move(dx, dy)
            elif dx < 0:
                self.zone = "left"
            elif dy and k == "achievements":
                self.ach_i = max(0, min(len(self.rows) - 1, self.ach_i + dy))
                H = self.height()
                self.ach_sc.follow(self.ach_i * H * 0.098, H * 0.088, H * 0.72, len(self.rows) * H * 0.098)
            elif dy and k == "activity":
                self.act_i = max(0, min(len(self.act_events) - 1, self.act_i + dy))
                self._follow_activity()
            elif dy and k == "badges":
                self.title_i = max(0, min(len(self.titles) - 1, self.title_i + dy))
                H = self.height()
                self.title_sc.follow(self.title_i * H * 0.058, H * 0.054, H * 0.73, len(self.titles) * H * 0.058)
        self.update()

    def _showcase_move(self, dx, dy):
        i = self.sc_i
        if dy > 0 and i < 5:
            self.sc_i = 5 + min(2, i * 3 // 5)
        elif dy < 0 and i >= 5:
            self.sc_i = (i - 5) * 2
        elif dx:
            lo, hi = (0, 4) if i < 5 else (5, 7)
            if dx < 0 and i == lo:
                self.zone = "left"
            else:
                self.sc_i = max(lo, min(hi, i + dx))

    def _act_geom(self, H):
        out, y, n = [], 0.0, -1
        for kind, data in self.act_rows:
            h = H * 0.045 if kind == "head" else H * 0.066
            if kind == "ev":
                n += 1
            out.append((kind, data, y, h, n))
            y += h + (0 if kind == "head" else H * 0.008)
        return out, y

    def _follow_activity(self, animate=True):
        H = self.height()
        geo, total = self._act_geom(H)
        for kind, data, y, h, n in geo:
            if kind == "ev" and n == self.act_i:
                self.act_sc.follow(y, h, H * 0.75, total, animate)
                return

    def shoulder(self, n):
        self.tab = (self.tab + n) % len(PROFILE_TABS)
        self.ov_i = self.ach_i = self.act_i = self.title_i = 0
        for sc in (self.ach_sc, self.act_sc, self.title_sc):
            sc.reset()
        if self.zone == "content" and self._kind() in ("stats", "goals", "year"):
            pass
        self.update()

    def back(self):
        self.win.close_profile()

    def activate(self):
        k = self._kind()
        if self.zone == "left":
            self._do_left(self.left_i)
        elif k == "overview" and self.ov_games:
            self.win.open_game_page(self.ov_games[min(self.ov_i, len(self.ov_games) - 1)])
        elif k == "badges" and self.titles:
            t = self.titles[self.title_i]
            if t["unlocked"]:
                self.win.profile.title = t["id"]
                self.win.profile.save()
            else:
                self.win.status.setText(f"Locked \u2013 {t['how']}")
            self.update()
        elif k == "showcase":
            self._pick_slot(self.sc_i)

    def tertiary(self):                                   # X: clear a showcase slot
        if self.zone == "content" and self._kind() == "showcase":
            self._set_slot(self.sc_i, ListPicker.CLEAR)

    # ---- showcase
    def _slot_list(self, j):
        pr = self.win.profile
        return (pr.showcase_games, j) if j < 5 else (pr.showcase_ach, j - 5)

    def _pick_slot(self, j):
        w, pr = self.win, self.win.profile
        lst, idx = self._slot_list(j)
        cur = lst[idx] if idx < len(lst) else None
        if j < 5:
            games = sorted((g for g in w.model.games if g.store not in ("App", "Soon")), key=lambda g: g.name.lower())
            items = [(g.name, f"{g.store}  \u2022  {g.playtime_min / 60 + g.tracked_sec / 3600:.1f} h", g.id) for g in games]
            w.pick_from("Pin a game to your profile", items, lambda v: self._set_slot(j, v), bool(cur), cur)
        else:
            items = [(a[1], a[2], a[0]) for a in ACHIEVEMENTS if a[0] in pr.unlocked]
            if not items:
                w.status.setText("Unlock an achievement first, then pin it here.")
                return
            w.pick_from("Pin an achievement to your profile", items, lambda v: self._set_slot(j, v), bool(cur), cur)

    def _set_slot(self, j, value):
        pr = self.win.profile
        lst, idx = self._slot_list(j)
        if value is ListPicker.CLEAR:
            if idx < len(lst):
                del lst[idx]
        elif value:
            if value in lst:
                lst.remove(value)
            if idx < len(lst):
                lst[idx] = value
            else:
                lst.append(value)
        pr.save()
        self.refresh()

    # ---- painting
    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W <= 0 or H <= 0:
            return
        self._hits = []
        w = self.win
        pr = w.profile
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        full = QRectF(0, 0, W, H)
        p.fillRect(full, QColor("#0a0714"))
        if self._tiny is None and self.top_games:
            self._tiny = make_backdrop(w.thumbs, self.top_games[0])
        if self._tiny is not None:
            paint_backdrop(p, full, self._tiny, 0.8)
        else:
            g = QLinearGradient(0, 0, W, H)
            g.setColorAt(0, QColor(ACCENT).darker(220))
            g.setColorAt(1, QColor("#0a0714"))
            p.fillRect(full, g)
        shade = QLinearGradient(0, 0, W, 0)
        shade.setColorAt(0, QColor(8, 5, 18, 235))
        shade.setColorAt(0.5, QColor(8, 5, 18, 190))
        shade.setColorAt(1, QColor(8, 5, 18, 150))
        p.fillRect(full, shade)
        self._paint_left(p, W, H, pr)
        self._paint_right(p, W, H, pr)
        self._paint_hints(p, W, H)
        p.end()

    def _paint_right(self, p, W, H, pr):
        rx, mx = W * 0.36, W * 0.05
        rw = W - rx - mx
        xs, x = [], rx
        for i, (name, kind) in enumerate(PROFILE_TABS):
            label = name + (f"  {len(pr.unlocked)}/{len(ACHIEVEMENTS)}" if kind == "achievements" else "")
            p.setFont(_px_font(H * 0.0225, True))
            tw = QFontMetrics(p.font()).horizontalAdvance(label)
            xs.append((x, tw, label))
            x += tw + W * 0.022
        sel_x, sel_w, _ = xs[self.tab]
        shift = max(0.0, sel_x + sel_w - (rx + rw))
        p.save()
        p.setClipRect(QRectF(rx - 4, H * 0.06, rw + 8, H * 0.08))
        for i, (tx, tw, label) in enumerate(xs):
            sel = self.tab == i
            _draw_text(p, QRectF(tx - shift, H * 0.07, tw + 10, H * 0.05), label, H * 0.0225,
                       "white" if sel else QColor(255, 255, 255, 125), True)
            if sel:
                p.fillRect(QRectF(tx - shift, H * 0.123, tw, 3), QColor(ACCENT))
            self._hits.append((QRectF(tx - shift, H * 0.07, tw + 10, H * 0.06), ("tab", i)))
        p.restore()
        getattr(self, "_paint_" + self._kind())(p, W, H, pr, rx, rw)

    def _head(self, p, rx, y, rw, H, text):
        _draw_text(p, QRectF(rx, y, rw, H * 0.04), text, H * 0.0145, QColor(ACCENT).lighter(150), True, spacing=3)

    # ---- Overview
    def _paint_overview(self, p, W, H, pr, rx, rw):
        st = self.stats
        gap = W * 0.012
        cw, ch = (rw - 2 * gap) / 3, H * 0.105
        cards = [("Games", f"{st['games']}"), ("Hours played", f"{st['hours']:,.0f}"), ("Launches", f"{st['launches']}"),
                 ("Favourites", f"{st['favourites']}"), ("Achievements", f"{len(pr.unlocked)} / {len(ACHIEVEMENTS)}"),
                 ("Day streak", f"{st['streak']}")]
        top = H * 0.16
        for i, (label, value) in enumerate(cards):
            r = QRectF(rx + (i % 3) * (cw + gap), top + (i // 3) * (ch + gap), cw, ch)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 14, 14)
            _draw_text(p, r.adjusted(16, 8, -10, -ch * 0.42), value, H * 0.042, "white", True)
            _draw_text(p, QRectF(r.left() + 16, r.bottom() - ch * 0.42, r.width() - 20, ch * 0.36), label.upper(),
                       H * 0.0135, QColor(255, 255, 255, 140), True, spacing=2)
        y = top + 2 * (ch + gap) + H * 0.012
        self._head(p, rx, y, rw, H, self.ov_header)
        y += H * 0.045
        ts = min(H * 0.165, (rw - 4 * gap) / 5)
        if not self.ov_games:
            _draw_text(p, QRectF(rx, y, rw, H * 0.05), "Play something and it shows up here.", H * 0.02,
                       QColor(255, 255, 255, 130))
        for i, g in enumerate(self.ov_games):
            r = QRectF(rx + i * (ts + gap), y, ts, ts)
            sel = self.zone == "content" and self.ov_i == i
            p.save()
            path = QPainterPath()
            path.addRoundedRect(r, 12, 12)
            p.setClipPath(path)
            self.win.covers.draw(p, r, g)
            p.restore()
            if sel:
                self._ring(p, r, 12)
            _draw_text(p, QRectF(r.left(), r.bottom() + 4, ts, H * 0.03), g.name, H * 0.0175, "white", True)
            hrs = g.playtime_min / 60 + g.tracked_sec / 3600
            _draw_text(p, QRectF(r.left(), r.bottom() + H * 0.03, ts, H * 0.026), f"{hrs:,.1f} h", H * 0.0155,
                       QColor(255, 255, 255, 140))
            self._hits.append((r, ("tile", i)))
        y += ts + H * 0.075
        self._head(p, rx, y, rw, H, "LATEST ACHIEVEMENTS")
        y += H * 0.045
        if not self.recent:
            _draw_text(p, QRectF(rx, y, rw, H * 0.05), "None yet \u2013 launch a game to get started.", H * 0.02,
                       QColor(255, 255, 255, 130))
        for i, (a, ts_) in enumerate(self.recent):
            self._ach_row(p, QRectF(rx, y + i * (H * 0.078), rw, H * 0.07), a, ts_, a[5], 1.0, False, H)

    def _ring(self, p, r, radius, width=3.5):
        glow = QColor(ACCENT)
        glow.setAlpha(90)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(glow, width * 2.5))
        p.drawRoundedRect(r, radius, radius)
        p.setPen(QPen(QColor(ACCENT).lighter(125), width))
        p.drawRoundedRect(r, radius, radius)

    # ---- Achievements
    def _paint_achievements(self, p, W, H, pr, rx, rw):
        n_un = len(pr.unlocked)
        total = sum(a[3] for a in ACHIEVEMENTS)
        _draw_text(p, QRectF(rx, H * 0.145, rw, H * 0.05),
                   f"{n_un} of {len(ACHIEVEMENTS)} unlocked   \u2022   {sum(a[3] for a in ACHIEVEMENTS if a[0] in pr.unlocked):,} of {total:,} G",
                   H * 0.021, QColor(255, 255, 255, 180))
        rh, gap = H * 0.088, H * 0.01
        p.save()
        p.setClipRect(QRectF(rx - 10, H * 0.2, rw + 20, H * 0.76))
        for i, (a, ts, prog, frac) in enumerate(self.rows):
            y = H * 0.215 + i * (rh + gap) - self.ach_sc.v
            if y > H or y + rh < H * 0.2:
                continue
            r = QRectF(rx, y, rw, rh)
            self._ach_row(p, r, a, ts, prog, frac, self.zone == "content" and self.ach_i == i, H)
            self._hits.append((r, ("ach", i)))
        p.restore()

    # ---- Activity
    def _paint_activity(self, p, W, H, pr, rx, rw):
        _draw_text(p, QRectF(rx, H * 0.145, rw, H * 0.05), f"{len(self.act_events)} recent events",
                   H * 0.021, QColor(255, 255, 255, 180))
        if not self.act_events:
            draw_skeleton(p, QRectF(rx, H * 0.22, rw, H * 0.5), "list")
            _draw_text(p, QRectF(rx, H * 0.75, rw, H * 0.05),
                       "Nothing yet \u2013 play a game, favourite one or unlock an achievement.", H * 0.02,
                       QColor(255, 255, 255, 150), False, Qt.AlignmentFlag.AlignHCenter)
            return
        p.save()
        p.setClipRect(QRectF(rx - 10, H * 0.2, rw + 20, H * 0.76))
        geo, _ = self._act_geom(H)
        for kind, data, y, h, n in geo:
            yy = H * 0.215 + y - self.act_sc.v
            if yy > H or yy + h < H * 0.2:
                continue
            if kind == "head":
                _draw_text(p, QRectF(rx, yy, rw, h), data.upper(), H * 0.0148, QColor(ACCENT).lighter(150), True, spacing=3)
                continue
            r = QRectF(rx, yy, rw, h)
            sel = self.zone == "content" and n == self.act_i
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 34 if sel else 18))
            p.drawRoundedRect(r, 12, 12)
            if sel:
                self._ring(p, r, 12, 3)
            glyph, col = self.ACT_STYLE.get(data.get("k"), ("\u25CF", "#7c8aa5"))
            ic = QRectF(r.left() + 12, r.top() + r.height() * 0.14, r.height() * 0.72, r.height() * 0.72)
            c = QColor(col)
            c.setAlpha(60)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(ic)
            _draw_text(p, ic, glyph, H * 0.022, QColor(col).lighter(130), True, Qt.AlignmentFlag.AlignHCenter)
            _draw_text(p, QRectF(ic.right() + 16, r.top(), r.width() * 0.72, r.height()), data.get("x", ""), H * 0.0215,
                       "white" if sel else QColor(255, 255, 255, 215))
            _draw_text(p, QRectF(r.right() - r.width() * 0.2, r.top(), r.width() * 0.19, r.height()),
                       time.strftime("%H:%M", time.localtime(data.get("t", 0))), H * 0.0175, QColor(255, 255, 255, 130),
                       False, Qt.AlignmentFlag.AlignRight)
        p.restore()

    # ---- Stats
    def _bars(self, p, rect, items, H, color=None, fmt="{:.1f} h"):
        """Horizontal bars: items = [(label, value)]."""
        if not items:
            return
        mx_v = max(v for _, v in items) or 1
        rh = rect.height() / len(items)
        for i, (label, v) in enumerate(items):
            y = rect.top() + i * rh
            _draw_text(p, QRectF(rect.left(), y, rect.width() * 0.3, rh), label, H * 0.0185, QColor(255, 255, 255, 215))
            bar = QRectF(rect.left() + rect.width() * 0.32, y + rh * 0.3, rect.width() * 0.5, rh * 0.4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 28))
            p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
            p.setBrush(QColor(color or ACCENT))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * v / mx_v), bar.height()),
                              bar.height() / 2, bar.height() / 2)
            _draw_text(p, QRectF(bar.right() + 10, y, rect.right() - bar.right() - 10, rh), fmt.format(v), H * 0.0175,
                       QColor(255, 255, 255, 170), False, Qt.AlignmentFlag.AlignRight)

    def _paint_stats(self, p, W, H, pr, rx, rw):
        w = self.win
        gap = W * 0.012
        monday = week_monday()
        this = pr.range_stats(monday, 7)
        last = pr.range_stats(monday - datetime.timedelta(days=7), 7)
        d30 = [h for _, h in pr.daily_hours(30)]
        act30 = sum(1 for h in d30 if h > 0.016)
        avg = (sum(d30) / act30) if act30 else 0.0
        cw, ch = (rw - 2 * gap) / 3, H * 0.1
        diff = this["hours"] - last["hours"]
        cards = [("This week", f"{this['hours']:.1f} h", f"{'+' if diff >= 0 else ''}{diff:.1f} h vs last week"),
                 ("Average per active day", f"{avg:.1f} h", "last 30 days"),
                 ("Longest session", f"{pr.longest_session / 3600:.1f} h", "since tracking began")]
        top = H * 0.16
        for i, (label, value, sub) in enumerate(cards):
            r = QRectF(rx + i * (cw + gap), top, cw, ch)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 14, 14)
            _draw_text(p, QRectF(r.left() + 16, r.top() + 6, r.width() - 20, ch * 0.5), value, H * 0.036, "white", True)
            _draw_text(p, QRectF(r.left() + 16, r.top() + ch * 0.5, r.width() - 20, ch * 0.22), label.upper(), H * 0.0125,
                       QColor(255, 255, 255, 140), True, spacing=2)
            _draw_text(p, QRectF(r.left() + 16, r.top() + ch * 0.72, r.width() - 20, ch * 0.24), sub, H * 0.0145,
                       QColor(255, 255, 255, 110))
        # last 14 days
        y = top + ch + H * 0.03
        self._head(p, rx, y, rw, H, "LAST 14 DAYS")
        chart = QRectF(rx, y + H * 0.05, rw, H * 0.22)
        vals = self.daily
        mxh = max([h for _, h in vals] + [1.0])
        bw = chart.width() / len(vals)
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        for f in (0.5, 1.0):
            yy = chart.bottom() - chart.height() * f
            p.drawLine(QPointF(chart.left(), yy), QPointF(chart.right(), yy))
        for i, (day, h) in enumerate(vals):
            bh = chart.height() * (h / mxh)
            r = QRectF(chart.left() + i * bw + bw * 0.2, chart.bottom() - bh, bw * 0.6, max(bh, 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ACCENT) if i == len(vals) - 1 else QColor(ACCENT).darker(140) if h else QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 5, 5)
            if h >= 0.1:
                _draw_text(p, QRectF(r.left() - 8, r.top() - H * 0.028, r.width() + 16, H * 0.026), f"{h:.1f}", H * 0.0135,
                           QColor(255, 255, 255, 190), False, Qt.AlignmentFlag.AlignHCenter)
            lab = datetime.date.fromisoformat(day).strftime("%a")[:2]
            _draw_text(p, QRectF(chart.left() + i * bw, chart.bottom() + 4, bw, H * 0.028), lab, H * 0.0145,
                       QColor(255, 255, 255, 140), False, Qt.AlignmentFlag.AlignHCenter)
        since = pr.tracking_since
        note = ("Tracking since " + time.strftime("%d %b %Y", time.localtime(since))) if since else \
               "Detailed charts start once you play a game with Nexus Grid open."
        _draw_text(p, QRectF(rx, chart.bottom() + H * 0.035, rw, H * 0.03), note, H * 0.0145, QColor(255, 255, 255, 110))
        # by store + top games (lifetime)
        y2 = chart.bottom() + H * 0.085
        half = (rw - gap) / 2
        real = [g for g in w.model.games if g.store not in ("App", "Soon")]
        stores = {}
        for g in real:
            stores[g.store] = stores.get(g.store, 0.0) + g.playtime_min / 60 + g.tracked_sec / 3600
        self._head(p, rx, y2, half, H, "HOURS BY STORE")
        self._bars(p, QRectF(rx, y2 + H * 0.045, half, H * 0.17),
                   sorted(((k, v) for k, v in stores.items() if v > 0), key=lambda x: -x[1])[:5], H)
        self._head(p, rx + half + gap, y2, half, H, "TOP GAMES (ALL TIME)")
        top5 = sorted(real, key=lambda g: -(g.playtime_min / 60 + g.tracked_sec / 3600))[:5]
        self._bars(p, QRectF(rx + half + gap, y2 + H * 0.045, half, H * 0.17),
                   [(g.name, g.playtime_min / 60 + g.tracked_sec / 3600) for g in top5 if (g.playtime_min or g.tracked_sec)], H)

    # ---- Badges & titles
    def _paint_badges(self, p, W, H, pr, rx, rw):
        half = rw * 0.47
        self._head(p, rx, H * 0.16, half, H, f"BADGES   {len(pr.unlocked)} / {len(ACHIEVEMENTS)}")
        cols = 7
        d = min(half / cols * 0.84, H * 0.08)
        stepx, stepy = half / cols, d * 1.45
        for i, a in enumerate(ACHIEVEMENTS):
            c = QPointF(rx + (i % cols) * stepx + d / 2, H * 0.23 + (i // cols) * stepy + d / 2)
            on = a[0] in pr.unlocked
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 34 if on else 14))
            p.drawEllipse(c, d / 2, d / 2)
            draw_trophy(p, c, d * 0.66, trophy_color(a[3], on))
        tx, tw = rx + rw * 0.52, rw * 0.48
        self._head(p, tx, H * 0.16, tw, H, "TITLES")
        rh = H * 0.058
        p.save()
        p.setClipRect(QRectF(tx - 8, H * 0.2, tw + 16, H * 0.75))
        for i, t in enumerate(self.titles):
            y = H * 0.21 + i * rh - self.title_sc.v
            if y > H or y + rh < H * 0.2:
                continue
            r = QRectF(tx, y, tw, rh * 0.92)
            sel = self.zone == "content" and i == self.title_i
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 34 if sel else 16))
            p.drawRoundedRect(r, 10, 10)
            if sel:
                self._ring(p, r, 10, 3)
            on = t["unlocked"]
            _draw_text(p, QRectF(r.left() + 14, r.top() + r.height() * 0.04, r.width() * 0.6, r.height() * 0.52), t["name"],
                       H * 0.0215, "white" if on else QColor(255, 255, 255, 110), True)
            _draw_text(p, QRectF(r.left() + 14, r.top() + r.height() * 0.5, r.width() * 0.62, r.height() * 0.45),
                       t["how"], H * 0.0148, QColor(255, 255, 255, 130 if on else 90))
            if t["id"] == pr.title and on:
                chip = QRectF(r.right() - r.width() * 0.28, r.center().y() - H * 0.015, r.width() * 0.26, H * 0.03)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
                _draw_text(p, chip, "EQUIPPED", H * 0.0128, "white", True, Qt.AlignmentFlag.AlignHCenter, 1)
            elif not on:
                _draw_text(p, QRectF(r.right() - r.width() * 0.2, r.top(), r.width() * 0.18, r.height()), "Locked",
                           H * 0.0155, QColor(255, 255, 255, 100), False, Qt.AlignmentFlag.AlignRight)
        p.restore()

    # ---- Showcase
    def _paint_showcase(self, p, W, H, pr, rx, rw):
        gap = W * 0.012
        by_id = {g.id: g for g in self.win.model.games}
        self._head(p, rx, H * 0.16, rw, H, "PINNED GAMES   (up to 5)")
        ts = min(H * 0.17, (rw - 4 * gap) / 5)
        y = H * 0.21
        for j in range(5):
            r = QRectF(rx + j * (ts + gap), y, ts, ts)
            g = by_id.get(pr.showcase_games[j]) if j < len(pr.showcase_games) else None
            self._slot(p, r, g is not None, j, H)
            if g is not None:
                p.save()
                path = QPainterPath()
                path.addRoundedRect(r, 12, 12)
                p.setClipPath(path)
                self.win.covers.draw(p, r, g)
                p.restore()
                _draw_text(p, QRectF(r.left(), r.bottom() + 4, ts, H * 0.03), g.name, H * 0.0175, "white", True)
            self._slot_ring(p, r, j, H)
            self._hits.append((r, ("slot", j)))
        y2 = y + ts + H * 0.09
        self._head(p, rx, y2 - H * 0.045, rw, H, "PINNED ACHIEVEMENTS   (up to 3)")
        cw, ch = (rw - 2 * gap) / 3, H * 0.13
        for k in range(3):
            j = 5 + k
            r = QRectF(rx + k * (cw + gap), y2, cw, ch)
            aid = pr.showcase_ach[k] if k < len(pr.showcase_ach) else None
            a = next((x for x in ACHIEVEMENTS if x[0] == aid), None)
            self._slot(p, r, a is not None, j, H)
            if a is not None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 30))
                p.drawRoundedRect(r, 14, 14)
                ic = QRectF(r.left() + 14, r.top() + ch * 0.16, ch * 0.68, ch * 0.68)
                p.setBrush(QColor(255, 255, 255, 24))
                p.drawEllipse(ic)
                draw_trophy(p, ic.center(), ic.height() * 0.62, trophy_color(a[3]))
                _draw_text(p, QRectF(ic.right() + 14, r.top() + ch * 0.12, r.width() - ic.width() - 40, ch * 0.4), a[1],
                           H * 0.0225, "white", True)
                _draw_text(p, QRectF(ic.right() + 14, r.top() + ch * 0.52, r.width() - ic.width() - 40, ch * 0.36), a[2],
                           H * 0.0155, QColor(255, 255, 255, 150))
            self._slot_ring(p, r, j, H)
            self._hits.append((r, ("slot", j)))
        _draw_text(p, QRectF(rx, y2 + ch + H * 0.04, rw, H * 0.04),
                   "A  choose   \u2022   X  remove   \u2022   pinned games also replace \u201cMost played\u201d on your Overview"
                   if self.win._pad_seen else
                   "Enter  choose   \u2022   Delete / X  remove   \u2022   pinned games also replace \u201cMost played\u201d on your Overview",
                   H * 0.0165, QColor(255, 255, 255, 120))

    def _slot(self, p, r, filled, j, H):
        if filled:
            return
        pen = QPen(QColor(255, 255, 255, 70), 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(r, 14, 14)
        _draw_text(p, r, "+", H * 0.06, QColor(255, 255, 255, 90), False, Qt.AlignmentFlag.AlignHCenter)

    def _slot_ring(self, p, r, j, H):
        if self.zone == "content" and self.sc_i == j:
            self._ring(p, r, 14)

    # ---- Goals
    def _paint_goals(self, p, W, H, pr, rx, rw):
        wk = self.goals
        mon = wk["monday"]
        end = mon + datetime.timedelta(days=6)
        _draw_text(p, QRectF(rx, H * 0.145, rw * 0.7, H * 0.05),
                   f"This week   {mon.strftime('%d %b')} \u2013 {end.strftime('%d %b')}", H * 0.0235, "white", True)
        _draw_text(p, QRectF(rx + rw * 0.6, H * 0.145, rw * 0.4, H * 0.05),
                   f"Resets in {wk['days_left']} day{'s' if wk['days_left'] != 1 else ''}", H * 0.019,
                   QColor(255, 255, 255, 150), False, Qt.AlignmentFlag.AlignRight)
        rh, gap, y = H * 0.1, H * 0.014, H * 0.215
        for i, g in enumerate(wk["goals"]):
            r = QRectF(rx, y + i * (rh + gap), rw, rh)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30 if g["done"] else 20))
            p.drawRoundedRect(r, 14, 14)
            ic = QRectF(r.left() + 14, r.top() + rh * 0.17, rh * 0.66, rh * 0.66)
            col = QColor("#22c55e") if g["done"] else QColor(ACCENT)
            c2 = QColor(col)
            c2.setAlpha(60)
            p.setBrush(c2)
            p.drawEllipse(ic)
            _draw_text(p, ic, "\u2713" if g["done"] else str(i + 1), H * 0.03, col.lighter(130), True,
                       Qt.AlignmentFlag.AlignHCenter)
            tx = ic.right() + 18
            _draw_text(p, QRectF(tx, r.top() + rh * 0.1, r.width() * 0.6, rh * 0.42), g["title"], H * 0.0245,
                       "white", True)
            hours = "hour" in g["title"].lower()
            prog = f"{g['progress']:.1f} / {g['target']} h" if hours else f"{int(g['progress'])} / {g['target']}"
            bar = QRectF(tx, r.top() + rh * 0.62, r.width() * 0.55, H * 0.011)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 36))
            p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
            frac = min(1.0, g["progress"] / g["target"])
            p.setBrush(col)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * frac), bar.height()),
                              bar.height() / 2, bar.height() / 2)
            _draw_text(p, QRectF(bar.right() + 14, r.top() + rh * 0.5, r.width() * 0.2, rh * 0.36), prog, H * 0.0175,
                       QColor(255, 255, 255, 170))
            chip = QRectF(r.right() - r.width() * 0.13, r.center().y() - H * 0.02, r.width() * 0.11, H * 0.04)
            chipc = QColor("#f5c542")
            chipc.setAlpha(60 if not g["done"] else 100)
            p.setBrush(chipc)
            p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
            _draw_text(p, chip, f"+{g['reward']} G", H * 0.0185, QColor("#f5c542"), True, Qt.AlignmentFlag.AlignHCenter)
        by = y + 4 * (rh + gap) + H * 0.01
        r = QRectF(rx, by, rw, H * 0.075)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT).darker(160) if wk["all_done"] else QColor(255, 255, 255, 16))
        p.drawRoundedRect(r, 14, 14)
        _draw_text(p, r.adjusted(18, 0, -18, 0),
                   ("All four goals done \u2013 bonus earned!" if wk["all_done"] else "Complete all four goals for a bonus")
                   + f"   (+{GOAL_ALL_BONUS} G)", H * 0.0215, "white", True)
        _draw_text(p, QRectF(rx, by + H * 0.095, rw, H * 0.04),
                   f"{pr.goals_completed()} goals completed   \u2022   {pr.perfect_weeks()} perfect weeks   \u2022   "
                   f"{pr.goal_points()} G earned from goals", H * 0.0185, QColor(255, 255, 255, 150))
        _draw_text(p, QRectF(rx, by + H * 0.135, rw, H * 0.04), "New goals appear every Monday.", H * 0.0165,
                   QColor(255, 255, 255, 110))

    # ---- Year in Review
    def _paint_year(self, p, W, H, pr, rx, rw):
        y = self.year
        yr = time.localtime().tm_year
        gap = W * 0.012
        hero = QRectF(rx, H * 0.16, rw, H * 0.22)
        bg = QLinearGradient(hero.topLeft(), hero.bottomRight())
        bg.setColorAt(0, QColor(ACCENT).darker(110))
        bg.setColorAt(1, QColor(ACCENT).darker(300))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(hero, 20, 20)
        _draw_text(p, QRectF(hero.left() + 28, hero.top() + 14, hero.width() * 0.5, hero.height() * 0.22), f"{yr} IN REVIEW",
                   H * 0.0175, QColor(255, 255, 255, 190), True, spacing=4)
        _draw_text(p, QRectF(hero.left() + 28, hero.top() + hero.height() * 0.24, hero.width() * 0.5, hero.height() * 0.5),
                   f"{y['hours']:,.0f} hours", H * 0.075, "white", True)
        _draw_text(p, QRectF(hero.left() + 28, hero.top() + hero.height() * 0.74, hero.width() * 0.55, hero.height() * 0.2),
                   f"across {y['games']} game{'s' if y['games'] != 1 else ''} and {y['active_days']} active days",
                   H * 0.0205, QColor(255, 255, 255, 200))
        if y["top"]:
            g0, sec = y["top"][0]
            cov = QRectF(hero.right() - hero.height() * 0.8 - 24, hero.top() + hero.height() * 0.1, hero.height() * 0.8, hero.height() * 0.8)
            p.save()
            path = QPainterPath()
            path.addRoundedRect(cov, 14, 14)
            p.setClipPath(path)
            self.win.covers.draw(p, cov, g0)
            p.restore()
            _draw_text(p, QRectF(cov.left() - hero.width() * 0.3 - 16, hero.top() + hero.height() * 0.3, hero.width() * 0.3, hero.height() * 0.2),
                       "TOP GAME", H * 0.0145, QColor(255, 255, 255, 180), True, Qt.AlignmentFlag.AlignRight, 3)
            _draw_text(p, QRectF(cov.left() - hero.width() * 0.3 - 16, hero.top() + hero.height() * 0.46, hero.width() * 0.3, hero.height() * 0.24),
                       g0.name, H * 0.03, "white", True, Qt.AlignmentFlag.AlignRight)
            _draw_text(p, QRectF(cov.left() - hero.width() * 0.3 - 16, hero.top() + hero.height() * 0.7, hero.width() * 0.3, hero.height() * 0.2),
                       f"{sec / 3600:.1f} h", H * 0.0205, QColor(255, 255, 255, 190), False, Qt.AlignmentFlag.AlignRight)
        cw, ch = (rw - 2 * gap) / 3, H * 0.085
        tiles = [("Launches", f"{y['launches']}"), ("Achievements unlocked", f"{y['achievements']}"),
                 ("Longest streak", f"{y['longest_streak']} d")]
        ty = hero.bottom() + gap
        for i, (label, value) in enumerate(tiles):
            r = QRectF(rx + i * (cw + gap), ty, cw, ch)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 14, 14)
            _draw_text(p, QRectF(r.left() + 16, r.top() + 4, r.width() - 20, ch * 0.56), value, H * 0.034, "white", True)
            _draw_text(p, QRectF(r.left() + 16, r.top() + ch * 0.58, r.width() - 20, ch * 0.34), label.upper(), H * 0.0125,
                       QColor(255, 255, 255, 140), True, spacing=2)
        by = ty + ch + H * 0.03
        half = (rw - gap) / 2
        self._head(p, rx, by, half, H, "TOP 5 GAMES")
        self._bars(p, QRectF(rx, by + H * 0.045, half, H * 0.2), [(g.name, s / 3600) for g, s in y["top"]], H)
        self._head(p, rx + half + gap, by, half, H, "BUSIEST DAYS OF THE WEEK")
        wd = y["weekday"]
        mxw = max(wd + [0.001])
        chart = QRectF(rx + half + gap, by + H * 0.07, half, H * 0.14)
        bw = chart.width() / 7
        for i, h in enumerate(wd):
            bh = chart.height() * h / mxw
            r = QRectF(chart.left() + i * bw + bw * 0.2, chart.bottom() - bh, bw * 0.6, max(bh, 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ACCENT) if h == mxw and h > 0 else QColor(ACCENT).darker(150) if h else QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 5, 5)
            _draw_text(p, QRectF(chart.left() + i * bw, chart.bottom() + 4, bw, H * 0.03), ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")[i],
                       H * 0.0155, QColor(255, 255, 255, 140), False, Qt.AlignmentFlag.AlignHCenter)
        if y["hours"] <= 0:
            _draw_text(p, QRectF(rx, by + H * 0.3, rw, H * 0.05),
                       "Your year fills in as you play with Nexus Grid open \u2013 check back soon.", H * 0.02,
                       QColor(255, 255, 255, 150), False, Qt.AlignmentFlag.AlignHCenter)

    # ---- input
    def keyPressEvent(self, e):
        K, k = Qt.Key, e.key()
        if k == K.Key_Left:
            self.move(-1, 0)
        elif k == K.Key_Right:
            self.move(1, 0)
        elif k == K.Key_Up:
            self.move(0, -1)
        elif k == K.Key_Down:
            self.move(0, 1)
        elif k in (K.Key_Return, K.Key_Enter, K.Key_Space):
            self.activate()
        elif k in (K.Key_Delete, K.Key_X):
            self.tertiary()
        elif k in (K.Key_Escape, K.Key_Backspace):
            self.back()
        elif k == K.Key_PageUp:
            self.shoulder(-1)
        elif k == K.Key_PageDown:
            self.shoulder(1)
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        for rect, (kind, idx) in reversed(self._hits):
            if rect.contains(e.position()):
                if kind == "tab":
                    self.tab = idx
                    self.ach_i = self.act_i = self.title_i = 0
                    for sc in (self.ach_sc, self.act_sc, self.title_sc):
                        sc.reset()
                elif kind == "left":
                    self.zone, self.left_i = "left", idx
                    self._do_left(idx)
                elif kind == "tile":
                    self.zone, self.ov_i = "content", idx
                    self.activate()
                elif kind == "ach":
                    self.zone, self.ach_i = "content", idx
                elif kind == "slot":
                    self.zone, self.sc_i = "content", idx
                    self._pick_slot(idx)
                self.update()
                return

    def wheelEvent(self, e):
        k = self._kind()
        if k in ("achievements", "activity", "badges"):
            self.zone = "content"
            self.move(0, -1 if e.angleDelta().y() > 0 else 1)

    def _paint_left(self, p, W, H, pr):
        mx, cw = W * 0.05, W * 0.27
        d = min(H * 0.2, cw * 0.7)
        av = QRectF(mx, H * 0.075, d, d)
        paint_avatar(p, av, self.win)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 70), 3))
        p.drawEllipse(av)
        y = av.bottom() + H * 0.018
        _draw_text(p, QRectF(mx, y, cw, H * 0.055), pr.name, H * 0.04, "white", True)
        _draw_text(p, QRectF(mx, y + H * 0.05, cw, H * 0.03), pr.title_name(), H * 0.0185, QColor(ACCENT).lighter(150), True)
        y += H * 0.082
        gr = H * 0.016
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f5c542"))
        p.drawEllipse(QPointF(mx + gr, y + gr), gr, gr)
        _draw_text(p, QRectF(mx, y, gr * 2, gr * 2), "G", H * 0.02, "#2b2200", True, Qt.AlignmentFlag.AlignHCenter)
        _draw_text(p, QRectF(mx + gr * 2 + 10, y - 2, cw, gr * 2 + 4), f"{pr.points():,}", H * 0.032, "white", True)
        y += H * 0.047
        lvl, frac, into, need = pr.level_info()
        _draw_text(p, QRectF(mx, y, cw, H * 0.03), f"Level {lvl}   \u2022   {into} / {need} G to next", H * 0.018,
                   QColor(255, 255, 255, 190))
        y += H * 0.033
        bar = QRectF(mx, y, cw * 0.92, H * 0.009)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * frac), bar.height()),
                          bar.height() / 2, bar.height() / 2)
        y += H * 0.02
        _draw_text(p, QRectF(mx, y, cw, H * 0.03), "Member since " + time.strftime("%b %Y", time.localtime(pr.created)),
                   H * 0.016, QColor(255, 255, 255, 130))
        y += H * 0.045
        _draw_text(p, QRectF(mx, y, cw, H * 0.026), "BIO", H * 0.014, QColor(ACCENT).lighter(150), True, spacing=3)
        y += H * 0.03
        bio = pr.bio or "Nothing here yet \u2013 choose \u201cEdit bio\u201d to tell people about yourself."
        _draw_wrapped(p, QRectF(mx, y, cw * 0.95, H * 0.15), bio, H * 0.0195,
                      QColor(255, 255, 255, 215 if pr.bio else 120))
        y += H * 0.165
        bh, gap = H * 0.05, H * 0.012
        for i, label in enumerate(self.left_labels()):
            r = QRectF(mx, y + i * (bh + gap), cw * 0.78, bh)
            sel = self.zone == "left" and self.left_i == i
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ACCENT) if sel else QColor(255, 255, 255, 34))
            p.drawRoundedRect(r, bh / 2, bh / 2)
            if sel:
                glow = QColor(ACCENT)
                glow.setAlpha(80)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(glow, 8))
                p.drawRoundedRect(r, bh / 2, bh / 2)
            _draw_text(p, r.adjusted(bh * 0.5, 0, -bh * 0.3, 0), label, H * 0.0215, "white", sel)
            if i == 2:                                           # swatch of the current avatar colour
                p.setPen(QPen(QColor(255, 255, 255, 200), 2))
                p.setBrush(QColor(pr.avatar_color or ACCENT))
                p.drawEllipse(QPointF(r.right() - bh * 0.55, r.center().y()), bh * 0.24, bh * 0.24)
            self._hits.append((r, ("left", i)))

    def _ach_row(self, p, r, a, unlocked_ts, prog, frac, selected, H):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34 if selected else 20))
        p.drawRoundedRect(r, 12, 12)
        if selected:
            glow = QColor(ACCENT)
            glow.setAlpha(90)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(glow, 8))
            p.drawRoundedRect(r, 12, 12)
            p.setPen(QPen(QColor(ACCENT).lighter(125), 3))
            p.drawRoundedRect(r, 12, 12)
        ic = QRectF(r.left() + 12, r.top() + (r.height() - r.height() * 0.78) / 2, r.height() * 0.78, r.height() * 0.78)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawEllipse(ic)
        draw_trophy(p, ic.center(), ic.height() * 0.62, trophy_color(a[3], bool(unlocked_ts)))
        tx = ic.right() + 16
        right_w = r.width() * 0.27
        locked_col = QColor(255, 255, 255, 120)
        _draw_text(p, QRectF(tx, r.top() + r.height() * 0.1, r.width() - right_w - tx + r.left(), r.height() * 0.45), a[1],
                   H * 0.0235, "white" if unlocked_ts else locked_col, True)
        _draw_text(p, QRectF(tx, r.top() + r.height() * 0.52, r.width() - right_w - tx + r.left(), r.height() * 0.38), a[2],
                   H * 0.0165, QColor(255, 255, 255, 150))
        rr = QRectF(r.right() - right_w, r.top(), right_w - 14, r.height())
        _draw_text(p, QRectF(rr.left(), rr.top() + r.height() * 0.08, rr.width(), r.height() * 0.42), f"{a[3]} G", H * 0.0235,
                   QColor("#f5c542") if unlocked_ts else locked_col, True, Qt.AlignmentFlag.AlignRight)
        if unlocked_ts:
            _draw_text(p, QRectF(rr.left(), rr.top() + r.height() * 0.5, rr.width(), r.height() * 0.38),
                       "Unlocked " + ago(unlocked_ts), H * 0.0148, QColor(255, 255, 255, 140), False,
                       Qt.AlignmentFlag.AlignRight)
        else:
            bar = QRectF(rr.left() + rr.width() * 0.25, rr.bottom() - r.height() * 0.3, rr.width() * 0.75, H * 0.008)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * frac), bar.height()),
                              bar.height() / 2, bar.height() / 2)
            _draw_text(p, QRectF(rr.left(), bar.top() - H * 0.026, rr.width(), H * 0.024), f"{int(prog)} / {a[5]}",
                       H * 0.0148, QColor(255, 255, 255, 140), False, Qt.AlignmentFlag.AlignRight)

    def _paint_hints(self, p, W, H):
        mx = W * 0.05
        y, h = H * 0.9735, H * 0.021
        if self.win._pad_seen:
            items = [("A", "Select"), ("B", "Back"), ("LB RB", "Switch tab")]
        else:
            items = [("Enter", "Select"), ("Esc", "Back"), ("PgUp PgDn", "Switch tab")]
        kf, tf = _px_font(H * 0.0125, True), _px_font(H * 0.014)
        x = mx
        for key, text in items:
            kw = max(h, QFontMetrics(kf).horizontalAdvance(key) + h * 0.8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawRoundedRect(QRectF(x, y, kw, h), h / 2, h / 2)
            p.setFont(kf)
            p.setPen(QColor("white"))
            p.drawText(QRectF(x, y, kw, h), Qt.AlignmentFlag.AlignCenter, key)
            x += kw + 8
            p.setFont(tf)
            p.setPen(QColor(255, 255, 255, 175))
            tw = QFontMetrics(tf).horizontalAdvance(text)
            p.drawText(QRectF(x, y, tw + 4, h), Qt.AlignmentFlag.AlignVCenter, text)
            x += tw + h * 1.3

    def _do_left(self, i):
        w, pr = self.win, self.win.profile
        if i == 0:
            w.text_input("Display name", pr.name, self._set_name, False, 24)
        elif i == 1:
            w.text_input("Your bio", pr.bio, self._set_bio, True, 280)
        elif i == 2:
            cur = AVATAR_COLORS.index(pr.avatar_color) if pr.avatar_color in AVATAR_COLORS else 0
            pr.avatar_color = AVATAR_COLORS[(cur + 1) % len(AVATAR_COLORS)]
            pr.save()
            self.update()
        else:
            if pr.has_avatar:
                pr.has_avatar = False
                pr.save()
                AVATAR_FILE.unlink(missing_ok=True)
                w.reload_avatar()
                self.update()
            else:
                QTimer.singleShot(0, self._pick_picture)

    def _set_name(self, text):
        if text and text.strip():
            self.win.profile.name = text.strip()
            self.win.profile.save()
            self.update()

    def _set_bio(self, text):
        if text is not None:
            self.win.profile.bio = text.strip()
            self.win.profile.save()
            self.win.queue_achievements()
            self.update()

    def _pick_picture(self):
        path, _ = QFileDialog.getOpenFileName(self.win, "Choose a profile picture", "",
                                              "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            return
        side = min(img.width(), img.height())
        img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
        img = img.scaled(256, 256, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        img.save(str(AVATAR_FILE), "PNG")
        self.win.profile.has_avatar = True
        self.win.profile.save()
        self.win.reload_avatar()
        self.update()


ROADMAP_FILE = DATA_DIR / "roadmap.json"
STATUSES = ["Idea", "Planned", "In progress", "Done"]
STATUS_COLORS = {"Idea": "#7c8aa5", "Planned": "#3b82f6", "In progress": "#f59e0b", "Done": "#22c55e"}
ROADMAP_CATS = ["Side panel", "Home screen", "Profile", "Game page", "Apps", "Settings", "Already built"]

# (id, category, title, description)
ROADMAP = [
    # ---- side panel
    ("friends", "Side panel", "Friends", "Who is online and what they're playing. A preview with sample people exists."),
    ("messages", "Side panel", "Messages", "Chat with friends from the panel. A preview with sample messages exists."),
    ("notifications", "Side panel", "Notifications", "Downloads, updates and achievements in one place."),
    ("parties", "Side panel", "Parties & voice chat", "Hop into a party and talk to friends while you play."),
    ("captures", "Side panel", "Captures", "Screenshots and clips in a gallery you can browse with the controller."),
    ("downloads", "Side panel", "Downloads & updates", "A queue of game and app updates with progress."),
    ("wishlist", "Side panel", "Wishlist", "Games you want, with notes and links."),
    ("controller", "Side panel", "Controller", "Battery level, button remapping and vibration settings."),
    ("audio", "Side panel", "Audio", "Volume, mute and choosing the output device."),
    ("power", "Side panel", "Power", "Sleep, restart and shut down without leaving Big Picture."),
    ("help", "Side panel", "Help & tips", "Controls, shortcuts and tips for getting the most out of Nexus Grid."),
    # ---- home screen
    ("surprise", "Home screen", "Surprise me", "Picks a random game from your backlog."),
    ("recent_added", "Home screen", "Recently added", "The newest games in your library."),
    ("backlog", "Home screen", "Backlog", "Games you haven't played yet, ready to start."),
    ("this_week", "Home screen", "This week", "Hours played and your top game this week."),
    ("themes", "Home screen", "Themes & wallpapers", "A bigger Customize your Home: themes, wallpapers and layouts."),
    # ---- profile
    ("p_activity", "Profile", "Activity", "A feed of what you played and unlocked, e.g. \u201cplayed X for 2 h\u201d."),
    ("p_stats", "Profile", "Stats", "Charts of playtime by day, week and store."),
    ("p_badges", "Profile", "Badges & titles", "Pick a title to show under your name."),
    ("p_showcase", "Profile", "Showcase", "Pin your favourite games and achievements to your profile."),
    ("p_goals", "Profile", "Goals", "Weekly challenges such as \u201cplay 3 different games\u201d."),
    ("p_year", "Profile", "Year in Review", "A summary of your gaming year."),
    # ---- game page
    ("g_screens", "Game page", "Screenshots", "A strip of screenshots on each game's page."),
    ("g_news", "Game page", "News & patch notes", "What's new for this game."),
    ("g_rating", "Game page", "My rating", "Rate each game with stars."),
    ("g_ttb", "Game page", "Time to beat", "How long the game takes, next to your own playtime."),
    ("g_saves", "Game page", "Saves & backups", "Back up and restore your save files."),
    ("g_mods", "Game page", "Mods", "Manage mods for a game."),
    ("g_store_ach", "Game page", "Store achievements", "The game's own achievements from its store."),
    # ---- apps
    ("a_twitch", "Apps", "Twitch", "Watch streams on the couch."),
    ("a_spotify", "Apps", "Spotify", "Music while you browse or play."),
    ("a_netflix", "Apps", "Netflix", "Movies and shows, controller-friendly."),
    ("a_plex", "Apps", "Plex", "Your own media library."),
    ("a_discord", "Apps", "Discord", "Chat and voice with your servers."),
    ("a_browser", "Apps", "Web browser", "A simple full-screen browser."),
    ("a_files", "Apps", "File explorer", "Browse your files from Big Picture."),
    ("a_media", "Apps", "Media player", "Play local video and music."),
    ("a_emulators", "Apps", "Emulators", "Add emulators and ROM folders as games."),
    # ---- settings
    ("s_quick_resume", "Settings", "Quick Resume", "Jump straight back into your last game."),
    ("s_screensaver", "Settings", "Screensaver & idle dimming", "Dim or show a screensaver when you're away."),
    ("s_sounds", "Settings", "Sound effects", "Soft navigation and panel sounds, on or off."),
    ("s_language", "Settings", "Language", "Use Nexus Grid in your language."),
    ("s_backup", "Settings", "Backup & restore", "Back up and restore your whole NexusGrid folder."),
    ("s_updates", "Settings", "Check for updates", "Find and install new Nexus Grid versions."),
    ("s_parental", "Settings", "Parental controls & playtime limits", "Limit what can be played, and for how long."),
    # ---- already built
    ("b_profile", "Already built", "Profile & achievements", "Name, bio, avatar, G score, levels and 28 achievements."),
    ("b_osk", "Already built", "On-screen keyboard", "Type with the controller anywhere in Big Picture."),
    ("b_search", "Already built", "Search", "Find games and apps from the icon bar or with /."),
    ("b_panel", "Already built", "Side panel", "Right-stick click opens the panel with quick settings."),
    ("b_youtube", "Already built", "YouTube app", "Your own YouTube app, started from the Apps shelf."),
    ("b_folders", "Already built", "Folders & list view", "Group games by creator; grid or list."),
    ("b_covers", "Already built", "Cover art & thumbnails", "Real cover art and a fast thumbnail cache."),
    ("b_pad", "Already built", "Controller navigation", "Navigate everything with an Xbox-style controller."),
    ("b_home", "Already built", "Xbox-style home", "Featured tile row, shortcut tiles and an icon bar."),
    ("b_docs", "Already built", "NexusGrid folder & docs", "Everything lives in Documents/NexusGrid with a README."),
    ("b_roadmap", "Already built", "Roadmap", "This page: every idea, with a status you control."),
]
ROADMAP_BY_ID = {r[0]: r for r in ROADMAP}
ROADMAP_DEFAULT = {r[0]: "Done" for r in ROADMAP if r[1] == "Already built"}
ROADMAP_DEFAULT.update({"friends": "Planned", "messages": "Planned"})
ROADMAP_DEFAULT.update({k: "Done" for k in ("p_activity", "p_stats", "p_badges", "p_showcase", "p_goals", "p_year")})


class RoadmapStore:
    """Your status for each roadmap item (Idea / Planned / In progress / Done) and starred priorities."""

    def __init__(self):
        self.statuses, self.stars = {}, set()
        try:
            raw = json.loads(ROADMAP_FILE.read_text(encoding="utf-8"))
            self.statuses = {k: v for k, v in (raw.get("status") or {}).items() if v in STATUSES}
            self.stars = set(raw.get("star") or [])
        except Exception:
            pass

    def save(self):
        try:
            tmp = ROADMAP_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"status": self.statuses, "star": sorted(self.stars)}, indent=2), encoding="utf-8")
            tmp.replace(ROADMAP_FILE)
        except Exception:
            pass

    def status(self, rid):
        return self.statuses.get(rid) or ROADMAP_DEFAULT.get(rid, "Idea")

    def cycle(self, rid, step):
        i = STATUSES.index(self.status(rid))
        self.statuses[rid] = STATUSES[(i + (step or 1)) % len(STATUSES)]
        self.save()

    def starred(self, rid):
        return rid in self.stars

    def toggle_star(self, rid):
        self.stars ^= {rid}
        self.save()

    def counts(self):
        c = {s: 0 for s in STATUSES}
        for r in ROADMAP:
            c[self.status(r[0])] += 1
        return c


def draw_skeleton(p, r, kind):
    """Faint wireframe of a feature that isn't built yet (so a placeholder hints at its future layout)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    soft, softer = QColor(255, 255, 255, 30), QColor(255, 255, 255, 16)
    if kind == "list":
        n = 5
        h = r.height() / n
        for i in range(n):
            y = r.top() + i * h
            p.setBrush(softer)
            p.drawRoundedRect(QRectF(r.left(), y + 4, r.width(), h - 8), 10, 10)
            p.setBrush(soft)
            p.drawEllipse(QPointF(r.left() + h * 0.5, y + h / 2), h * 0.26, h * 0.26)
            p.drawRoundedRect(QRectF(r.left() + h * 0.95, y + h * 0.28, r.width() * 0.42, h * 0.16), 5, 5)
            p.drawRoundedRect(QRectF(r.left() + h * 0.95, y + h * 0.56, r.width() * 0.28, h * 0.13), 5, 5)
    elif kind == "chart":
        hs = [0.35, 0.55, 0.4, 0.7, 0.5, 0.85, 0.6, 0.45, 0.75, 0.9, 0.55, 0.65]
        w = r.width() / len(hs)
        p.setBrush(soft)
        for i, f in enumerate(hs):
            p.drawRoundedRect(QRectF(r.left() + i * w + w * 0.18, r.bottom() - r.height() * f, w * 0.64, r.height() * f), 6, 6)
    elif kind == "badges":
        d = min(r.height() / 2.3, r.width() / 3.6)
        p.setBrush(soft)
        for i in range(6):
            c = QPointF(r.left() + d * 0.6 + (i % 3) * d * 1.3, r.top() + d * 0.6 + (i // 3) * d * 1.3)
            p.drawEllipse(c, d * 0.5, d * 0.5)
    elif kind == "tiles":
        d = min(r.height(), r.width() / 5.4)
        p.setBrush(soft)
        for i in range(5):
            p.drawRoundedRect(QRectF(r.left() + i * d * 1.12, r.top(), d, d), 12, 12)
    elif kind == "goals":
        h = r.height() / 4
        for i in range(4):
            y = r.top() + i * h
            p.setBrush(soft)
            p.drawRoundedRect(QRectF(r.left(), y + h * 0.12, r.width() * 0.4, h * 0.2), 5, 5)
            p.setBrush(softer)
            p.drawRoundedRect(QRectF(r.left(), y + h * 0.5, r.width(), h * 0.16), 8, 8)
            p.setBrush(soft)
            p.drawRoundedRect(QRectF(r.left(), y + h * 0.5, r.width() * (0.25 + 0.18 * i), h * 0.16), 8, 8)
    else:                                              # hero card
        p.setBrush(softer)
        p.drawRoundedRect(r, 16, 16)
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(r.left() + r.width() * 0.06, r.top() + r.height() * 0.12, r.width() * 0.5, r.height() * 0.14), 6, 6)
        for i in range(3):
            p.drawRoundedRect(QRectF(r.left() + r.width() * 0.06, r.top() + r.height() * (0.45 + i * 0.16),
                                     r.width() * (0.7 - i * 0.12), r.height() * 0.08), 5, 5)
    p.restore()


class RoadmapPage(QWidget):
    """Every idea for what to build next, with a status you control. Left/Right changes it."""
    FILTERS = ["All"] + STATUSES

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.filter_i, self.sel, self.scroll = 0, 0, 0.0
        self._rows, self._items, self._hits = [], [], []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_scroll)

    def _set_scroll(self, v):
        self.scroll = float(v)
        self.update()

    # ---- data
    def show_roadmap(self, focus=None):
        self.filter_i, self.sel, self.scroll = 0, 0, 0.0
        self._anim.stop()
        self.rebuild()
        if focus and focus in ROADMAP_BY_ID:
            for i, r in enumerate(self._items):
                if r[0] == focus:
                    self.sel = i
                    self._scroll_to_sel(animate=False)
                    break
        self.update()

    def rebuild(self):
        st = self.win.roadmap
        f = self.FILTERS[self.filter_i]
        rows = []
        for cat in ROADMAP_CATS:
            items = [r for r in ROADMAP if r[1] == cat and (f == "All" or st.status(r[0]) == f)]
            if items:
                rows.append(("head", cat))
                rows += [("item", r) for r in items]
        self._rows = rows
        self._items = [r for kind, r in rows if kind == "item"]
        self.sel = max(0, min(self.sel, len(self._items) - 1))

    def _geom(self, H):
        hh, rh, gap = H * 0.05, H * 0.078, H * 0.008
        y, out = 0.0, []
        for kind, data in self._rows:
            h = hh if kind == "head" else rh
            out.append((kind, data, y, h))
            y += h + (0 if kind == "head" else gap)
        return out, y

    def _scroll_to_sel(self, animate=True):
        H = self.height()
        geo, total = self._geom(H)
        n, y, h = -1, 0.0, 0.0
        for kind, data, yy, hh in geo:
            if kind == "item":
                n += 1
                if n == self.sel:
                    y, h = yy, hh
                    break
        top, bottom = 0.0, H * 0.95 - H * 0.29
        target = self.scroll
        if y - self.scroll < top:
            target = max(0.0, y - H * 0.05)
        elif y + h - self.scroll > bottom:
            target = y + h - bottom
        target = max(0.0, min(target, max(0.0, total - bottom)))
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self.scroll)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.scroll = target

    # ---- actions
    def current(self):
        return self._items[self.sel] if 0 <= self.sel < len(self._items) else None

    def move(self, dx, dy):
        r = self.current()
        if dy:
            self.sel = max(0, min(len(self._items) - 1, self.sel + dy))
            self._scroll_to_sel()
        elif dx and r:
            self._cycle(r, 1 if dx > 0 else -1)
        self.update()

    def _cycle(self, r, step):
        self.win.roadmap.cycle(r[0], step)
        if self.FILTERS[self.filter_i] != "All":              # it may no longer match the filter
            self.rebuild()
        self.update()

    def activate(self):
        r = self.current()
        if r:
            self._cycle(r, 1)

    def tertiary(self):                                       # X: star / unstar as a priority
        r = self.current()
        if r:
            self.win.roadmap.toggle_star(r[0])
            self.update()

    def shoulder(self, n):
        self.filter_i = (self.filter_i + n) % len(self.FILTERS)
        self.sel, self.scroll = 0, 0.0
        self.rebuild()
        self.update()

    def back(self):
        self.win.close_roadmap()

    # ---- painting
    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W <= 0 or H <= 0:
            return
        self._hits = []
        st = self.win.roadmap
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        bg = QLinearGradient(0, 0, W, H)
        bg.setColorAt(0, QColor(ACCENT).darker(260))
        bg.setColorAt(1, QColor("#0a0714"))
        p.fillRect(QRectF(0, 0, W, H), bg)
        mx = W * 0.055
        _draw_text(p, QRectF(mx, H * 0.045, W * 0.5, H * 0.075), "Roadmap", H * 0.05, "white", True)
        c = st.counts()
        total = sum(c.values())
        _draw_text(p, QRectF(mx, H * 0.115, W * 0.8, H * 0.04),
                   f"{c['Done']} of {total} done   \u2022   {c['In progress']} in progress   \u2022   {c['Planned']} planned   \u2022   {c['Idea']} ideas",
                   H * 0.0205, QColor(255, 255, 255, 175))
        bar = QRectF(mx, H * 0.16, W * 0.4, H * 0.009)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        x = bar.left()
        for s_ in ("Done", "In progress", "Planned"):
            w = bar.width() * c[s_] / max(1, total)
            if w > 0:
                p.setBrush(QColor(STATUS_COLORS[s_]))
                p.drawRoundedRect(QRectF(x, bar.top(), w, bar.height()), bar.height() / 2, bar.height() / 2)
                x += w
        # filters
        fx = mx
        for i, name in enumerate(self.FILTERS):
            n = total if name == "All" else c[name]
            label = f"{name}  {n}"
            p.setFont(_px_font(H * 0.021, True))
            tw = QFontMetrics(p.font()).horizontalAdvance(label)
            sel = i == self.filter_i
            _draw_text(p, QRectF(fx, H * 0.2, tw + 8, H * 0.04), label, H * 0.021,
                       "white" if sel else QColor(255, 255, 255, 125), True)
            if sel:
                p.fillRect(QRectF(fx, H * 0.243, tw, 3), QColor(ACCENT))
            self._hits.append((QRectF(fx, H * 0.2, tw + 8, H * 0.05), ("filter", i)))
            fx += tw + W * 0.03
        # list
        rx, rw = mx, W - 2 * mx
        top = H * 0.29
        p.save()
        p.setClipRect(QRectF(0, top - 6, W, H * 0.95 - top + 6))
        geo, _ = self._geom(H)
        item_n = -1
        for kind, data, y, h in geo:
            yy = top + y - self.scroll
            if kind == "item":
                item_n += 1
            if yy > H or yy + h < top - 10:
                continue
            if kind == "head":
                _draw_text(p, QRectF(rx, yy, rw, h), data.upper(), H * 0.0155, QColor(ACCENT).lighter(150), True, spacing=3)
                continue
            self._row(p, QRectF(rx, yy, rw, h), data, item_n == self.sel, H, item_n)
        if not self._items:
            _draw_text(p, QRectF(0, top + H * 0.1, W, H * 0.06), "Nothing with that status.", H * 0.026,
                       QColor(255, 255, 255, 140), False, Qt.AlignmentFlag.AlignHCenter)
        p.restore()
        self._paint_hints(p, W, H)
        p.end()

    def _row(self, p, r, item, selected, H, idx):
        st = self.win.roadmap
        rid, cat, title, desc = item
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34 if selected else 18))
        p.drawRoundedRect(r, 12, 12)
        if selected:
            glow = QColor(ACCENT)
            glow.setAlpha(90)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(glow, 8))
            p.drawRoundedRect(r, 12, 12)
            p.setPen(QPen(QColor(ACCENT).lighter(125), 3))
            p.drawRoundedRect(r, 12, 12)
        star = st.starred(rid)
        _draw_text(p, QRectF(r.left() + 10, r.top(), r.height() * 0.8, r.height()), "\u2605" if star else "\u2606",
                   H * 0.03, QColor("#ffd54a") if star else QColor(255, 255, 255, 70), False, Qt.AlignmentFlag.AlignHCenter)
        tx = r.left() + r.height() * 0.95
        status = st.status(rid)
        col = QColor(STATUS_COLORS[status])
        chip_w = r.width() * 0.2
        _draw_text(p, QRectF(tx, r.top() + r.height() * 0.1, r.width() - chip_w - (tx - r.left()) - 20, r.height() * 0.45),
                   title, H * 0.0235, "white", True)
        _draw_text(p, QRectF(tx, r.top() + r.height() * 0.52, r.width() - chip_w - (tx - r.left()) - 20, r.height() * 0.38),
                   desc, H * 0.0165, QColor(255, 255, 255, 145))
        chip = QRectF(r.right() - chip_w - 14, r.top() + r.height() * 0.2, chip_w, r.height() * 0.6)
        bgc = QColor(col)
        bgc.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bgc)
        p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
        _draw_text(p, chip, f"\u2039  {status}  \u203a" if selected else status, H * 0.0185, col.lighter(130), True,
                   Qt.AlignmentFlag.AlignHCenter)
        self._hits.append((r, ("row", idx)))

    def _paint_hints(self, p, W, H):
        mx = W * 0.055
        y, h = H * 0.9735, H * 0.021
        if self.win._pad_seen:
            items = [("\u2039 \u203a", "Change status"), ("X", "Star priority"), ("LB RB", "Filter"), ("B", "Back")]
        else:
            items = [("\u2190 \u2192", "Change status"), ("S", "Star priority"), ("PgUp PgDn", "Filter"), ("Esc", "Back")]
        kf, tf = _px_font(H * 0.0125, True), _px_font(H * 0.014)
        x = mx
        for key, text in items:
            kw = max(h, QFontMetrics(kf).horizontalAdvance(key) + h * 0.8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawRoundedRect(QRectF(x, y, kw, h), h / 2, h / 2)
            p.setFont(kf)
            p.setPen(QColor("white"))
            p.drawText(QRectF(x, y, kw, h), Qt.AlignmentFlag.AlignCenter, key)
            x += kw + 8
            p.setFont(tf)
            p.setPen(QColor(255, 255, 255, 175))
            tw = QFontMetrics(tf).horizontalAdvance(text)
            p.drawText(QRectF(x, y, tw + 4, h), Qt.AlignmentFlag.AlignVCenter, text)
            x += tw + h * 1.3

    # ---- input
    def keyPressEvent(self, e):
        K, k = Qt.Key, e.key()
        if k == K.Key_Left:
            self.move(-1, 0)
        elif k == K.Key_Right:
            self.move(1, 0)
        elif k == K.Key_Up:
            self.move(0, -1)
        elif k == K.Key_Down:
            self.move(0, 1)
        elif k in (K.Key_Return, K.Key_Enter, K.Key_Space):
            self.activate()
        elif k == K.Key_S:
            self.tertiary()
        elif k in (K.Key_Escape, K.Key_Backspace):
            self.back()
        elif k == K.Key_PageUp:
            self.shoulder(-1)
        elif k == K.Key_PageDown:
            self.shoulder(1)
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        for rect, (kind, idx) in reversed(self._hits):
            if rect.contains(e.position()):
                if kind == "filter":
                    self.filter_i, self.sel, self.scroll = idx, 0, 0.0
                    self.rebuild()
                elif kind == "row":
                    if idx == self.sel:
                        self.activate()
                    self.sel = idx
                self.update()
                return

    def wheelEvent(self, e):
        self.move(0, -1 if e.angleDelta().y() > 0 else 1)



NAV_ITEMS = ("profile", "games", "apps", "search", "settings")
# (label, key, tag): tag "soon" = placeholder (opens its Roadmap card), "preview" = sample data
PANEL_MENU = [("Home", "home", ""), ("Profile", "profile", ""), ("Friends", "friends", "preview"),
              ("Messages", "messages", "preview"), ("Notifications", "notifications", "soon"),
              ("Parties & voice chat", "parties", "soon"), ("Captures", "captures", "soon"),
              ("Downloads & updates", "downloads", "soon"), ("Wishlist", "wishlist", "soon"),
              ("Games", "games", ""), ("Apps", "apps", ""), ("Controller", "controller", "soon"),
              ("Audio", "audio", "soon"), ("Settings", "settings", ""), ("Power", "power", "soon"),
              ("Help & tips", "help", "soon"), ("Roadmap", "roadmap", ""), ("Exit Big Picture", "exit", "")]
WIDE_TILES = [("Browse your games", "games", ""), ("Apps", "apps", ""), ("Customize your Home", "customize", ""),
              ("Surprise me", "surprise", "soon"), ("Recently added", "recent_added", "soon"),
              ("Backlog", "backlog", "soon"), ("This week", "this_week", "soon"),
              ("Themes & wallpapers", "themes", "soon")]
PROFILE_TABS = [("Overview", "overview"), ("Achievements", "achievements"), ("Activity", "activity"),
                ("Stats", "stats"), ("Badges & titles", "badges"), ("Showcase", "showcase"), ("Goals", "goals"),
                ("Year in Review", "year")]
PROFILE_SKELETON = {"p_activity": "list", "p_stats": "chart", "p_badges": "badges", "p_showcase": "tiles",
                    "p_goals": "goals", "p_year": "hero"}


def is_real_game(g):
    return g.store not in ("App", "Soon")


FRIENDS = [("Alex", "Playing a game", "online"), ("Sam", "Online", "online"),
           ("Jordan", "Away", "away"), ("Riley", "Offline \u2022 2 h ago", "off")]
MESSAGES = [("Alex", "Want to play tonight?", "2 min"), ("Sam", "That last round was wild", "1 h"),
            ("Party invite", "Jordan invited you to a party", "3 h")]


class BigPictureHome(QWidget):
    """Xbox-style Big Picture: art backdrop, profile + icon bar, a featured tile row, shortcut tiles,
    a full Games / Apps grid, and a left side panel (right-stick click) with friends, messages and quick settings."""
    play_requested = Signal(object)
    details_requested = Signal(object)
    favourite_requested = Signal(object)
    options_requested = Signal(object, QPoint)
    menu_requested = Signal()
    panel_soon, wide_scroll, _wide_target = "", 0.0, 0

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.mode, self.zone = "home", "row"            # mode: home | games | apps ; zone (home): nav | row | wide
        self.nav_i, self.row_i, self.wide_i = 1, 0, 0
        self.home_items, self.apps = [], []
        self.games_sections, self.apps_sections = [], []
        self.pos = 0.0                                  # animated position of the featured tile in the row
        self.grid_sel, self.grid_scroll = 0, 0.0
        self.panel_open, self._panel_closing, self.panel_t = False, False, 0.0
        self.panel_level, self.panel_i, self.panel_msg = "menu", 0, ""
        self.bg_cur = self.bg_prev = None
        self.bg_t, self.fade, self.bounce = 1.0, 1.0, 0.0
        self._bg_key, self._bg_ready, self._last_item = None, True, None
        self._hits, self._sel_rect, self._wheel, self._animating = [], QRectF(), 0, True
        self.search_q, self.search_sections = "", []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._pos_anim = self._mk_anim(280, lambda v: self._set("pos", v))
        self._scroll_anim = self._mk_anim(260, lambda v: self._set("grid_scroll", v))
        self._fade_anim = self._mk_anim(300, lambda v: self._set("fade", v))
        self._bg_anim = self._mk_anim(520, lambda v: self._set("bg_t", v))
        self._panel_anim = self._mk_anim(240, self._set_panel_t)
        self._panel_anim.finished.connect(self._panel_done)
        self._wide_anim = self._mk_anim(240, lambda v: self._set("wide_scroll", v))
        self._bounce_anim = QVariantAnimation(self)
        self._bounce_anim.setDuration(1600)
        self._bounce_anim.setStartValue(0.0)
        self._bounce_anim.setEndValue(1.0)
        self._bounce_anim.setLoopCount(-1)
        self._bounce_anim.valueChanged.connect(self._on_bounce)
        win.thumbs.changed.connect(self._thumbs_changed)

    # ------------------------------------------------------------------ animation plumbing
    def _mk_anim(self, ms, slot):
        a = QVariantAnimation(self)
        a.setDuration(ms)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.valueChanged.connect(slot)
        return a

    @staticmethod
    def _restart(anim, a=0.0, b=1.0):
        anim.stop()
        anim.setStartValue(a)
        anim.setEndValue(b)
        anim.start()

    def _set(self, name, v):
        setattr(self, name, float(v))
        self.update()

    def _on_bounce(self, v):                    # soft pulse on the focused tile's glow (repaints only that area)
        self.bounce = float(v)
        if not self._sel_rect.isEmpty() and not self.panel_open:
            self.update(self._sel_rect.toAlignedRect().adjusted(-34, -34, 34, 34))

    def set_animating(self, on):
        self._animating = on
        if on and self.isVisible():
            self._bounce_anim.start()
        else:
            self._bounce_anim.stop()

    def showEvent(self, e):
        super().showEvent(e)
        if self._animating:
            self._bounce_anim.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._bounce_anim.stop()

    # ------------------------------------------------------------------ data
    def rebuild(self):
        keep = self.current_item()
        m = self.win.model
        games = sorted(m.games, key=lambda g: g.name.lower())
        recent = sorted((g for g in games if g.last_played), key=lambda g: g.last_played, reverse=True)[:10]
        seen = {g.id for g in recent}
        favs = [g for g in games if g.favourite and g.id not in seen][:6]
        seen |= {g.id for g in favs}
        rest = [g for g in games if g.id not in seen][:6]
        self.apps = self.win.app_items()
        self.home_items = recent + favs + rest + self.apps
        sections = []
        for f in m.folders:
            gs = list(m.folder_games(f.id))
            if gs:
                sections.append((f.name, gs))
        loose = [g for g in games if not g.folder]
        if loose:
            sections.append(("Games" if not sections else "Other games", loose))
        self.games_sections = sections
        self.apps_sections = ([("Apps", self.apps)] if self.apps else []) + [("Coming soon", self.win.soon_app_items())]
        if self.mode == "home":
            self.row_i = next((i for i, it in enumerate(self.home_items) if keep and it.id == keep.id),
                              min(self.row_i, max(0, len(self.home_items) - 1)))
            self.pos = float(self.row_i)
            self._pos_anim.stop()
        else:
            flat = self._flat()
            self.grid_sel = next((i for i, it in enumerate(flat) if keep and it.id == keep.id),
                                 min(self.grid_sel, max(0, len(flat) - 1)))
        self._bg_key = None
        self._update_bg(fade=False)
        self.update()

    def _secs(self):
        return {"games": self.games_sections, "apps": self.apps_sections, "search": self.search_sections}[self.mode]

    def _flat(self):
        return [it for _, items in self._secs() for it in items]

    def current_item(self):
        if self.mode == "home":
            if self.zone == "row" and 0 <= self.row_i < len(self.home_items):
                return self.home_items[self.row_i]
            return None
        flat = self._flat()
        return flat[self.grid_sel] if 0 <= self.grid_sel < len(flat) else None

    current_game = current_item

    def reset_view(self):
        """Fresh start each time Big Picture opens: home screen, tile row, panel closed."""
        self.mode, self.zone, self.nav_i = "home", "row", 1
        self.panel_open, self._panel_closing, self.panel_t = False, False, 0.0
        self.panel_level, self.panel_i, self.panel_msg = "menu", 0, ""

    def select(self, game):
        """Jump to a game (used when coming back from its game page)."""
        self.mode, self.zone, self.panel_open, self.panel_t = "home", "row", False, 0.0
        for i, it in enumerate(self.home_items):
            if it.id == game.id:
                self.row_i, self.pos = i, float(i)
                self._pos_anim.stop()
                self._update_bg(fade=False)
                self.update()
                return
        self.open_page("games")
        for i, it in enumerate(self._flat()):
            if it.id == game.id:
                self.grid_sel = i
                self._scroll_to_sel(animate=False)
                break

    def _bg_item(self):
        it = self.current_item()
        if it is not None:
            self._last_item = it
        return self._last_item

    def _update_bg(self, fade=True):
        g = self._bg_item()
        key = (g.cover or g.name) if g else None
        if key == self._bg_key:
            return
        self._bg_key = key
        tiny = make_backdrop(self.win.thumbs, g) if g else None
        self._bg_ready = tiny is not None or not (g and g.cover)
        if fade:
            self.bg_prev = self.bg_cur
            self._restart(self._bg_anim)
        else:
            self.bg_prev, self.bg_t = None, 1.0
        self.bg_cur = tiny
        self.update()

    def _thumbs_changed(self):
        if not self._bg_ready:
            self._bg_key = None
            self._update_bg(fade=False)
        self.update()

    # ------------------------------------------------------------------ actions (called by keyboard / mouse / controller)
    def move(self, dx, dy):
        if self.panel_open:
            return self._panel_move(dx, dy)
        if self.mode == "home":
            return self._home_move(dx, dy)
        self._grid_move(dx, dy)

    def _home_move(self, dx, dy):
        if dy:
            order = ("nav", "row", "wide")
            new = order[max(0, min(2, order.index(self.zone) + dy))]
            if new != self.zone:
                self.zone = new
                self._update_bg()
                self.update()
            return
        if dx:
            if self.zone == "nav":
                self.nav_i = max(0, min(len(NAV_ITEMS) - 1, self.nav_i + dx))
            elif self.zone == "row":
                self._row_jump(dx)
            else:
                self.wide_i = max(0, min(len(WIDE_TILES) - 1, self.wide_i + dx))
                self._wide_scroll_to()
            self.update()

    def _row_jump(self, n):
        if not self.home_items:
            return
        new = max(0, min(len(self.home_items) - 1, self.row_i + n))
        if new != self.row_i:
            self.row_i = new
            self._restart(self._pos_anim, self.pos, float(new))
            self._update_bg()

    def shoulder(self, n):
        if self.panel_open:
            return
        if self.mode == "home":
            if self.zone == "row":
                self._row_jump(5 * n)
        else:
            self._grid_move(0, n * 3)

    def activate(self):
        if self.panel_open:
            return self._panel_activate()
        if self.mode == "home":
            if self.zone == "nav":
                return self._nav_activate(self.nav_i)
            if self.zone == "wide":
                return self._wide_activate(self.wide_i)
        it = self.current_item()
        if it is not None:
            if it.store == "Soon":
                self.win.open_roadmap(it.id.split(":", 1)[1])
            else:
                self.play_requested.emit(it)

    def _nav_activate(self, i):
        name = NAV_ITEMS[i]
        if name == "profile":
            self.win.open_profile()
        elif name in ("games", "apps"):
            self.open_page(name)
        elif name == "search":
            self.win.text_input("Search games and apps", self.search_q, self._search_done, False, 40)
        else:
            self.open_panel("settings")

    def _search_done(self, text):
        if text is None or not text.strip():
            return
        self.search_q = text.strip()
        q = self.search_q.lower()
        items = [g for g in self.win.model.games if q in g.name.lower()] + [a for a in self.apps if q in a.name.lower()]
        items.sort(key=lambda g: g.name.lower())
        self.search_sections = [(f"Results for \u201c{self.search_q}\u201d", items)]
        self.open_page("search")

    def _wide_activate(self, i):
        key = WIDE_TILES[i][1]
        if key == "games":
            self.open_page("games")
        elif key == "apps":
            self.open_page("apps")
        elif key == "customize":
            self.open_panel("settings")
        else:
            self.win.open_roadmap(key)           # placeholder tile: jump to its Roadmap entry

    def open_page(self, mode):
        self.mode, self.grid_sel, self.grid_scroll = mode, 0, 0.0
        self._scroll_anim.stop()
        self._restart(self._fade_anim)
        self._update_bg()
        self.update()

    def back(self):
        if self.panel_open:
            if self.panel_level != "menu":
                self.panel_level, self.panel_i, self.panel_msg = "menu", 0, ""
                self.update()
            else:
                self.close_panel()
            return
        if self.mode != "home":
            self.mode, self.zone = "home", "row"
            self._update_bg()
            self.update()
        elif self.zone != "row":
            self.zone = "row"
            self._update_bg()
            self.update()

    def secondary(self):                              # Y: game page
        it = self.current_item()
        if it is not None and is_real_game(it) and not self.panel_open:
            self.details_requested.emit(it)

    def tertiary(self):                               # X: favourite
        it = self.current_item()
        if it is not None and is_real_game(it) and not self.panel_open:
            self.favourite_requested.emit(it)

    def options(self, global_pos=None):               # Start: the game's options menu
        it = self.current_item()
        if it is None or not is_real_game(it) or self.panel_open:
            return
        if global_pos is None:
            c = self._sel_rect.center() if not self._sel_rect.isEmpty() else QPointF(self.width() / 2, self.height() / 2)
            global_pos = self.mapToGlobal(c.toPoint())
        self.options_requested.emit(it, global_pos)

    # ------------------------------------------------------------------ side panel
    def toggle_panel(self):
        if self.panel_open and not self._panel_closing:
            self.close_panel()
        else:
            self.open_panel("menu")

    def open_panel(self, level="menu"):
        self.panel_open, self._panel_closing = True, False
        self.panel_level, self.panel_i, self.panel_msg = level, 0, ""
        self._restart(self._panel_anim, self.panel_t, 1.0)

    def close_panel(self):
        self._panel_closing = True
        self._restart(self._panel_anim, self.panel_t, 0.0)

    def _set_panel_t(self, v):
        self.panel_t = float(v)
        self.update()

    def _panel_done(self):
        if self._panel_closing:
            self.panel_open, self._panel_closing, self.panel_t = False, False, 0.0
            self.update()

    def _settings_spec(self):
        return [
            {"label": "Accent colour", "key": "accent", "kind": "choice", "default": ACCENT,
             "options": list(ACCENT_PRESETS.items())},
            {"label": "Transition animation", "key": "animations", "kind": "bool", "default": True},
            {"label": "Show playtime", "key": "show_playtime", "kind": "bool", "default": True},
            {"label": "When I close the window", "key": "close_action", "kind": "choice", "default": "quit",
             "options": [("Quit completely", "quit"), ("Keep in tray", "tray")]},
            {"label": "Start with Windows", "key": "startup", "kind": "bool", "default": False, "live": startup_enabled},
            {"label": "Hide launcher while playing", "key": "hide_on_launch", "kind": "bool", "default": True},
            {"label": "Controller support warning", "key": "pad_warn", "kind": "bool", "default": True},
            {"label": "YouTube in a window", "key": "yt_windowed", "kind": "bool", "default": False},
            {"label": "Quick Resume", "kind": "soon", "id": "s_quick_resume"},
            {"label": "Screensaver & idle dimming", "kind": "soon", "id": "s_screensaver"},
            {"label": "Sound effects", "kind": "soon", "id": "s_sounds"},
            {"label": "Language", "kind": "soon", "id": "s_language"},
            {"label": "Backup & restore", "kind": "soon", "id": "s_backup"},
            {"label": "Check for updates", "kind": "soon", "id": "s_updates"},
            {"label": "Parental controls", "kind": "soon", "id": "s_parental"},
            {"label": "All settings\u2026", "kind": "action", "action": "open_settings"},
        ]

    def _setting_value(self, spec):
        """-> (text, swatch colour or None)"""
        w = self.win
        if spec["kind"] == "bool":
            on = spec["live"]() if "live" in spec else w.cfg_bool(spec["key"], spec["default"])
            return ("On" if on else "Off"), None
        if spec["kind"] == "choice":
            cur = str(w.settings.value(spec["key"], spec["default"])).lower()
            for name, val in spec["options"]:
                if str(val).lower() == cur:
                    return name, (val if spec["key"] == "accent" else None)
            return spec["options"][0][0], None
        return "", None

    def _change_setting(self, spec, step):
        w = self.win
        if spec["kind"] == "bool":
            on = spec["live"]() if "live" in spec else w.cfg_bool(spec["key"], spec["default"])
            new = (not on) if step == 0 else (step > 0)
            w.settings.setValue(spec["key"], new)
        elif spec["kind"] == "choice":
            vals = [str(v).lower() for _, v in spec["options"]]
            cur = str(w.settings.value(spec["key"], spec["default"])).lower()
            i = vals.index(cur) if cur in vals else 0
            i = (i + (step or 1)) % len(vals)
            w.settings.setValue(spec["key"], spec["options"][i][1])
        else:
            return
        w.apply_settings(spec["key"])
        self.update()

    def _panel_rows(self):
        return {"menu": len(PANEL_MENU), "friends": len(FRIENDS), "messages": len(MESSAGES),
                "settings": len(self._settings_spec()), "soon": 1}[self.panel_level]

    def _panel_move(self, dx, dy):
        n = self._panel_rows()
        if dy:
            self.panel_i = max(0, min(n - 1, self.panel_i + dy))
        if dx and self.panel_level == "settings":
            spec = self._settings_spec()[self.panel_i]
            if spec["kind"] != "action":
                self._change_setting(spec, 1 if dx > 0 else -1)
        elif dx > 0 and self.panel_level == "menu":
            pass
        self.update()

    def _panel_activate(self):
        lvl = self.panel_level
        if lvl == "menu":
            label, key, tag = PANEL_MENU[self.panel_i]
            if key == "home":
                self.close_panel()
            elif key == "profile":
                self.close_panel()
                QTimer.singleShot(0, self.win.open_profile)
            elif key == "roadmap":
                self.close_panel()
                QTimer.singleShot(0, self.win.open_roadmap)
            elif key in ("friends", "messages", "settings"):
                self.panel_level, self.panel_i, self.panel_msg = key, 0, ""
            elif key in ("games", "apps"):
                self.close_panel()
                self.open_page(key)
            elif tag == "soon":
                self.panel_level, self.panel_soon, self.panel_i, self.panel_msg = "soon", key, 0, ""
            else:
                self.close_panel()
                QTimer.singleShot(0, self.win.exit_big_picture)
        elif lvl == "soon":
            rid = self.panel_soon
            self.close_panel()
            QTimer.singleShot(0, lambda: self.win.open_roadmap(rid))
        elif lvl == "settings":
            spec = self._settings_spec()[self.panel_i]
            if spec["kind"] == "action":
                self.close_panel()
                QTimer.singleShot(0, self.win.open_settings)
            elif spec["kind"] == "soon":
                rid = spec["id"]
                self.close_panel()
                QTimer.singleShot(0, lambda: self.win.open_roadmap(rid))
            else:
                self._change_setting(spec, 0 if spec["kind"] == "bool" else 1)
        else:
            self.panel_msg = "Friends and messages are previews with sample data \u2013 online features aren't connected yet."
        self.update()

    # ------------------------------------------------------------------ games / apps grid
    def _grid_layout(self, W, H):
        secs = self._secs()
        mx, t, g = W * 0.054, H * 0.125, H * 0.018
        cols = max(3, int((W - 2 * mx + g) // (t + g)))
        y, items, heads = H * 0.19, [], []
        for title, its in secs:
            heads.append((title, y))
            y += H * 0.06
            for k, it in enumerate(its):
                r, c = divmod(k, cols)
                items.append((it, mx + c * (t + g), y + r * (t + g)))
            y += -(-len(its) // cols) * (t + g) + H * 0.025
        return t, items, heads, y

    def _scroll_to_sel(self, animate=True):
        W, H = self.width(), self.height()
        t, items, _, total = self._grid_layout(W, H)
        if not items or not (0 <= self.grid_sel < len(items)):
            return
        y = items[self.grid_sel][2]
        top, bottom = H * 0.2, H * 0.9
        target = self.grid_scroll
        if y - self.grid_scroll < top:
            target = y - top
        elif y + t - self.grid_scroll > bottom:
            target = y + t - bottom
        target = max(0.0, min(target, max(0.0, total - H * 0.9)))
        if animate:
            self._restart(self._scroll_anim, self.grid_scroll, target)
        else:
            self._scroll_anim.stop()
            self.grid_scroll = target
        self.update()

    def _grid_move(self, dx, dy):
        W, H = self.width(), self.height()
        t, items, _, _ = self._grid_layout(W, H)
        if not items:
            return
        i = max(0, min(len(items) - 1, self.grid_sel))
        if dx:
            i = max(0, min(len(items) - 1, i + dx))
        for _ in range(abs(dy)):
            cy, cx = items[i][2], items[i][1]
            if dy > 0:
                cand = [k for k, it in enumerate(items) if it[2] > cy + 1]
                if not cand:
                    break
                ny = min(items[k][2] for k in cand)
            else:
                cand = [k for k, it in enumerate(items) if it[2] < cy - 1]
                if not cand:
                    break
                ny = max(items[k][2] for k in cand)
            i = min((k for k in cand if abs(items[k][2] - ny) < 1), key=lambda k: abs(items[k][1] - cx))
        if i != self.grid_sel:
            self.grid_sel = i
            self._update_bg()
            self._scroll_to_sel()
        self.update()

    # ------------------------------------------------------------------ painting helpers
    def _card(self, p, r, radius=14):
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.setClipPath(path)

    def _ring(self, p, r, radius, width=4.0, pulse=True):
        glow = QColor(ACCENT)
        glow.setAlpha(int(70 + (40 * math.sin(self.bounce * 2 * math.pi) if pulse else 0)))
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(glow, width * 2.6))
        p.drawRoundedRect(r, radius, radius)
        p.setPen(QPen(QColor(ACCENT).lighter(125), width))
        p.drawRoundedRect(r, radius, radius)

    def _text(self, p, rect, text, px, color="white", bold=False, align=Qt.AlignmentFlag.AlignLeft, spacing=0):
        p.setFont(_px_font(px, bold, spacing))
        p.setPen(QColor(color) if isinstance(color, str) else color)
        fm = QFontMetrics(p.font())
        p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(rect.width()))))

    # icons (drawn, so no font/emoji dependency)
    def _icon_library(self, p, c, s, col):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        w, h = s * 0.2, s * 0.9
        for k in range(2):
            p.drawRoundedRect(QRectF(c.x() - s * 0.5 + k * s * 0.3, c.y() - h / 2, w, h), 2.5, 2.5)
        p.translate(c.x() + s * 0.32, c.y())
        p.rotate(-16)
        p.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), 2.5, 2.5)
        p.restore()

    def _icon_apps(self, p, c, s, col):
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, max(2.0, s * 0.09)))
        p.drawRoundedRect(QRectF(c.x() - s * 0.5, c.y() - s * 0.5, s, s), s * 0.18, s * 0.18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        q = s * 0.2
        for dx in (-1, 1):
            for dy in (-1, 1):
                p.drawRoundedRect(QRectF(c.x() + dx * s * 0.17 - q / 2, c.y() + dy * s * 0.17 - q / 2, q, q), 2, 2)
        p.restore()

    def _icon_gear(self, p, c, s, col):
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(col, max(2.0, s * 0.1))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(c, s * 0.3, s * 0.3)
        p.drawEllipse(c, s * 0.12, s * 0.12)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * s * 0.36, c.y() + math.sin(a) * s * 0.36),
                       QPointF(c.x() + math.cos(a) * s * 0.5, c.y() + math.sin(a) * s * 0.5))
        p.restore()

    def _icon_pad(self, p, c, s, col):
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, max(2.0, s * 0.08)))
        p.drawRoundedRect(QRectF(c.x() - s * 0.5, c.y() - s * 0.28, s, s * 0.56), s * 0.26, s * 0.26)
        p.drawEllipse(QPointF(c.x() - s * 0.2, c.y() - s * 0.02), s * 0.08, s * 0.08)
        p.drawEllipse(QPointF(c.x() + s * 0.2, c.y() + s * 0.06), s * 0.08, s * 0.08)
        p.restore()

    def _icon_brush(self, p, c, s, col):
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(col, max(2.0, s * 0.07))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(c.x() - s * 0.5, c.y() - s * 0.4, s, s * 0.7), s * 0.08, s * 0.08)
        p.drawLine(QPointF(c.x() - s * 0.2, c.y() + s * 0.5), QPointF(c.x() + s * 0.2, c.y() + s * 0.5))
        p.drawLine(QPointF(c.x(), c.y() + s * 0.3), QPointF(c.x(), c.y() + s * 0.5))
        p.drawLine(QPointF(c.x() - s * 0.12, c.y() + s * 0.1), QPointF(c.x() + s * 0.26, c.y() - s * 0.28))
        p.restore()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W <= 0 or H <= 0:
            return
        self._hits, self._sel_rect = [], QRectF()
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        full = QRectF(0, 0, W, H)
        p.fillRect(full, QColor("#0a0714"))
        g = self._bg_item()
        if self.bg_prev is not None and self.bg_t < 1.0:
            paint_backdrop(p, full, self.bg_prev, (1 - self.bg_t) * 0.95)
        paint_backdrop(p, full, self.bg_cur, self.bg_t * 0.95, g if self.bg_cur is None else None)
        top = QLinearGradient(0, 0, 0, H * 0.3)
        top.setColorAt(0, QColor(6, 4, 14, 170))
        top.setColorAt(1, QColor(6, 4, 14, 0))
        p.fillRect(QRectF(0, 0, W, H * 0.3), top)
        bot = QLinearGradient(0, H * 0.42, 0, H)
        bot.setColorAt(0, QColor(6, 4, 14, 0))
        bot.setColorAt(1, QColor(6, 4, 14, 215))
        p.fillRect(QRectF(0, H * 0.42, W, H * 0.58), bot)
        if self.mode == "home":
            self._paint_header(p, W, H)
            self._paint_row(p, W, H)
            self._paint_wide(p, W, H)
        else:
            self._paint_page(p, W, H)
        self._paint_hints(p, W, H)
        if self.panel_open:
            self._paint_panel(p, W, H)
        p.end()

    # ---- header: profile, icon bar, clock
    def _paint_header(self, p, W, H):
        mx = W * 0.054
        r = H * 0.027
        cy = H * 0.105
        pr = self.win.profile
        av = QRectF(mx, cy - r, 2 * r, 2 * r)
        paint_avatar(p, av, self.win)
        if self.zone == "nav" and self.nav_i == 0:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(ACCENT).lighter(125), 3.5))
            p.drawEllipse(av.adjusted(-5, -5, 5, 5))
        self._hits.append((av, ("nav", 0)))
        nx = mx + 2 * r + H * 0.018
        self._text(p, QRectF(nx, cy - r - 2, W * 0.28, r), pr.name, H * 0.023, "white")
        gr = H * 0.0095
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f5c542"))
        p.drawEllipse(QPointF(nx + gr, cy + r * 0.52), gr, gr)
        self._text(p, QRectF(nx, cy + r * 0.52 - gr, gr * 2, gr * 2), "G", H * 0.0125, "#2b2200", True,
                   Qt.AlignmentFlag.AlignHCenter)
        self._text(p, QRectF(nx + gr * 2 + 8, cy + 1, W * 0.26, r), f"{pr.points():,}   \u2022   Level {pr.level_info()[0]}",
                   H * 0.0185, QColor(255, 255, 255, 190))
        # icon bar (centre)
        icons = (("games", self._icon_library), ("apps", self._icon_apps), ("search", self._icon_search),
                 ("settings", self._icon_gear))
        step, rad = W * 0.046, H * 0.029
        for k, (name, fn) in enumerate(icons):
            c = QPointF(W / 2 + (k - (len(icons) - 1) / 2) * step, cy)
            sel = self.zone == "nav" and self.nav_i == k + 1
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ACCENT) if sel else QColor(255, 255, 255, 34))
            rr = rad * (1.12 if sel else 1.0)
            p.drawEllipse(c, rr, rr)
            if sel:
                glow = QColor(ACCENT)
                glow.setAlpha(80)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(glow, 9))
                p.drawEllipse(c, rr + 3, rr + 3)
            fn(p, c, rad * 0.95, QColor("white"))
            self._hits.append((QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), ("nav", k + 1)))
        # clock (right)
        p.setFont(_px_font(H * 0.034))
        clock = time.strftime("%I:%M %p").lstrip("0").lower()
        cw = QFontMetrics(p.font()).horizontalAdvance(clock)
        self._text(p, QRectF(W - mx - cw - 4, cy - H * 0.025, cw + 8, H * 0.05), clock, H * 0.034, "white")
        if self.win._pad_seen:
            self._icon_pad(p, QPointF(W - mx - cw - H * 0.05, cy), H * 0.04, QColor(255, 255, 255, 210))

    def _icon_search(self, p, c, s, col):
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(col, max(2.0, s * 0.1))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(QPointF(c.x() - s * 0.08, c.y() - s * 0.08), s * 0.3, s * 0.3)
        p.drawLine(QPointF(c.x() + s * 0.14, c.y() + s * 0.14), QPointF(c.x() + s * 0.46, c.y() + s * 0.46))
        p.restore()

    # ---- featured tile row
    def _row_rect(self, i, W, H):
        mx, small, big = W * 0.054, H * 0.1425, H * 0.24
        gap, bottom = W * 0.0125, H * 0.731
        d = i - self.pos
        focus = _clamp(1 - abs(d))
        s = small + (big - small) * focus
        x = mx + d * (small + gap) + (big - small) * _clamp(d)
        return QRectF(x, bottom - s, s, s), focus

    def _paint_row(self, p, W, H):
        if not self.home_items:
            return
        n = len(self.home_items)
        first = max(0, int(math.floor(self.pos)) - 2)
        last = min(n - 1, int(math.ceil(self.pos + W / (H * 0.17))) + 1)
        for i in sorted(range(first, last + 1), key=lambda i: -abs(i - self.pos)):
            it = self.home_items[i]
            r, focus = self._row_rect(i, W, H)
            if r.right() < -10 or r.left() > W:
                continue
            d = i - self.pos
            p.save()
            p.setOpacity(1.0 if d > -0.6 else 0.45)
            if focus > 0.02:                                   # soft shadow under the featured tile
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, int(110 * focus)))
                p.drawRoundedRect(r.adjusted(-4, 8, 4, 16), 18, 18)
            self._card(p, r, 12 + 6 * focus)
            self.win.covers.draw(p, r, it)
            if focus > 0.3:                                    # title band inside the featured tile
                band = QRectF(r.left(), r.bottom() - H * 0.06, r.width(), H * 0.06)
                p.fillRect(band, QColor(0, 0, 0, int(150 * focus)))
                p.setOpacity(focus)
                self._text(p, band.adjusted(H * 0.018, 0, -H * 0.01, 0), it.name, H * 0.0285, "white")
                p.setOpacity(1.0)
            if it.favourite and is_real_game(it):
                self._text(p, QRectF(r.right() - H * 0.045, r.top() + 3, H * 0.04, H * 0.04), "\u2605", H * 0.024,
                           "#ffd54a", False, Qt.AlignmentFlag.AlignHCenter)
            if self.zone == "row" and focus > 0.5:
                self._ring(p, r, 18)
                self._sel_rect = r
            p.restore()
            self._hits.append((r, ("row", i)))

    # ---- shortcut tiles under the row
    def _wide_rect(self, i, W, H):
        mx, gap = W * 0.054, W * 0.02
        w = (W - 2 * mx - 2 * gap) / 3
        return QRectF(mx + (i - self.wide_scroll) * (w + gap), H * 0.76, w, H * 0.208)

    def _wide_scroll_to(self):
        t = self._wide_target
        if self.wide_i < t:
            t = self.wide_i
        elif self.wide_i > t + 2:
            t = self.wide_i - 2
        t = max(0, min(len(WIDE_TILES) - 3, t))
        if t != self._wide_target:
            self._wide_target = t
            self._restart(self._wide_anim, self.wide_scroll, float(t))

    def _icon_clock(self, p, c, s, col):
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(col, max(2.0, s * 0.08))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(c, s * 0.5, s * 0.5)
        p.drawLine(c, QPointF(c.x(), c.y() - s * 0.3))
        p.drawLine(c, QPointF(c.x() + s * 0.22, c.y() + s * 0.1))
        p.restore()

    def _paint_wide(self, p, W, H):
        n = len(WIDE_TILES)
        for i in range(n):
            r = self._wide_rect(i, W, H)
            if r.right() < 0 or r.left() > W:
                continue
            label, key, tag = WIDE_TILES[i]
            sel = self.zone == "wide" and self.wide_i == i
            if sel:
                r = r.adjusted(-W * 0.004, -H * 0.006, W * 0.004, H * 0.006)
                self._sel_rect = r
            p.save()
            self._card(p, r, 14)
            if key == "games":
                games = [x for x in self.home_items if x.store != "App"][:4]
                strip = r.width() / 4
                p.fillRect(r, QColor("#10101a"))
                for k, it in enumerate(games):
                    self.win.covers.draw(p, QRectF(r.left() + k * strip, r.top(), strip + 1, r.height()), it)
                p.fillRect(r, QColor(0, 0, 0, 70))
                self._icon_pad(p, r.center(), H * 0.07, QColor("white"))
            elif key == "apps":
                bg = QLinearGradient(r.topLeft(), r.bottomRight())
                bg.setColorAt(0, QColor("#2a2540"))
                bg.setColorAt(1, QColor("#12101f"))
                p.fillRect(r, bg)
                sz = r.height() * 0.5
                shown = self.apps[:4]
                total = len(shown) * sz + (len(shown) - 1) * 14
                for k, it in enumerate(shown):
                    cell = QRectF(r.center().x() - total / 2 + k * (sz + 14), r.top() + r.height() * 0.14, sz, sz)
                    self._card(p, cell, 12)
                    self.win.covers.draw(p, cell, it)
            elif key == "customize":
                bg = QLinearGradient(r.topLeft(), r.bottomRight())
                bg.setColorAt(0, QColor("#0f3b4a"))
                bg.setColorAt(1, QColor("#0a1c2c"))
                p.fillRect(r, bg)
                self._icon_brush(p, QPointF(r.center().x(), r.top() + r.height() * 0.36), H * 0.075, QColor("white"))
            else:                                                  # placeholder tile
                bg = QLinearGradient(r.topLeft(), r.bottomRight())
                bg.setColorAt(0, QColor("#262436"))
                bg.setColorAt(1, QColor("#13111d"))
                p.fillRect(r, bg)
                self._icon_clock(p, QPointF(r.center().x(), r.top() + r.height() * 0.36), H * 0.06, QColor(255, 255, 255, 110))
            p.setClipping(False)
            lab = QRectF(r.left() + r.width() * 0.045, r.bottom() - H * 0.07, r.width() * 0.9, H * 0.05)
            shade = QLinearGradient(0, r.bottom() - H * 0.1, 0, r.bottom())
            shade.setColorAt(0, QColor(0, 0, 0, 0))
            shade.setColorAt(1, QColor(0, 0, 0, 150))
            p.fillRect(QRectF(r.left(), r.bottom() - H * 0.1, r.width(), H * 0.1), shade)
            self._text(p, lab, label, H * 0.0285, "white" if not tag else QColor(255, 255, 255, 190))
            if tag:
                self._chip(p, QRectF(r.left() + r.width() * 0.02, r.top() + H * 0.026, r.width() - r.width() * 0.04, 0),
                           "COMING SOON", H)
            if sel:
                self._ring(p, r, 14)
            p.restore()
            self._hits.append((r, ("wide", i)))
        mx = W * 0.054
        if self._wide_target + 3 < n:
            self._text(p, QRectF(W - mx * 0.8, H * 0.8, mx * 0.7, H * 0.1), "\u203a", H * 0.06,
                       QColor(255, 255, 255, 150), False, Qt.AlignmentFlag.AlignHCenter)
        if self._wide_target > 0:
            self._text(p, QRectF(mx * 0.1, H * 0.8, mx * 0.7, H * 0.1), "\u2039", H * 0.06,
                       QColor(255, 255, 255, 150), False, Qt.AlignmentFlag.AlignHCenter)

    # ---- full Games / Apps grid
    def _paint_page(self, p, W, H):
        mx = W * 0.054
        title = {"games": "Games", "apps": "Apps", "search": "Search"}[self.mode]
        flat = self._flat()
        self._text(p, QRectF(mx, H * 0.055, W * 0.5, H * 0.07), title, H * 0.045, "white", True)
        p.setFont(_px_font(H * 0.045, True))
        tw = QFontMetrics(p.font()).horizontalAdvance(title)
        self._text(p, QRectF(mx + tw + H * 0.025, H * 0.065, W * 0.3, H * 0.05),
                   f"{len(flat)}", H * 0.026, QColor(255, 255, 255, 150))
        t, items, heads, total = self._grid_layout(W, H)
        if not items:
            self._text(p, QRectF(0, H * 0.4, W, H * 0.1), "Nothing here yet", H * 0.03, QColor(255, 255, 255, 160),
                       False, Qt.AlignmentFlag.AlignHCenter)
            return
        p.save()
        p.setClipRect(QRectF(0, H * 0.14, W, H * 0.84))
        sc = self.grid_scroll
        for title_s, y in heads:
            yy = y - sc
            if -H * 0.1 < yy < H:
                self._text(p, QRectF(mx, yy, W * 0.6, H * 0.05), title_s, H * 0.024, QColor(255, 255, 255, 190), True)
        for idx, (it, x, y) in enumerate(items):
            yy = y - sc
            if yy > H or yy + t < H * 0.14:
                continue
            r = QRectF(x, yy, t, t)
            sel = idx == self.grid_sel
            if sel:
                r = r.adjusted(-t * 0.06, -t * 0.06, t * 0.06, t * 0.06)
                self._sel_rect = r
            p.save()
            self._card(p, r, 12)
            self.win.covers.draw(p, r, it)
            if it.favourite and is_real_game(it):
                self._text(p, QRectF(r.right() - t * 0.3, r.top() + 2, t * 0.28, t * 0.28), "\u2605", t * 0.18, "#ffd54a",
                           False, Qt.AlignmentFlag.AlignHCenter)
            if sel:
                band = QRectF(r.left(), r.bottom() - t * 0.28, r.width(), t * 0.28)
                p.fillRect(band, QColor(0, 0, 0, 165))
                self._text(p, band.adjusted(t * 0.07, 0, -t * 0.05, 0), it.name, t * 0.12, "white")
                self._ring(p, r, 14, 3.5)
            p.restore()
            self._hits.append((r, ("grid", idx)))
        p.restore()

    # ---- hint bar
    def _paint_hints(self, p, W, H):
        mx = W * 0.054
        y, h = H * 0.9735, H * 0.021
        if self.panel_open:
            items = [("A", "Select"), ("B", "Back"), ("\u2039 \u203a", "Change")] if self.win._pad_seen else \
                    [("Enter", "Select"), ("Esc", "Back"), ("\u2190 \u2192", "Change")]
        elif self.mode != "home":
            items = [("A", "Play"), ("B", "Back"), ("Y", "Details"), ("X", "Favourite")] if self.win._pad_seen else \
                    [("Enter", "Play"), ("Esc", "Back"), ("I", "Details"), ("F", "Favourite")]
        elif self.win._pad_seen:
            items = [("A", "Play"), ("Y", "Details"), ("X", "Favourite"), ("R3", "Panel"), ("Select", "Menu")]
        else:
            items = [("Enter", "Play"), ("I", "Details"), ("F", "Favourite"), ("Tab", "Panel"), ("Esc", "Menu")]
        kf, tf = _px_font(H * 0.0125, True), _px_font(H * 0.014)
        x = mx
        for key, text in items:
            kw = max(h, QFontMetrics(kf).horizontalAdvance(key) + h * 0.8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawRoundedRect(QRectF(x, y, kw, h), h / 2, h / 2)
            p.setFont(kf)
            p.setPen(QColor("white"))
            p.drawText(QRectF(x, y, kw, h), Qt.AlignmentFlag.AlignCenter, key)
            x += kw + 8
            p.setFont(tf)
            p.setPen(QColor(255, 255, 255, 175))
            tw = QFontMetrics(tf).horizontalAdvance(text)
            p.drawText(QRectF(x, y, tw + 4, h), Qt.AlignmentFlag.AlignVCenter, text)
            x += tw + h * 1.3

    # ---- left side panel
    def _paint_panel(self, p, W, H):
        t = _ease_out_cubic(_clamp(self.panel_t))
        pw = W * 0.3
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, int(150 * t)))
        x0 = -pw * (1 - t)
        panel = QRectF(x0, 0, pw, H)
        bg = QLinearGradient(panel.topLeft(), panel.bottomRight())
        bg.setColorAt(0, QColor(20, 18, 28, 246))
        bg.setColorAt(1, QColor(11, 10, 18, 250))
        p.fillRect(panel, bg)
        p.fillRect(QRectF(x0 + pw - 3, 0, 3, H), QColor(ACCENT))
        self._hits.append((QRectF(x0 + pw, 0, W, H), ("panel_close", 0)))
        pad = pw * 0.07
        # profile block
        pr = self.win.profile
        r = H * 0.032
        av = QRectF(x0 + pad, H * 0.05, 2 * r, 2 * r)
        paint_avatar(p, av, self.win)
        self._text(p, QRectF(av.right() + H * 0.02, av.top() - 2, pw * 0.6, r), pr.name, H * 0.026, "white", True)
        self._text(p, QRectF(av.right() + H * 0.02, av.center().y() + 2, pw * 0.6, r),
                   f"G {pr.points():,}  \u2022  Level {pr.level_info()[0]}", H * 0.017, QColor(255, 255, 255, 170))
        lvl = self.panel_level
        if lvl == "soon":
            self._paint_panel_soon(p, x0, pw, pad, H)
            return
        top = H * 0.17
        row_h = H * 0.062
        titles = {"menu": "", "friends": "Friends", "messages": "Messages", "settings": "Settings"}
        if titles[lvl]:
            self._text(p, QRectF(x0 + pad, top - H * 0.005, pw * 0.8, H * 0.05), titles[lvl], H * 0.03, "white", True)
            top += H * 0.06
        if lvl == "menu":
            rows = [(label, (key, tag)) for label, key, tag in PANEL_MENU]
        elif lvl == "friends":
            online = sum(1 for f in FRIENDS if f[2] == "online")
            self._text(p, QRectF(x0 + pad, top - H * 0.012, pw * 0.8, H * 0.03), f"{online} online  \u2022  preview",
                       H * 0.0165, QColor(255, 255, 255, 140))
            top += H * 0.025
            rows = [(f[0], f) for f in FRIENDS]
        elif lvl == "messages":
            rows = [(m[0], m) for m in MESSAGES]
        else:
            rows = [(sp["label"], sp) for sp in self._settings_spec()]
        n = len(rows)
        vis = max(3, int((H * 0.95 - top) // row_h))
        first = max(0, min(n - vis, self.panel_i - vis // 2)) if n > vis else 0
        if first > 0:
            self._text(p, QRectF(x0, top - H * 0.03, pw, H * 0.025), "\u25b2", H * 0.014, QColor(255, 255, 255, 110),
                       False, Qt.AlignmentFlag.AlignHCenter)
        if first + vis < n:
            self._text(p, QRectF(x0, H * 0.955, pw, H * 0.02), "\u25bc", H * 0.014, QColor(255, 255, 255, 110),
                       False, Qt.AlignmentFlag.AlignHCenter)
        for i in range(first, min(n, first + vis)):
            label, data = rows[i]
            row = QRectF(x0 + pad * 0.6, top + (i - first) * row_h, pw - pad * 1.2, row_h * 0.92)
            sel = i == self.panel_i
            if sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 34))
                p.drawRoundedRect(row, 10, 10)
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(QRectF(row.left(), row.top() + row.height() * 0.2, 4, row.height() * 0.6), 2, 2)
            self._hits.append((row, ("panel", i)))
            inner = row.adjusted(pad * 0.7, 0, -pad * 0.5, 0)
            if lvl == "menu":
                key, tag = data
                white = "white" if sel else QColor(255, 255, 255, 135 if tag == "soon" else 200)
                self._text(p, QRectF(inner.left(), inner.top(), inner.width() * (0.66 if tag else 1.0), inner.height()),
                           label, H * 0.0245, white)
                if tag:
                    self._chip(p, inner, "SOON" if tag == "soon" else "PREVIEW", H)
            elif lvl == "friends":
                col = {"online": "#3ecf5d", "away": "#f5b83d", "off": "#7b7b88"}[data[2]]
                c = QPointF(inner.left() + H * 0.022, row.center().y())
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor.fromHsl(sum(map(ord, data[0])) * 37 % 360, 120, 90))
                p.drawEllipse(c, H * 0.022, H * 0.022)
                p.setBrush(QColor(col))
                p.drawEllipse(QPointF(c.x() + H * 0.016, c.y() + H * 0.016), H * 0.007, H * 0.007)
                self._text(p, QRectF(inner.left() + H * 0.06, row.top() + 4, inner.width(), row.height() * 0.5), data[0],
                           H * 0.0235, "white", True)
                self._text(p, QRectF(inner.left() + H * 0.06, row.center().y(), inner.width(), row.height() * 0.45),
                           data[1], H * 0.0165, QColor(255, 255, 255, 150))
            elif lvl == "messages":
                self._text(p, QRectF(inner.left(), row.top() + 4, inner.width() * 0.7, row.height() * 0.5), data[0],
                           H * 0.0235, "white", True)
                self._text(p, QRectF(inner.left(), row.center().y(), inner.width(), row.height() * 0.45), data[1],
                           H * 0.0165, QColor(255, 255, 255, 150))
                self._text(p, QRectF(inner.right() - inner.width() * 0.25, row.top() + 4, inner.width() * 0.25,
                                     row.height() * 0.5), data[2], H * 0.015, QColor(255, 255, 255, 120),
                           False, Qt.AlignmentFlag.AlignRight)
            else:
                text, swatch = self._setting_value(data)
                soon = data["kind"] == "soon"
                self._text(p, QRectF(inner.left(), row.top(), inner.width() * 0.62, row.height()), label, H * 0.0215,
                           "white" if sel else QColor(255, 255, 255, 120 if soon else 205))
                if soon:
                    self._chip(p, inner, "SOON", H)
                elif data["kind"] != "action":
                    vr = QRectF(inner.left() + inner.width() * 0.52, row.top(), inner.width() * 0.48, row.height())
                    if swatch:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(swatch))
                        p.drawEllipse(QPointF(vr.left() + H * 0.012, vr.center().y()), H * 0.011, H * 0.011)
                        vr = vr.adjusted(H * 0.03, 0, 0, 0)
                    shown = f"\u2039  {text}  \u203a" if sel else text
                    self._text(p, vr, shown, H * 0.0195, QColor(ACCENT).lighter(150) if sel else QColor(255, 255, 255, 170),
                               True, Qt.AlignmentFlag.AlignRight)
                else:
                    self._text(p, QRectF(inner.right() - H * 0.03, row.top(), H * 0.03, row.height()), "\u203a", H * 0.03,
                               QColor(255, 255, 255, 150), False, Qt.AlignmentFlag.AlignRight)
        msg = self.panel_msg
        if msg:
            p.setFont(_px_font(H * 0.0155))
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(x0 + pad, H * 0.86, pw - 2 * pad, H * 0.1),
                       Qt.TextFlag.TextWordWrap.value | Qt.AlignmentFlag.AlignTop.value, msg)

    def _chip(self, p, inner, text, H):
        """Small pill at the right end of a panel row (SOON / PREVIEW)."""
        p.setFont(_px_font(H * 0.0125, True, 1))
        w = QFontMetrics(p.font()).horizontalAdvance(text) + H * 0.026
        r = QRectF(inner.right() - w, inner.center().y() - H * 0.014, w, H * 0.028)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor(255, 255, 255, 185))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_panel_soon(self, p, x0, pw, pad, H):
        r = ROADMAP_BY_ID.get(self.panel_soon)
        title, desc = (r[2], r[3]) if r else ("Coming soon", "")
        status = self.win.roadmap.status(self.panel_soon) if r else "Idea"
        y = H * 0.17
        self._text(p, QRectF(x0 + pad, y, pw - 2 * pad, H * 0.06), title, H * 0.034, "white", True)
        self._chip(p, QRectF(x0 + pad, y + H * 0.095, 0.0 + H * 0.16, H * 0.03), "COMING SOON", H)
        col = QColor(STATUS_COLORS[status])
        p.setFont(_px_font(H * 0.0145, True))
        sw = QFontMetrics(p.font()).horizontalAdvance(status) + H * 0.03
        chip = QRectF(x0 + pad + H * 0.19, y + H * 0.081, sw, H * 0.028)
        bgc = QColor(col)
        bgc.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bgc)
        p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
        p.setPen(col.lighter(130))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, status)
        p.setFont(_px_font(H * 0.019))
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(QRectF(x0 + pad, y + H * 0.13, pw - 2 * pad, H * 0.14),
                   Qt.TextFlag.TextWordWrap.value | Qt.AlignmentFlag.AlignTop.value, desc)
        draw_skeleton(p, QRectF(x0 + pad, H * 0.45, pw - 2 * pad, H * 0.3), "list")
        p.setFont(_px_font(H * 0.0165))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(x0 + pad, H * 0.86, pw - 2 * pad, H * 0.08),
                   Qt.TextFlag.TextWordWrap.value | Qt.AlignmentFlag.AlignTop.value,
                   "Not built yet. Press A to open this on your Roadmap, or B to go back.")

    # ------------------------------------------------------------------ input
    def keyPressEvent(self, e):
        K, k = Qt.Key, e.key()
        if k == K.Key_Left:
            self.move(-1, 0)
        elif k == K.Key_Right:
            self.move(1, 0)
        elif k == K.Key_Up:
            self.move(0, -1)
        elif k == K.Key_Down:
            self.move(0, 1)
        elif k in (K.Key_Return, K.Key_Enter, K.Key_Space):
            self.activate()
        elif k == K.Key_Slash:
            self.win.text_input("Search games and apps", self.search_q, self._search_done, False, 40)
        elif k == K.Key_Tab:
            self.toggle_panel()
        elif k == K.Key_Backspace:
            self.back()
        elif k == K.Key_Escape:
            if self.panel_open or self.mode != "home" or self.zone != "row":
                self.back()
            else:
                self.menu_requested.emit()
        elif k == K.Key_I:
            self.secondary()
        elif k == K.Key_F:
            self.tertiary()
        elif k == K.Key_Menu:
            self.options()
        elif k == K.Key_PageUp:
            self.shoulder(-1)
        elif k == K.Key_PageDown:
            self.shoulder(1)
        else:
            super().keyPressEvent(e)

    def _click(self, kind, idx):
        if kind == "panel_close":
            self.close_panel()
        elif kind == "panel":
            self.panel_i = idx
            self._panel_activate()
        elif self.panel_open:
            return
        elif kind == "nav":
            self.zone, self.nav_i = "nav", idx
            self._nav_activate(idx)
        elif kind == "row":
            if self.zone == "row" and idx == self.row_i:
                self.activate()
            else:
                self.zone = "row"
                self._row_jump(idx - self.row_i)
        elif kind == "wide":
            self.zone, self.wide_i = "wide", idx
            self._wide_scroll_to()
            self._wide_activate(idx)
        elif kind == "grid":
            if idx == self.grid_sel:
                self.activate()
            else:
                self.grid_sel = idx
                self._update_bg()
                self._scroll_to_sel()
        self.update()

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        for rect, (kind, idx) in reversed(self._hits):
            if rect.contains(e.position()):
                self._click(kind, idx)
                return

    def contextMenuEvent(self, e):
        for rect, (kind, idx) in reversed(self._hits):
            if kind in ("row", "grid") and rect.contains(QPointF(e.pos())):
                self._click(kind, idx)
                self.options(e.globalPos())
                return

    def wheelEvent(self, e):
        self._wheel += e.angleDelta().y()
        while abs(self._wheel) >= 120:
            step = -1 if self._wheel > 0 else 1
            self.move(0, step) if (self.mode != "home" or self.panel_open) else self.move(step, 0)
            self._wheel -= 120 * (1 if self._wheel > 0 else -1)


class CoverWidget(QWidget):
    def __init__(self, covers):
        super().__init__()
        self.covers, self.game = covers, None

    def paintEvent(self, _):
        if not self.game:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.setClipPath(path)
        self.covers.draw(p, r, self.game)
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1.5))
        p.drawRoundedRect(r, 16, 16)
        p.end()


class GamePage(QWidget):
    """Per-game page: big cover, Play, favourite, folder, stats, status and your notes."""

    def __init__(self, win):
        super().__init__()
        self.win, self.game, self.big, self._tiny = win, None, False, None
        self.setStyleSheet("QLabel { background: transparent; }")
        self.cover = CoverWidget(win.covers)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(900)
        self._save_timer.timeout.connect(win.save)

        self.root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.back_btn = QPushButton("\u2190 Library")
        self.back_btn.clicked.connect(lambda: self.reject())
        top.addWidget(self.back_btn)
        top.addStretch(1)
        self.root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(36)
        body.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(14)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.meta = QLabel()
        right.addWidget(self.title)
        right.addWidget(self.meta)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.play_btn = QPushButton("\u25B6  Play")
        self.play_btn.setObjectName("playbtn")
        self.fav_btn = QPushButton("\u2605  Favourite")
        self.fav_btn.setCheckable(True)
        self.folder_btn = QPushButton("Folder\u2026")
        self.more_btn = QPushButton("More\u2026")
        for b in (self.play_btn, self.fav_btn, self.folder_btn, self.more_btn):
            row.addWidget(b)
        row.addStretch(1)
        right.addLayout(row)
        # buttons that open dialogs/menus run after the click handler returns (so the controller keeps working)
        self.play_btn.clicked.connect(lambda: QTimer.singleShot(0, self._play))
        self.fav_btn.clicked.connect(lambda: self.toggle_fav())
        self.folder_btn.clicked.connect(lambda: QTimer.singleShot(0, self._folder_menu))
        self.more_btn.clicked.connect(lambda: QTimer.singleShot(0, self._more_menu))

        grid = QGridLayout()
        grid.setHorizontalSpacing(40)
        grid.setVerticalSpacing(4)
        self.stat_vals = {}
        for i, name in enumerate(["Playtime", "Last played", "Launched", "Store", "Controller", "Folder", "Location"]):
            r, c = (i // 2) * 2, i % 2
            if name == "Location":
                r, c = 6, 0
            grid.addWidget(QLabel(name.upper(), objectName="statkey"), r, c)
            val = QLabel("", objectName="statval")
            grid.addWidget(val, r + 1, c, 1, 2 if name == "Location" else 1)
            self.stat_vals[name] = val
        right.addSpacing(6)
        right.addLayout(grid)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("STATUS", objectName="statkey"))
        self.status_box = QComboBox()
        for text, val in STATUS_CHOICES:
            self.status_box.addItem(text, val)
        self.status_box.currentIndexChanged.connect(self._status_changed)
        srow.addWidget(self.status_box)
        srow.addStretch(1)
        right.addLayout(srow)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Your notes about this game\u2026")
        self.notes.setTabChangesFocus(True)
        self.notes.setMaximumHeight(120)
        self.notes.textChanged.connect(self._notes_changed)
        right.addWidget(self.notes)
        right.addSpacing(2)
        right.addWidget(QLabel("COMING SOON", objectName="statkey"))
        chips = QGridLayout()
        chips.setHorizontalSpacing(8)
        chips.setVerticalSpacing(8)
        game_items = [r for r in ROADMAP if r[1] == "Game page"]
        for k, r in enumerate(game_items):
            lab = QLabel(r[2])
            lab.setObjectName("soonchip")
            lab.setToolTip(r[3])
            chips.addWidget(lab, k // 4, k % 4)
        rm = QPushButton("Open Roadmap")
        rm.clicked.connect(lambda: QTimer.singleShot(0, lambda: self.win.open_roadmap()))
        chips.addWidget(rm, len(game_items) // 4, len(game_items) % 4)
        right.addLayout(chips)
        right.addStretch(1)
        body.addLayout(right, 1)
        self.root.addLayout(body, 1)
        win.thumbs.changed.connect(self._thumbs_changed)

    # ---- content
    def show_game(self, g, big):
        self.game, self.big, self._tiny = g, big, None
        b = big
        self.root.setContentsMargins(*((70, 44, 70, 44) if b else (26, 18, 26, 18)))
        self.cover.setFixedSize(*((340, 510) if b else (250, 375)))
        self.title.setStyleSheet(f"font-size: {46 if b else 30}px; font-weight: 800; color: white;")
        self.meta.setStyleSheet(f"font-size: {18 if b else 13}px; color: #c9bfe8;")
        for btn in (self.play_btn, self.fav_btn, self.folder_btn, self.more_btn, self.back_btn):
            btn.setMinimumHeight(54 if b else 38)
        self.cover.game = g
        self.cover.update()
        self.refresh()
        self.notes.blockSignals(True)
        self.notes.setPlainText(g.notes)
        self.notes.blockSignals(False)
        self.status_box.blockSignals(True)
        self.status_box.setCurrentIndex(max(0, self.status_box.findData(g.status)))
        self.status_box.blockSignals(False)
        self.update()

    def refresh(self):
        g = self.game
        if not g:
            return
        self.title.setText(g.name)
        self.meta.setText("   \u2022   ".join(x for x in (g.store, g.status) if x))
        self.fav_btn.setChecked(g.favourite)
        folder = next((f.name for f in self.win.model.folders if f.id == g.folder), "None")
        path = g.install_dir or g.launch or ""
        vals = {
            "Playtime": playtime_text(g) or "\u2014",
            "Last played": ago(g.last_played).capitalize() if g.last_played else "Never",
            "Launched": f"{g.launches} time" + ("" if g.launches == 1 else "s"),
            "Store": g.store,
            "Controller": self.win.CONTROLLER_LABELS.get(g.controller, "Unknown"),
            "Folder": folder,
            "Location": QFontMetrics(self.stat_vals["Location"].font()).elidedText(
                path, Qt.TextElideMode.ElideMiddle, 560),
        }
        for k, v in vals.items():
            self.stat_vals[k].setText(v)

    def rebind(self, games):
        """The library was rescanned: point at the fresh Game object (or leave if it vanished)."""
        for x in games:
            if self.game and x.id == self.game.id:
                self.game = self.cover.game = x
                self.refresh()
                return
        self.win.close_game_page(select=False)

    # ---- actions
    def toggle_fav(self):
        if self.game:
            self.win.toggle_fav(self.game)
            self.refresh()

    def _play(self):
        if self.game:
            self.win.launch_auto(self.game)

    def _folder_menu(self):
        g = self.game
        if not g:
            return
        menu = QMenu(self)
        none = menu.addAction("No folder")
        none.setCheckable(True)
        none.setChecked(not g.folder)
        none.triggered.connect(lambda _=False: self.win.move_to_folder(g, ""))
        for f in self.win.model.folders:
            act = menu.addAction(f.name)
            act.setCheckable(True)
            act.setChecked(g.folder == f.id)
            act.triggered.connect(lambda _=False, fid=f.id: self.win.move_to_folder(g, fid))
        menu.addSeparator()
        menu.addAction("New folder\u2026", lambda: self.win.new_folder(move=g))
        menu.exec(self.folder_btn.mapToGlobal(QPoint(0, self.folder_btn.height())))
        self.refresh()

    def _more_menu(self):
        if self.game:
            self.win.show_game_menu(self.game, self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height())))
            if self.win._page_open:
                self.refresh()

    def _status_changed(self, i):
        if self.game:
            self.game.status = self.status_box.itemData(i) or ""
            if self.game.status == "Finished":
                self.win.profile.add_event("finished", f"Finished {self.game.name}", self.game.id)
            self.win.model.touch(self.game)
            self.win.save()
            self.refresh()

    def _notes_changed(self):
        if self.game:
            self.game.notes = self.notes.toPlainText()
            self._save_timer.start()

    def reject(self):
        self.win.close_game_page()

    # ---- painting / keys
    def _thumbs_changed(self):
        self._tiny = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        p.fillRect(r, QColor("#0a0714"))
        if self.game and self._tiny is None:
            self._tiny = make_backdrop(self.win.thumbs, self.game)
        paint_backdrop(p, r, self._tiny, 0.8, self.game if self._tiny is None else None)
        shade = QLinearGradient(0, 0, r.width(), 0)
        shade.setColorAt(0, QColor(8, 5, 18, 200))
        shade.setColorAt(1, QColor(8, 5, 18, 235))
        p.fillRect(r, shade)
        p.end()

    def keyPressEvent(self, e):
        fw = QApplication.focusWidget()
        if e.key() == Qt.Key.Key_Escape or (e.key() == Qt.Key.Key_Backspace and isinstance(fw, QAbstractButton)):
            self.reject()
            return
        super().keyPressEvent(e)


# --------------------------------------------------------------------------- #
# YouTube: the bundled "YouTube, Xbox style" app (real youtube.com in Windows' Edge WebView2, so Google
# sign-in works like in Edge; its own controller support). Nexus Grid starts it and idles behind it.
# --------------------------------------------------------------------------- #
YT_APP_VERSION = "3"
YT_DIR = APPS_DIR / "YouTube"


def ensure_youtube_app():
    """Write the bundled YouTube app into Apps/YouTube (first run / after an update).
    The app's code is refreshed on updates; README, example config and scripts never overwrite your edits."""
    YT_DIR.mkdir(parents=True, exist_ok=True)
    marker = YT_DIR / "version.txt"
    main = YT_DIR / "main.py"
    if main.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == YT_APP_VERSION:
        return main
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(YT_APP_B64))) as z:
        for name in z.namelist():
            dest = YT_DIR / name
            if name == "main.py" or not dest.exists():
                dest.write_bytes(z.read(name))
    marker.write_text(YT_APP_VERSION, encoding="utf-8")
    return main


class ImportDialog(QDialog):
    """Lists the games found in a folder; pick which to add and which .exe launches each."""

    def __init__(self, parent, candidates):
        super().__init__(parent)
        self.setWindowTitle("Add games from folder")
        self.resize(900, 560)
        self.rows = []   # (checkbox, name_edit, combo, folder)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Found {len(candidates)} game folder(s). Untick any you don't want, "
                             "and check the .exe is the right one."))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        lay.addWidget(self.list, 1)

        for folder, exes in candidates:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(6, 4, 6, 4)
            cb = QCheckBox()
            cb.setChecked(True)
            name = QLineEdit(clean_name(folder.name))
            combo = QComboBox()
            for e in exes:
                try:
                    label = str(e.relative_to(folder))
                except ValueError:
                    label = str(e)
                combo.addItem(label, str(e))
            h.addWidget(cb)
            h.addWidget(name, 2)
            h.addWidget(combo, 3)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 48))
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
            self.rows.append((cb, name, combo, folder))

        btns = QHBoxLayout()
        all_btn, none_btn = QPushButton("Select all"), QPushButton("Select none")
        all_btn.clicked.connect(lambda: [r[0].setChecked(True) for r in self.rows])
        none_btn.clicked.connect(lambda: [r[0].setChecked(False) for r in self.rows])
        cancel, ok = QPushButton("Cancel"), QPushButton("Add selected")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        btns.addWidget(all_btn)
        btns.addWidget(none_btn)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def chosen(self):
        out = []
        for cb, name, combo, folder in self.rows:
            if cb.isChecked() and combo.currentData():
                out.append((name.text().strip() or folder.name, combo.currentData(), str(folder)))
        return out


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nexus Grid")
        self.resize(1200, 780)
        self.settings = make_settings()
        if not self.cfg_bool("games_dir_added", False):
            folders = self.library_folders()
            if str(GAMES_DIR) not in folders:
                folders.append(str(GAMES_DIR))
                self.settings.setValue("library_folders", folders)
            self.settings.setValue("games_dir_added", True)
        try:
            ensure_youtube_app()                    # make sure Apps/YouTube exists from the first run
        except Exception:
            pass
        self.profile = Profile()
        self.roadmap = RoadmapStore()
        self._roadmap_open = False
        self._avatar_pm = None
        self._profile_open = False
        self.thumbs = ThumbCache()
        self.covers = CoverPainter(self.thumbs)
        self._page_open = False
        self._yt_proc, self._yt_log, self._yt_started = None, None, 0.0
        self._yt_dir, self._yt_name = YT_DIR, "YouTube"
        self._sessions, self._watch_start = {}, 0.0
        self.youtube_item = Game(id="app:youtube", name="YouTube", store="App",
                                 launch=str(YT_DIR / "main.py"), install_dir=str(YT_DIR))
        self._pad_last = 0.0
        self.pool = QThreadPool.globalInstance()
        self.bridge = Bridge()
        self.bridge.scan_done.connect(self.on_scan_done)
        self.bridge.cover_done.connect(self.on_cover_done)
        self.bridge.art_done.connect(self.on_art_done)
        self.bridge.art_finished.connect(self.on_art_finished)
        self._art_pending, self._art_found, self._art_total = set(), 0, 0
        self._ctl_pending = set()
        self.bridge.controller_done.connect(self.on_controller_done)
        self.big = False                       # Big Picture mode
        self._idle = False                     # Big Picture + a game is running: do as little as possible
        self._quitting = False
        self._overlay = None
        self._desktop_state = "maximized"
        self._startup_import_done = False
        self.view_mode = str(self.settings.value("view_mode", "grid"))

        self.model = GameModel()
        folders, games = load_folders(), load_library()
        known = {f.id for f in folders}
        for g in games:
            if g.folder not in known:
                g.folder = ""
        self.model.folders = folders
        self.model.set_games(games)
        self.fill_exe_covers()
        self.proxy = GameProxy()
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(0)

        # ---- top bar
        self.search = SearchBox(placeholderText="Search games\u2026  (Ctrl+F)")
        self.search.go_grid.connect(self.focus_grid)
        self.search.returnPressed.connect(self.launch_current)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda t: self.proxy.configure(text=t))

        self.store_box = QComboBox()
        self.store_box.addItems(["All", "Favourites", "Steam", "Epic", "GOG", "Ubisoft", "Manual"])
        self.store_box.currentTextChanged.connect(lambda s_: (self.proxy.configure(store=s_), self.update_bp_info()))

        self.sort_box = QComboBox()
        self.sort_box.addItems(["A\u2013Z", "Recently played"])
        self.sort_box.currentIndexChanged.connect(lambda i: (self.proxy.configure(recent=(i == 1)), self.update_bp_info()))
        self.sort_box.setCurrentIndex(1 if str(self.settings.value("sort", "az")) == "recent" else 0)

        self.grid_btn, self.list_btn = QPushButton("Grid"), QPushButton("List")
        group = QButtonGroup(self)
        group.setExclusive(True)
        for btn, mode in ((self.grid_btn, "grid"), (self.list_btn, "list")):
            btn.setCheckable(True)
            group.addButton(btn)
            btn.clicked.connect(lambda _=False, m=mode: self.set_view_mode(m))

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(130, 320)
        self.size_slider.setFixedWidth(110)
        self.size_slider.setValue(int(self.settings.value("tile_w", 200)))
        self.size_slider.valueChanged.connect(self.on_resize_tiles)

        add_btn = QPushButton("+ Add game")
        add_menu = QMenu(add_btn)
        add_menu.addAction("Add a game (.exe)\u2026", lambda: self.add_manual())
        add_menu.addAction("Import games from a folder\u2026", lambda: self.add_folder())
        add_btn.clicked.connect(lambda: add_menu.exec(add_btn.mapToGlobal(QPoint(0, add_btn.height()))))
        new_folder_btn = QPushButton("+ New folder")
        new_folder_btn.setToolTip("Make a folder to group games, e.g. one per creator")
        new_folder_btn.clicked.connect(lambda: self.new_folder())
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self.on_scan_clicked)
        bp_btn = QPushButton("Big Picture")
        bp_btn.setToolTip("Full-screen, controller-friendly mode  (F10)")
        bp_btn.clicked.connect(lambda: self.toggle_big_picture())
        profile_btn = QPushButton("Profile")
        profile_btn.clicked.connect(lambda: self.open_profile())
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(lambda: self.open_settings())

        self.topbar = QWidget()
        bar = QHBoxLayout(self.topbar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setPixmap(logo_pixmap(30))
        bar.addWidget(brand_icon)
        bar.addWidget(QLabel("NEXUS GRID", objectName="brand"))
        bar.addSpacing(8)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.store_box)
        bar.addWidget(self.sort_box)
        bar.addWidget(self.grid_btn)
        bar.addWidget(self.list_btn)
        bar.addWidget(QLabel("Size"))
        bar.addWidget(self.size_slider)
        bar.addWidget(add_btn)
        bar.addWidget(new_folder_btn)
        bar.addWidget(scan_btn)
        bar.addWidget(bp_btn)
        bar.addWidget(profile_btn)
        bar.addWidget(settings_btn)

        # ---- Big Picture header (hidden until Big Picture mode)
        self.bp_header = QWidget()
        hb = QHBoxLayout(self.bp_header)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(16)
        bp_logo = QLabel()
        bp_logo.setPixmap(logo_pixmap(46))
        hb.addWidget(bp_logo)
        hb.addWidget(QLabel("NEXUS GRID", objectName="bpbrand"))
        hb.addStretch(1)
        self.bp_info = QLabel("", objectName="bpinfo")
        self.bp_clock = QLabel("", objectName="bpclock")
        hb.addWidget(self.bp_info)
        hb.addSpacing(18)
        hb.addWidget(self.bp_clock)
        self.bp_header.hide()

        # ---- breadcrumb shown while inside a folder
        self.crumb_row = QWidget()
        cr = QHBoxLayout(self.crumb_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.setSpacing(14)
        back_btn = QPushButton("\u2190 Back")
        back_btn.clicked.connect(lambda: self.folder_up())
        cr.addWidget(back_btn)
        self.crumb = QLabel("", objectName="crumb")
        cr.addWidget(self.crumb)
        cr.addStretch(1)
        self.crumb_row.hide()

        # ---- grid / list
        self.delegate = CardDelegate(self.model, self.covers)
        self.delegate.set_tile_width(self.size_slider.value())
        self.view = GridView()
        self.view.resized.connect(self.relayout)
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(self.delegate)
        self.view.setUniformItemSizes(True)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(30)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setDragEnabled(True)
        self.view.setAcceptDrops(True)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.view.setDropIndicatorShown(False)
        self.view.can_drop = lambda idx: isinstance(self.game_at(idx), Folder)
        self.view.dropped.connect(self.on_dropped)
        self.view.context_requested.connect(self.on_context)
        self.view.typed.connect(self.on_typed)
        self.view.escaped.connect(self.on_back)
        self.view.doubleClicked.connect(self.activate_index)
        self.view.activated.connect(self.activate_index)

        self.thumbs.changed.connect(self.view.viewport().update)

        # console-style Big Picture home + the per-game page
        self.bp_home = BigPictureHome(self)
        self.bp_home.play_requested.connect(lambda g: self.launch_auto(g))
        self.bp_home.details_requested.connect(lambda g: self.open_game_page(g))
        self.bp_home.favourite_requested.connect(lambda g: self.toggle_fav(g))
        self.bp_home.options_requested.connect(lambda g, pos: self.show_game_menu(g, pos))
        self.bp_home.menu_requested.connect(lambda: self.show_main_menu())
        self.bp_home.hide()
        self.page = GamePage(self)
        self.page.hide()
        self._yt_timer = QTimer(self)
        self._yt_timer.setInterval(1000)
        self._yt_timer.timeout.connect(self._yt_poll)
        self.profile_page = ProfilePage(self)
        self.profile_page.hide()
        self.roadmap_page = RoadmapPage(self)
        self.roadmap_page.hide()
        self.toast = ToastWidget(self)
        self.osk = OskOverlay(self)
        self.picker = ListPicker(self)
        self._ach_timer = QTimer(self)
        self._ach_timer.setSingleShot(True)
        self._ach_timer.setInterval(900)
        self._ach_timer.timeout.connect(self.check_achievements)
        self._home_timer = QTimer(self)
        self._home_timer.setSingleShot(True)
        self._home_timer.setInterval(150)
        self._home_timer.timeout.connect(lambda: self.bp_home.rebuild() if self.big else None)
        for sig in (self.model.modelReset, self.model.dataChanged, self.model.rowsInserted, self.model.rowsRemoved):
            sig.connect(lambda *_: self._home_timer.start() if self.big else None)
        for sig in (self.model.modelReset, self.model.dataChanged, self.model.rowsInserted, self.model.rowsRemoved):
            sig.connect(lambda *_: self._ach_timer.start())
        QTimer.singleShot(3000, self.check_achievements)

        self.status = QLabel("", objectName="status")
        self.hint = QLabel("", objectName="status")

        central = QWidget()
        self.central_layout = lay = QVBoxLayout(central)
        lay.setContentsMargins(18, 16, 10, 10)
        lay.setSpacing(12)
        lay.addWidget(self.topbar)
        lay.addWidget(self.bp_header)
        lay.addWidget(self.crumb_row)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.bp_home, 1)
        lay.addWidget(self.page, 1)
        lay.addWidget(self.profile_page, 1)
        lay.addWidget(self.roadmap_page, 1)
        foot = QHBoxLayout()
        foot.addWidget(self.status, 1)
        foot.addWidget(self.hint)
        lay.addLayout(foot)
        self.setCentralWidget(central)

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.focus_search)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("F10"), self, activated=self.toggle_big_picture)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.fav_current)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.on_back)
        self._restore_state = "maximized"
        for sig in (self.proxy.rowsInserted, self.proxy.rowsRemoved, self.proxy.modelReset,
                    self.proxy.layoutChanged):
            sig.connect(lambda *_: QTimer.singleShot(0, self.ensure_selection))

        # ---- controller
        self.pad = XInput()
        self._pad_seen, self._pad_prev, self._pad_dir, self._pad_next = False, 0, (0, 0), 0.0
        self._pad_trig = (False, False)
        self.pad_timer = QTimer(self)
        self.pad_timer.timeout.connect(self.poll_pad)
        self.update_hint()
        self.proxy.rowsInserted.connect(self.update_status)
        self.proxy.rowsRemoved.connect(self.update_status)
        self.proxy.modelReset.connect(self.update_status)
        self.proxy.layoutChanged.connect(self.update_status)

        # clock for the Big Picture header
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(20000)
        self.update_clock()

        self.apply_settings()
        self._apply_view_mode()
        self.update_status()
        if self.cfg_bool("scan_on_start", True):
            self.start_scan()

        self.watching = None
        self.bridge.watch_done.connect(self.on_watch_done)
        self.setup_tray()
        self._tracking, self._ticks, self._last_tick = False, 0, time.time()
        self.bridge.running.connect(self.on_running)
        self.tracker = QTimer(self)
        self.tracker.timeout.connect(self.track)
        self.tracker.start(10000)

    # ---- settings helpers
    def cfg_bool(self, key, default=False):
        return as_bool(self.settings.value(key, default), default)

    def cfg_int(self, key, default):
        try:
            return int(self.settings.value(key, default))
        except (TypeError, ValueError):
            return default

    def cfg_str(self, key, default=""):
        return str(self.settings.value(key, default) or default)

    def apply_settings(self, key=None):
        global ACCENT
        self.delegate.show_playtime = self.cfg_bool("show_playtime", True)
        if self.pad.fn:
            if self.cfg_bool("pad_enabled", True):
                if not self.pad_timer.isActive():
                    self.pad_timer.start(33)
            else:
                self.pad_timer.stop()
                self._pad_seen = False
                self.update_hint()
        if key in ("startup", "startup_minimized"):
            if not set_startup(self.cfg_bool("startup", False), self.cfg_bool("startup_minimized", False)):
                self.status.setText("Couldn't change the Windows startup setting")
        elif key == "accent":
            ACCENT = self.cfg_str("accent", ACCENT_PRESETS["Violet"])
            self.profile.bump("accent_changes")
            self.queue_achievements()
            QApplication.instance().setStyleSheet(build_style(ACCENT))
        elif key == "view_mode":
            self.view_mode = self.cfg_str("view_mode", "grid")
            self._apply_view_mode()
        elif key == "bp_tile":
            self.relayout(force=True)
        pass
        self.view.viewport().update()

    def open_settings(self):
        SettingsDialog(self).exec()
        self.update_status()

    def set_sgdb_key_value(self, text):
        text = text.strip()
        if text == str(self.settings.value("sgdb_key", "") or ""):
            return
        self.settings.setValue("sgdb_key", text)
        if text:
            for g in self.model.games:
                g.art_tried = 0.0          # retry everything that had no art, now with the key
            self.fetch_art()

    def update_clock(self):
        self.bp_clock.setText(time.strftime("%H:%M   %a %d %b"))
        if self.big and self.bp_home.isVisible():
            self.bp_home.update()

    def update_bp_info(self):
        if not hasattr(self, "bp_info"):
            return
        self.bp_info.setText(f"{self.store_box.currentText()}   \u2022   {self.sort_box.currentText()}")

    # ---- view mode (grid / list)
    def set_view_mode(self, mode):
        self.settings.setValue("view_mode", mode)
        self.view_mode = mode
        self._apply_view_mode()

    def _apply_view_mode(self):
        list_mode = self.view_mode == "list" and not self.big
        self.delegate.list_mode = list_mode
        self.view.setViewMode(QListView.ViewMode.ListMode if list_mode else QListView.ViewMode.IconMode)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(0)
        if list_mode:
            self.view.setFlow(QListView.Flow.TopToBottom)
            self.view.setWrapping(False)
        else:
            self.view.setFlow(QListView.Flow.LeftToRight)
            self.view.setWrapping(True)
            self.view.setGridSize(QSize())
        self.size_slider.setEnabled(not list_mode)
        for btn, on in ((self.grid_btn, not list_mode), (self.list_btn, list_mode)):
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.blockSignals(False)
        self.relayout(force=True)

    # ---- helpers
    def game_at(self, proxy_index):
        """The Game or Folder shown at this proxy index."""
        return self.model.item_at(self.proxy.mapToSource(proxy_index).row())

    def update_status(self, *_):
        n = self.proxy.rowCount()
        nf = sum(1 for r in range(n) if isinstance(self.game_at(self.proxy.index(r, 0)), Folder))
        text = f"{n - nf} shown  \u2022  {len(self.model.games)} total"
        if self.model.folders:
            text += f"  \u2022  {len(self.model.folders)} folder" + ("" if len(self.model.folders) == 1 else "s")
        self.status.setText(text)

    def on_resize_tiles(self, w):
        self.settings.setValue("tile_w", w)
        self.relayout()

    def relayout(self, force=False):
        """Grid: as many columns as the size allows, tiles stretched to fill the row.  List: full-width rows."""
        avail = self.view.width() - 14            # leave room for the scrollbar so it never reflows
        if avail <= 0:
            return
        d = self.delegate
        if d.list_mode:
            changed = d.row_w != avail
            d.row_w = avail
            if changed or force:
                self.view.setGridSize(QSize(avail, d.LIST_H))
        else:
            base = self.cfg_int("bp_tile", 250) if self.big else self.size_slider.value()
            cols = max(1, avail // (base + 16))
            changed = d.set_tile_width(max(100, avail // cols - 16))
        if changed or force:
            self.view.doItemsLayout()
            self.view.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._overlay is not None:
            self._overlay.setGeometry(self.rect())
        osk = getattr(self, "osk", None)
        if osk is not None and osk.active:
            osk.setGeometry(self.rect())
        picker = getattr(self, "picker", None)
        if picker is not None and picker.active:
            picker.setGeometry(self.rect())

    def save(self):
        save_library(self.model.games)
        save_folders(self.model.folders)

    # ---- folders (user collections, e.g. one per creator)
    def activate_index(self, idx):
        item = self.game_at(idx)
        if isinstance(item, Folder):
            self.open_folder(item.id)
        elif self.cfg_str("open_action", "page") == "launch":
            self.launch(item)
        else:
            self.open_game_page(item)

    def update_crumb(self):
        f = next((x for x in self.model.folders if x.id == self.proxy.folder), None)
        self.crumb.setText(f.name if f else "")
        self._refresh_layout_state()

    def open_folder(self, fid):
        self.proxy.configure(folder=fid)
        self.update_crumb()
        self.view.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, self.focus_grid)

    def folder_up(self):
        fid = self.proxy.folder
        if not fid:
            return
        self.open_folder("")
        QTimer.singleShot(0, lambda: self.select_folder(fid))

    def select_folder(self, fid):
        for r in range(self.proxy.rowCount()):
            idx = self.proxy.index(r, 0)
            it = self.game_at(idx)
            if isinstance(it, Folder) and it.id == fid:
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)
                return

    def on_back(self):
        if self.proxy.folder:
            self.folder_up()
        elif self.search.text():
            self.search.clear()

    def new_folder(self, move=None):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name (e.g. a creator or a series):")
        name = name.strip()
        if not ok or not name:
            return None
        f = Folder(id=uuid.uuid4().hex[:10], name=name)
        self.model.set_folders(self.model.folders + [f])
        if move is not None:
            move.folder = f.id
            self.model.touch(move)
        self.proxy.invalidate()
        self.save()
        self.status.setText(f"Created folder \u201c{name}\u201d")
        return f

    def rename_folder(self, f):
        name, ok = QInputDialog.getText(self, "Rename folder", "Folder name:", QLineEdit.EchoMode.Normal, f.name)
        name = name.strip()
        if not ok or not name:
            return
        f.name = name
        self.model.touch_folders()
        self.proxy.invalidate()
        self.update_crumb()
        self.save()

    def delete_folder(self, f):
        n = len(self.model.folder_games(f.id))
        text = f"Delete the folder \u201c{f.name}\u201d?\n\nIts {n} game(s) stay in your library."
        if QMessageBox.question(self, "Delete folder", text) != QMessageBox.StandardButton.Yes:
            return
        for g in self.model.games:
            if g.folder == f.id:
                g.folder = ""
        if self.proxy.folder == f.id:
            self.open_folder("")
        self.model.set_folders([x for x in self.model.folders if x is not f])
        self.proxy.invalidate()
        self.save()

    def move_to_folder(self, g, fid):
        g.folder = fid
        self.model.touch(g)
        self.proxy.invalidate()
        self.save()

    def on_dropped(self, idx, mime):
        f = self.game_at(idx)
        if not isinstance(f, Folder):
            return
        ids = set(bytes(mime.data(GAME_MIME)).decode("utf-8").split("\n"))
        moved = 0
        for g in self.model.games:
            if g.id in ids and g.folder != f.id:
                g.folder = f.id
                moved += 1
        if moved:
            self.model.touch_folders()
            self.proxy.invalidate()
            self.save()
            self.status.setText(f"Moved {moved} game(s) into \u201c{f.name}\u201d")

    # ---- Big Picture
    def toggle_big_picture(self):
        if self.big:
            self.exit_big_picture()
        else:
            self.enter_big_picture()

    def _transition(self, title, subtitle, fn, animate):
        if not animate or not self.isVisible() or not self.cfg_bool("animations", True) or self._overlay is not None:
            fn()
            return
        self._overlay = TransitionOverlay(self, title, subtitle, fn, self._overlay_done)

    def _overlay_done(self):
        self._overlay = None
        self.focus_grid()

    def _refresh_layout_state(self):
        """Exactly one of: library (grid/list), Big Picture home, or a game page is on screen."""
        big, paged, prof, rm = self.big, self._page_open, self._profile_open, self._roadmap_open
        lib = not paged and not prof and not rm
        self.profile_page.setVisible(prof)
        self.roadmap_page.setVisible(rm)
        self.topbar.setVisible(lib and not big)
        self.bp_header.setVisible(False)
        self.crumb_row.setVisible(lib and not big and bool(self.proxy.folder))
        self.view.setVisible(lib and not big)
        self.bp_home.setVisible(lib and big)
        self.page.setVisible(paged)
        self.status.setVisible(not big)
        self.hint.setVisible(not big)
        self.central_layout.setContentsMargins(*((0, 0, 0, 0) if big else (18, 16, 10, 10)))

    def _apply_mode_ui(self):
        big = self.big
        self.delegate.big = big
        size = "font-size: 15px;" if big else ""
        self.hint.setStyleSheet(size)
        self.status.setStyleSheet(size)
        self._apply_view_mode()
        if self._page_open and self.page.game:
            self.page.show_game(self.page.game, big)
        self._refresh_layout_state()
        if big:
            self.bp_home.reset_view()
            self.bp_home.rebuild()
        self.update_bp_info()
        self.update_hint()

    # ---- game page
    def pad_recent(self):
        return time.time() - self._pad_last < 1.5

    # ---- YouTube (separate bundled app)
    def yt_running(self):
        return self._yt_proc is not None and self._yt_proc.poll() is None

    def app_items(self):
        """YouTube first, then every other folder in Apps/ that has a main.py."""
        items = [self.youtube_item]
        try:
            for d in sorted(APPS_DIR.iterdir(), key=lambda p: p.name.lower()):
                if d.is_dir() and d.name.lower() != "youtube" and (d / "main.py").exists():
                    items.append(Game(id=f"app:{d.name}", name=d.name, store="App",
                                      launch=str(d / "main.py"), install_dir=str(d)))
        except OSError:
            pass
        return items

    def open_youtube(self):
        self.open_app(self.youtube_item)

    def open_app(self, g):
        if self.yt_running():
            return
        d = Path(g.install_dir)
        try:
            if g.id == "app:youtube":
                ensure_youtube_app()
            main = d / "main.py"
            if not main.exists():
                raise FileNotFoundError(str(main))
        except Exception as e:
            QMessageBox.warning(self, g.name, f"Couldn't set up {g.name}:\n{e}")
            return
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        py = pyw if (os.name == "nt" and pyw.exists()) else exe
        args = [str(py), str(main)]
        if g.id == "app:youtube" and self.cfg_bool("yt_windowed", False):
            args.append("--windowed")
        try:
            self._yt_log = open(d / "app.log", "w", encoding="utf-8")
            self._yt_proc = subprocess.Popen(args, cwd=str(d), stdin=subprocess.DEVNULL, stdout=self._yt_log,
                                             stderr=subprocess.STDOUT, creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception as e:
            QMessageBox.warning(self, g.name, f"Couldn't start {g.name}:\n{e}")
            self._yt_proc = None
            return
        self._yt_dir, self._yt_name = d, g.name
        if g.id == "app:youtube":
            self.profile.bump("yt_opens")
            self.queue_achievements()
        self._yt_started = time.time()
        self.status.setText(f"Opening {g.name}\u2026 (the first time it installs a few packages and can take a minute)")
        self._set_idle(True)               # stay quiet behind it, like while a game runs
        self._yt_timer.start()

    def _yt_poll(self):
        p = self._yt_proc
        if p is None:
            self._yt_timer.stop()
            return
        rc = p.poll()
        if rc is None:
            return
        self._yt_timer.stop()
        self._yt_proc = None
        try:
            self._yt_log.close()
        except Exception:
            pass
        self._set_idle(False)
        self.restore()
        self.focus_grid()
        self.update_status()
        if rc != 0 and time.time() - self._yt_started < 90:
            tail = ""
            try:
                tail = "".join((self._yt_dir / "app.log").read_text(encoding="utf-8", errors="replace").splitlines(True)[-14:])
            except Exception:
                pass
            QMessageBox.warning(self, self._yt_name, f"{self._yt_name} closed unexpectedly (exit code {rc}).\n\n"
                                f"Last lines of its log:\n{tail}\n\nFull log: {self._yt_dir / 'app.log'}")

    def _stop_youtube(self):
        if self.yt_running():
            try:
                self._yt_proc.terminate()
            except Exception:
                pass

    # ---- profile, achievements, text entry
    def avatar_pixmap(self):
        if not self.profile.has_avatar:
            return None
        if self._avatar_pm is None:
            pm = QPixmap(str(AVATAR_FILE))
            self._avatar_pm = pm if not pm.isNull() else None
        return self._avatar_pm

    def reload_avatar(self):
        self._avatar_pm = None
        self.bp_home.update()
        self.profile_page.update()

    def pick_from(self, title, items, callback, allow_clear=False, current=None):
        """Choose one entry from a list with the controller/mouse/keyboard (items: (label, sub, value))."""
        self.picker.ask(title, items, callback, allow_clear, current)

    def text_input(self, title, initial, callback, multiline=False, maxlen=200):
        """On-screen keyboard text entry (works with controller, mouse and keyboard)."""
        self.osk.ask(title, initial, callback, multiline, maxlen)

    def open_profile(self):
        if self._profile_open:
            return
        self._profile_open = True
        self.profile_page.show_profile()
        self._refresh_layout_state()
        QTimer.singleShot(0, self.profile_page.setFocus)

    def close_profile(self):
        if not self._profile_open:
            return
        self._profile_open = False
        self._refresh_layout_state()
        self.bp_home.update()
        self.focus_grid()

    def soon_app_items(self):
        """Placeholder tiles for the apps on the Roadmap (they open their Roadmap entry)."""
        return [Game(id=f"soon:{r[0]}", name=r[2], store="Soon", launch="") for r in ROADMAP if r[1] == "Apps"]

    def open_roadmap(self, focus=None):
        if not isinstance(focus, str):                      # Qt signals can pass a bool
            focus = None
        self._profile_open = self._page_open = False
        self._roadmap_open = True
        self.roadmap_page.show_roadmap(focus)
        self._refresh_layout_state()
        QTimer.singleShot(0, self.roadmap_page.setFocus)

    def close_roadmap(self):
        if not self._roadmap_open:
            return
        self._roadmap_open = False
        self._refresh_layout_state()
        self.bp_home.update()
        self.focus_grid()

    def queue_achievements(self):
        self._ach_timer.start()

    def check_achievements(self):
        pr = self.profile
        was_fresh, pr.fresh = pr.fresh, False
        lvl0 = pr.level_info()[0]
        added = pr.note_games(self.model.games)                           # newly added games -> activity feed
        if len(added) > 3:
            pr.add_event("added", f"Added {len(added)} games to your library")
        else:
            for g in added:
                pr.add_event("added", f"Added {g.name} to your library", g.id)
        new = pr.evaluate(self.model.games, self.model.folders)
        goals = pr.evaluate_goals()
        lvl1 = pr.level_info()[0]
        for a in new:
            pr.add_event("achievement", f"Unlocked {a[1]}  (+{a[3]} G)")
        for title, reward in goals:
            pr.add_event("goal", f"Goal complete: {title}  (+{reward} G)")
        if lvl1 > lvl0 and not was_fresh:
            pr.add_event("level", f"Reached level {lvl1}")
        if new or goals or lvl1 > lvl0:
            if was_fresh:
                if new:
                    self.toast.push("Welcome to your profile", f"{len(new)} achievements already earned from your library",
                                    sum(a[3] for a in new))
            else:
                if len(new) > 3:
                    self.toast.push(f"{len(new)} achievements unlocked", ", ".join(a[1] for a in new[:3]) + "\u2026",
                                    sum(a[3] for a in new))
                else:
                    for a in new:
                        self.toast.push(a[1], a[2], a[3])
                for title, reward in goals[:3]:
                    self.toast.push("Weekly goal complete", title, reward)
                if lvl1 > lvl0:
                    self.toast.push(f"Level {lvl1}", "Keep playing to level up", 0)
            if self._profile_open:
                self.profile_page.refresh()
            self.bp_home.update()
        pr.flush()

    def launch_auto(self, g):
        """Launch; if the controller was just used, warn first about games without controller support."""
        if g.store == "Soon":
            self.open_roadmap(g.id.split(":", 1)[1])
        elif g.store == "App":
            self.open_app(g)
        elif self.pad_recent():
            self.launch_from_pad(g)
        else:
            self.launch(g)

    def open_game_page(self, g):
        self._page_open = True
        self.page.show_game(g, self.big)
        self._refresh_layout_state()
        QTimer.singleShot(0, self.page.play_btn.setFocus)

    def close_game_page(self, select=True):
        g = self.page.game
        self._page_open = False
        self._refresh_layout_state()
        if select and g:
            self.select_game(g)
        self.focus_grid()

    def select_game(self, g):
        if self.big:
            self.bp_home.select(g)
            return
        for r in range(self.proxy.rowCount()):
            idx = self.proxy.index(r, 0)
            if self.game_at(idx) is g:
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)
                return

    def enter_big_picture(self, animate=True):
        if self.big:
            return

        def apply():
            self._desktop_state = ("fullscreen" if self.isFullScreen()
                                   else "maximized" if self.isMaximized() else "normal")
            self.big = True
            self.profile.bump("bp_opens")
            self.queue_achievements()
            self._apply_mode_ui()
            self.showFullScreen()
            self._restore_state = "fullscreen"
            QTimer.singleShot(0, lambda: self.relayout(force=True))
            QTimer.singleShot(0, self.focus_grid)
        self._transition("BIG PICTURE", "entering big picture mode", apply, animate)

    def exit_big_picture(self, animate=True):
        if not self.big:
            return

        def apply():
            self.big = False
            self._apply_mode_ui()
            self._restore_state = self._desktop_state
            self.show_remembered()
            QTimer.singleShot(0, lambda: self.relayout(force=True))
            QTimer.singleShot(0, self.focus_grid)
        self._transition("DESKTOP MODE", "returning to the library", apply, animate)

    def show_initial(self):
        self.show_remembered()
        if self.cfg_bool("start_bp", False):
            QTimer.singleShot(150, lambda: self.enter_big_picture(animate=False))

    def focus_search(self):
        if not self.big:
            self.search.setFocus()

    def hide_window(self, notify=False):
        self._restore_state = ("fullscreen" if self.isFullScreen()
                               else "maximized" if self.isMaximized() else "normal")
        self.hide()
        if notify and not self.cfg_bool("tray_tip_shown", False):
            self.settings.setValue("tray_tip_shown", True)
            self.tray.showMessage("Nexus Grid", "Still running in the system tray. Click the icon to open it, "
                                  "or right-click it to quit.", QSystemTrayIcon.MessageIcon.Information, 4000)

    def show_main_menu(self):
        """Controller Back/Select button: the 'home' menu."""
        menu = QMenu(self)
        if self.big:
            menu.addAction("YouTube", lambda: self.open_youtube())
            menu.addAction("Exit Big Picture", lambda: self.exit_big_picture())
        else:
            menu.addAction("Big Picture mode", lambda: self.enter_big_picture())
            menu.addAction("Toggle fullscreen", lambda: self.toggle_fullscreen())
        menu.addAction("Profile", lambda: self.open_profile())
        menu.addAction("Roadmap", lambda: self.open_roadmap())
        menu.addAction("Settings\u2026", lambda: self.open_settings())
        menu.addAction("Scan for games", lambda: self.on_scan_clicked())
        menu.addAction("New folder\u2026", lambda: self.new_folder())
        menu.addSeparator()
        if QSystemTrayIcon.isSystemTrayAvailable():
            menu.addAction("Hide to tray", lambda: self.hide_window())
        menu.addAction("Quit Nexus Grid", lambda: self.quit_app())
        menu.exec(self.mapToGlobal(self.rect().center()) - QPoint(120, 130))

    # ---- scanning
    def start_scan(self):
        self.status.setText("Scanning…")
        self.pool.start(ScanTask(self.bridge))

    def on_scan_done(self, scanned):
        merged = merge(self.model.games, scanned)
        self.model.set_games(merged)
        self.fill_exe_covers()
        if self.cfg_bool("auto_art", True):
            self.fetch_art()
        self.fetch_controller()
        self.save()
        if self._page_open:
            self.page.rebind(self.model.games)
        if not self._startup_import_done:
            self._startup_import_done = True
            if self.cfg_bool("auto_import", False):
                QTimer.singleShot(800, lambda: self.import_from(
                    [f for f in self.library_folders() if os.path.isdir(f)]))
        missing = [(g.id, g.id.split(":")[1]) for g in merged if g.store == "Steam" and not g.cover]
        if missing:
            self.pool.start(CoverTask(self.bridge, missing))
        self.update_status()

    def on_cover_done(self, gid, path):
        for g in self.model.games:
            if g.id == gid:
                g.cover = path
                self.thumbs.forget(path)
                self.model.touch(g)
                break
        self.save()

    # ---- playtime tracking (non-Steam games)
    def track(self):
        self.profile.flush()                           # saves play time / activity at most once a minute
        if psutil is None or self._tracking:
            return
        items = [(g.id, g.install_dir) for g in self.model.games
                 if g.store != "Steam" and g.install_dir and os.path.isdir(g.install_dir)]
        if not items:
            return
        self._tracking = True
        self.pool.start(TrackTask(self.bridge, items))

    def _finish_session(self, gid, s):
        """A play session ended: add it to the activity feed (sessions under a minute are ignored)."""
        start, last, secs = s
        if secs < 60:
            return
        g = next((x for x in self.model.games if x.id == gid), None)
        mins = secs / 60
        text = f"{mins:.0f} min" if mins < 90 else f"{mins / 60:.1f} h"
        self.profile.add_event("session", f"Played {g.name if g else 'a game'} for {text}", gid, now=last)
        self.profile.longest_session = max(self.profile.longest_session, secs)
        self.profile._dirty = True
        self.queue_achievements()
        if self._profile_open:
            self.profile_page.refresh()

    def _end_sessions(self):
        for gid in list(self._sessions):
            self._finish_session(gid, self._sessions.pop(gid))

    def on_running(self, ids):
        now = time.time()
        dt = min(now - self._last_tick, 15)
        self._last_tick = now
        self._tracking = False
        changed = False
        for g in self.model.games:
            if g.id in ids:
                g.tracked_sec += dt
                g.last_played = now
                self.model.touch(g)
                changed = True
                s_ = self._sessions.setdefault(g.id, [now, now, 0.0])
                s_[1], s_[2] = now, s_[2] + dt
                self.profile.add_play(g.id, dt, now)
        for gid in [k for k, v in self._sessions.items() if k not in ids and now - v[1] > 25]:
            self._finish_session(gid, self._sessions.pop(gid))
        if changed:
            self._ticks += 1
            if self._ticks % 6 == 0:
                self.save()

    # ---- tray
    def setup_tray(self):
        icon = app_icon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray_menu = QMenu()
        self.tray_menu.addAction("Open launcher", self.restore)
        self.tray_menu.addAction("Big Picture", lambda: (self.restore(), self.enter_big_picture()))
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.setToolTip("Nexus Grid")
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore()

    def show_remembered(self):
        if self._restore_state == "fullscreen":
            self.showFullScreen()
        elif self._restore_state == "maximized":
            self.showMaximized()
        else:
            self.showNormal()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
            self._restore_state = "maximized"
        else:
            self.showFullScreen()
            self._restore_state = "fullscreen"

    def restore(self):
        self.show_remembered()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self.relayout)

    def hide_to_tray(self, g, pid):
        """Hide the launcher while the game runs; it comes back when the game exits."""
        big = self.big
        if not big and not self.cfg_bool("hide_on_launch", True):
            return
        if psutil is None or self.watching or (not big and not QSystemTrayIcon.isSystemTrayAvailable()):
            return
        d = g.install_dir if g.install_dir and os.path.isdir(g.install_dir) else ""
        if not d and not pid:
            return  # no way to tell when it closes, so stay visible
        d_norm = (os.path.normcase(os.path.abspath(d)).rstrip("\\/") + os.sep) if d else ""
        self.watching = g.id
        self._watch_start = time.time()
        self._restore_state = ("fullscreen" if self.isFullScreen()
                               else "maximized" if self.isMaximized() else "normal")
        self.pool.start(WatchTask(self.bridge, g.id, d_norm, pid, interval=4 if big else 2))
        if big:
            # Big Picture: stay open and fully loaded behind the game; it just goes quiet
            # (see changeEvent) so coming back is instant.
            self.status.setText(f"Playing {g.name}")
            return
        self.tray.setToolTip(f"Nexus Grid - {g.name} is running")
        self.hide()
        self.tray.showMessage("Nexus Grid", f"{g.name} is running. The launcher will return when you close it.",
                              QSystemTrayIcon.MessageIcon.Information, 3000)

    def on_watch_done(self, gid, seen):
        self.watching = None
        self.tray.setToolTip("Nexus Grid")
        if seen and self._watch_start:                 # Steam games aren't process-tracked: time the session here
            g = next((x for x in self.model.games if x.id == gid), None)
            if g is not None and g.store == "Steam":
                now = time.time()
                dur = now - self._watch_start
                self.profile.add_play(gid, dur, now)
                self._finish_session(gid, [self._watch_start, now, dur])
        self._watch_start = 0.0
        self._set_idle(False)
        self.restore()
        if self._page_open:
            self.page.refresh()
        if self.big:
            self.update_status()               # nothing to reload: just show the grid again
        elif seen:
            QTimer.singleShot(5000, self.start_scan)  # refresh Steam playtime after it syncs

    # ---- idle mode (Big Picture while a game is running)
    def _set_idle(self, on):
        """Pause the launcher's own timers and drop its CPU priority while a game has the screen."""
        if on == self._idle:
            return
        self._idle = on
        self.bp_home.set_animating(not on)
        if on:
            self.pad_timer.stop()
            self.clock_timer.stop()
        else:
            if self.pad.fn and self.cfg_bool("pad_enabled", True):
                self.pad_timer.start(33)
            self.clock_timer.start(20000)
            self.update_clock()
        if psutil is not None and os.name == "nt":
            try:
                psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if on else psutil.NORMAL_PRIORITY_CLASS)
            except Exception:
                pass

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and self.big and (
                getattr(self, "watching", None) or getattr(self, "_yt_proc", None)):
            # the game took the screen -> go quiet; focus came back -> wake instantly
            self._set_idle(not self.isActiveWindow())

    def quit_app(self):
        self._quitting = True
        self._stop_youtube()
        self._end_sessions()
        self.profile.flush(True)
        self.save()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e):
        if self.big and not self._quitting:
            e.ignore()                      # Alt+F4 in Big Picture = back to the desktop library
            self.exit_big_picture()
            return
        if (not self._quitting and self.cfg_str("close_action", "quit") == "tray"
                and QSystemTrayIcon.isSystemTrayAvailable()):
            e.ignore()                      # close button = hide to the tray, keep running
            self.hide_window(notify=True)
            return
        self._quitting = True
        self._stop_youtube()
        self._end_sessions()
        self.profile.flush(True)
        self.save()
        self.tray.hide()
        super().closeEvent(e)
        QApplication.quit()

    # ---- actions
    def launch(self, g):
        pid = None
        try:
            if "://" in g.launch:
                os.startfile(g.launch)
            else:
                cwd = str(Path(g.launch).parent)   # run from the exe's own folder
                pid = subprocess.Popen([g.launch], cwd=cwd).pid
        except Exception as e:
            self.status.setText(f"Couldn't launch {g.name}: {e}")
            return
        first = g.launches == 0
        g.last_played = time.time()
        g.launches += 1
        self.profile.record_launch()
        if first and is_real_game(g):
            self.profile.add_event("first", f"Played {g.name} for the first time", g.id)
            self.profile.bump_day("new", 1)
        self.model.touch(g)
        self.save()
        self.status.setText(f"Launched {g.name}")
        self.hide_to_tray(g, pid)

    def add_manual(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a game executable", "", "Executables (*.exe)")
        if not path:
            return
        p = Path(path)
        g = Game(id=manual_id(path), name=p.stem,
                 store="Manual", launch=str(p), install_dir=str(p.parent))
        if any(x.id == g.id for x in self.model.games):
            return
        self.ensure_exe_cover(g)
        self.model.add(g)
        self.save()

    # ---- real cover art (online)
    def art_candidates(self, force=False, only=None):
        now = time.time()
        out = []
        for g in self.model.games:
            if g.store == "Steam" or g.id in self._art_pending:
                continue
            if g.custom_cover and only is None:
                continue
            if only is not None and g.id != only:
                continue
            has_art = bool(g.cover) and Path(g.cover).name.startswith("art_") and os.path.exists(g.cover)
            if only is None and not force and (has_art or now - g.art_tried < 7 * 86400):
                continue
            out.append((g.id, g.name))
        return out

    def fetch_art(self, force=False, only=None):
        items = self.art_candidates(force, only)
        if not items:
            if force or only:
                self.status.setText("No games need cover art")
            return
        self._art_pending.update(gid for gid, _ in items)
        self._art_total += len(items)
        self.status.setText(f"Looking up cover art for {len(items)} game(s)\u2026")
        self.pool.start(ArtTask(self.bridge, items, str(self.settings.value("sgdb_key", "") or "").strip()))

    def on_art_done(self, gid, path, reached):
        self._art_pending.discard(gid)
        for g in self.model.games:
            if g.id == gid:
                if reached:
                    g.art_tried = time.time()
                if path:
                    g.cover, g.custom_cover = path, False
                    self.thumbs.forget(path)
                    self._art_found += 1
                    self.model.touch(g)
                break
        self.save()

    def on_art_finished(self):
        found, total = self._art_found, self._art_total
        self._art_found = self._art_total = 0
        msg = f"Cover art: found {found} of {total}"
        if found < total and not str(self.settings.value("sgdb_key", "") or "").strip():
            msg += "  \u2022  add a free SteamGridDB key (Covers menu) to find more"
        self.status.setText(msg)

    def set_sgdb_key(self):
        cur = str(self.settings.value("sgdb_key", "") or "")
        text, ok = QInputDialog.getText(
            self, "SteamGridDB API key",
            "Paste your free SteamGridDB API key\n(steamgriddb.com/profile/preferences/api):",
            QLineEdit.EchoMode.Normal, cur)
        if not ok:
            return
        self.settings.setValue("sgdb_key", text.strip())
        if text.strip():
            for g in self.model.games:
                g.art_tried = 0.0          # retry everything that had no art, now with the key
            self.fetch_art()

    # ---- covers from .exe icons
    def ensure_exe_cover(self, g):
        if g.store == "Steam" or (g.cover and os.path.exists(g.cover)):
            return False
        exe = exe_for(g)
        path = make_exe_cover(exe, g.name) if exe else ""
        if path:
            g.cover, g.custom_cover = path, False
            return True
        return False

    def fill_exe_covers(self):
        changed = False
        for g in self.model.games:
            changed |= self.ensure_exe_cover(g)
        if changed:
            self.delegate_cache_clear()
            self.save()

    def delegate_cache_clear(self):
        d = getattr(self, "delegate", None)
        if d:
            d._cache.clear()

    # ---- folder import
    def pick_folders(self):
        """Folder picker that allows selecting several folders (Ctrl/Shift-click)."""
        dlg = QFileDialog(self, "Choose one or more folders  (Ctrl / Shift-click to select several)")
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)  # native dialog can't multi-select
        start = self.settings.value("last_folder", "")
        if start and os.path.isdir(start):
            dlg.setDirectory(start)
        dlg.resize(900, 560)
        for view in dlg.findChildren(QListView) + dlg.findChildren(QTreeView):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if not dlg.exec():
            return []
        return [os.path.normpath(p) for p in dlg.selectedFiles() if os.path.isdir(p)]
    def library_folders(self):
        v = self.settings.value("library_folders", [])
        if isinstance(v, str):
            v = [v] if v else []
        return list(v or [])

    def add_folder(self):
        picked = self.pick_folders()
        if not picked:
            return
        self.settings.setValue("last_folder", os.path.dirname(picked[0]) or picked[0])
        folders = self.library_folders()
        for d in picked:
            if d not in folders:
                folders.append(d)
        self.settings.setValue("library_folders", folders)
        self.import_from(picked, announce=True)

    def on_scan_clicked(self):
        self.start_scan()
        self.import_from([f for f in self.library_folders() if os.path.isdir(f)])

    def import_from(self, folders, announce=False):
        known = {norm_path(g.launch) for g in self.model.games if "://" not in g.launch}
        cands, seen = [], set()
        for f in folders:
            for folder, exes in discover_games(f):
                if norm_path(folder) in seen or any(norm_path(e) in known for e in exes):
                    continue
                seen.add(norm_path(folder))
                cands.append((folder, exes))
        if not cands:
            if announce:
                self.status.setText("No new games with an .exe were found in that folder")
            return
        dlg = ImportDialog(self, cands)
        if not dlg.exec():
            return
        added = 0
        for name, exe, folder in dlg.chosen():
            g = Game(id=manual_id(exe), name=name, store="Manual", launch=exe, install_dir=folder)
            if any(x.id == g.id for x in self.model.games):
                continue
            self.ensure_exe_cover(g)
            self.model.add(g)
            added += 1
        self.save()
        self.update_status()
        self.status.setText(f"Added {added} game(s)")
        if self.cfg_bool("auto_art", True):
            self.fetch_art()
        self.fetch_controller()

    def change_exe(self, g):
        start = g.install_dir if g.install_dir and os.path.isdir(g.install_dir) else ""
        path, _ = QFileDialog.getOpenFileName(self, f"Choose the .exe for {g.name}", start, "Executables (*.exe)")
        if not path:
            return
        g.launch = str(Path(path))
        if not g.custom_cover:
            g.cover = ""
            self.ensure_exe_cover(g)
            self.delegate_cache_clear()
        self.model.touch(g)
        self.save()

    def set_cover(self, g):
        path, _ = QFileDialog.getOpenFileName(self, "Choose cover image", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        dest = COVERS / f"custom_{hashlib.md5(g.id.encode()).hexdigest()[:10]}{Path(path).suffix}"
        shutil.copy(path, dest)
        g.cover, g.custom_cover = str(dest), True
        self.thumbs.forget(str(dest))
        self.delegate._cache.clear()
        self.model.touch(g)
        self.save()

    def on_context(self, pos, keyboard):
        if keyboard:      # Menu key / Shift+F10 / controller Start: open on the selected card
            idx = self.view.currentIndex()
            if idx.isValid():
                pos = self.view.visualRect(idx).center()
        else:
            idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        item = self.game_at(idx)
        gpos = self.view.viewport().mapToGlobal(pos)
        if isinstance(item, Folder):
            self.show_folder_menu(item, gpos)
        else:
            self.show_game_menu(item, gpos)

    def show_folder_menu(self, f, global_pos):
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.open_folder(f.id))
        menu.addAction("Rename…", lambda: self.rename_folder(f))
        menu.addAction("Delete folder (games stay in your library)", lambda: self.delete_folder(f))
        menu.exec(global_pos)

    def show_game_menu(self, g, global_pos):
        menu = QMenu(self)
        menu.addAction("Play", lambda: self.launch(g))
        if not (self._page_open and self.page.game is g):
            menu.addAction("Game page", lambda: self.open_game_page(g))
        menu.addAction("Remove favourite" if g.favourite else "Add to favourites", lambda: self.toggle_fav(g))
        menu.addSeparator()
        sub = menu.addMenu("Move to folder")
        for f in self.model.folders:
            act = sub.addAction(f.name)
            act.setCheckable(True)
            act.setChecked(g.folder == f.id)
            act.triggered.connect(lambda _=False, fid=f.id: self.move_to_folder(g, fid))
        if self.model.folders:
            sub.addSeparator()
        sub.addAction("New folder…", lambda: self.new_folder(move=g))
        if g.folder:
            menu.addAction("Remove from folder", lambda: self.move_to_folder(g, ""))
        menu.addSeparator()
        menu.addAction("Set cover image…", lambda: self.set_cover(g))
        if g.store != "Steam":
            menu.addAction("Find cover art online", lambda: self.fetch_art(only=g.id))
        if g.install_dir and os.path.isdir(g.install_dir):
            menu.addAction("Open install folder", lambda: os.startfile(g.install_dir))
        menu.addSeparator()
        info = menu.addAction(f"Controller support: {self.CONTROLLER_LABELS.get(g.controller, 'Unknown')}")
        info.setEnabled(False)
        menu.addAction("Re-check controller support", lambda: self.fetch_controller(only=g.id))
        warn = menu.addAction("Don't warn me when launching with a controller")
        warn.setCheckable(True)
        warn.setChecked(g.controller_ok)
        warn.toggled.connect(lambda on: (setattr(g, "controller_ok", on), self.save()))
        if g.store == "Manual":
            menu.addSeparator()
            menu.addAction("Choose a different .exe…", lambda: self.change_exe(g))
            menu.addAction("Remove from launcher", lambda: self.remove_game(g))
        menu.exec(global_pos)

    # ---- controller-support info
    CONTROLLER_LABELS = {"full": "Full", "partial": "Partial", "none": "None", "unknown": "Unknown",
                         "": "Not checked yet"}

    def fetch_controller(self, force=False, only=None):
        now, games = time.time(), []
        for g in self.model.games:
            if g.id in self._ctl_pending or (only is not None and g.id != only):
                continue
            stale = not g.controller or (g.controller in ("none", "unknown") and now - g.controller_checked > 30 * 86400)
            if only is None and not force and not stale:
                continue
            games.append(g)
        if not games:
            return
        self._ctl_pending.update(g.id for g in games)
        self.pool.start(ControllerTask(self.bridge, games))

    def on_controller_done(self, gid, state):
        self._ctl_pending.discard(gid)
        if not state:
            return
        for g in self.model.games:
            if g.id == gid:
                g.controller, g.controller_checked = state, time.time()
                break
        self.save()

    def launch_from_pad(self, g):
        """Launching with the controller: warn first if the game has no controller support."""
        if not self.cfg_bool("pad_warn", True) or g.controller_ok or g.controller in ("full", "partial"):
            return self.launch(g)
        state = g.controller
        if not state:                                   # not looked up yet: check now (brief wait)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                state = lookup_controller(g)
            finally:
                QApplication.restoreOverrideCursor()
            if state:
                g.controller, g.controller_checked = state, time.time()
                self.save()
            else:
                state = "unknown"
            if state in ("full", "partial"):
                return self.launch(g)
        if state == "none":
            text = f"{g.name} doesn't have controller support.\n\nAre you sure you would like to play this?"
        else:
            text = (f"I couldn't confirm that {g.name} has controller support.\n\n"
                    "Are you sure you would like to play this?")
        dlg = ControllerWarning(self, text)
        if dlg.exec():
            if dlg.remember.isChecked():
                g.controller_ok = True
                self.save()
            self.launch(g)

    # ---- keyboard / controller navigation
    def ensure_selection(self):
        if self.proxy.rowCount() == 0:
            return
        cur = self.view.currentIndex()
        if not cur.isValid() or not self.view.selectionModel().isSelected(cur):
            self.view.setCurrentIndex(self.proxy.index(0, 0))

    def focus_grid(self):
        if self.picker.active:
            self.picker.setFocus()
        elif self.osk.active:
            self.osk.setFocus()
        elif self._roadmap_open:
            self.roadmap_page.setFocus()
        elif self._profile_open:
            self.profile_page.setFocus()
        elif self._page_open:
            self.page.play_btn.setFocus()
        elif self.big:
            self.bp_home.setFocus()
        else:
            self.view.setFocus()
            self.ensure_selection()

    def on_typed(self, text):
        if self.big or (text == " " and not self.search.text()):
            return
        self.search.setFocus()
        self.search.end(False)
        self.search.insert(text)

    def current_item(self):
        idx = self.view.currentIndex()
        return self.game_at(idx) if idx.isValid() else None

    def current_game(self):
        it = self.current_item()
        return it if isinstance(it, Game) else None

    def launch_current(self):
        self.ensure_selection()
        it = self.current_item()
        if isinstance(it, Folder):
            self.open_folder(it.id)
        elif it:
            self.launch(it)

    def fav_current(self):
        g = self.current_game()
        if g:
            self.toggle_fav(g)

    def update_hint(self):
        if self._pad_seen:
            self.hint.setText("A Open/Play  \u2022  B Back  \u2022  X Favourite  \u2022  Y Sort  \u2022  "
                              "LB/RB Filter  \u2022  Start Options  \u2022  Select Menu")
        elif self.big:
            self.hint.setText("Enter Open/Play  \u2022  Backspace Back  \u2022  Ctrl+D Favourite  \u2022  "
                              "F10 Exit Big Picture")
        else:
            self.hint.setText("Enter Open/Play  \u2022  Backspace Back  \u2022  Ctrl+D Favourite  \u2022  "
                              "Ctrl+F Search  \u2022  F10 Big Picture  \u2022  F11 Fullscreen")

    def poll_pad(self):
        st = self.pad.read()
        if st is None:
            if self._pad_seen:
                self._pad_seen = False
                self.update_hint()
            self._pad_prev = 0
            return
        btn, lt, rt, lx, ly, rx, ry = st
        # only treat a pad as "in use" once it actually sends input (ignores idle/virtual pads)
        used = bool(btn) or lt > 150 or rt > 150 or max(abs(lx), abs(ly), abs(rx), abs(ry)) > 16000
        if used and not self._pad_seen:
            self._pad_seen = True
            self.update_hint()
            self.status.setText("Controller connected")
        pressed = btn & ~self._pad_prev
        self._pad_prev = btn
        if pressed:
            self._pad_last = time.time()
        trig = (lt > 150, rt > 150)
        trig_pressed = (trig[0] and not self._pad_trig[0], trig[1] and not self._pad_trig[1])
        self._pad_trig = trig

        popup = QApplication.activePopupWidget()
        modal = QApplication.activeModalWidget()

        # direction: d-pad or left stick, with auto-repeat
        dx = (1 if (btn & BTN_RIGHT or lx > 16000) else 0) - (1 if (btn & BTN_LEFT or lx < -16000) else 0)
        dy = (1 if (btn & BTN_DOWN or ly < -16000) else 0) - (1 if (btn & BTN_UP or ly > 16000) else 0)
        if dx and dy:
            if abs(ly) > abs(lx):
                dx = 0
            else:
                dy = 0
        d, now, fire = (dx, dy), time.time(), False
        if d != (0, 0):
            if d != self._pad_dir:
                fire, self._pad_next = True, now + 0.38
            elif now >= self._pad_next:
                fire, self._pad_next = True, now + 0.11
        self._pad_dir = d
        K = Qt.Key
        keymap = {(-1, 0): K.Key_Left, (1, 0): K.Key_Right, (0, -1): K.Key_Up, (0, 1): K.Key_Down}

        if popup is None and isinstance(modal, QDialog):               # drive any open dialog
            self.pad_in_dialog(modal, fire, d, pressed)
            return
        if popup is None and (modal or not self.isActiveWindow()):
            self._pad_dir = (0, 0)           # another window has focus: stay out of the way
            return

        if popup is not None:                 # a right-click menu / dropdown is open
            target = popup.findChild(QAbstractItemView) or popup      # combo-box dropdowns hold a list
            if fire:
                send_key(target, keymap[d])
            if pressed & BTN_A:
                send_key(target, K.Key_Return)
            if pressed & BTN_B:
                send_key(popup, K.Key_Escape)
            return

        if self.picker.active:                                # list chooser (pin a game, ...)
            self.picker.pad(fire, d, pressed)
            return
        if self.osk.active:                                   # on-screen keyboard has priority
            self.osk.pad(fire, d, pressed)
            return
        if self._profile_open:                                # profile page
            h = self.profile_page
            if not h.hasFocus():
                h.setFocus()
            if fire:
                h.move(d[0], d[1])
            if pressed & (BTN_LB | BTN_RB):
                h.shoulder(1 if pressed & BTN_RB else -1)
            if pressed & BTN_A:
                QTimer.singleShot(0, h.activate)
            if pressed & BTN_X:
                h.tertiary()
            if pressed & BTN_B:
                h.back()
            return
        if self._roadmap_open:                                # roadmap page
            h = self.roadmap_page
            if not h.hasFocus():
                h.setFocus()
            if fire and d[1]:
                h.move(0, d[1])
            if pressed & BTN_LEFT:
                h.move(-1, 0)
            elif pressed & BTN_RIGHT:
                h.move(1, 0)
            if pressed & (BTN_LB | BTN_RB):
                h.shoulder(1 if pressed & BTN_RB else -1)
            if pressed & BTN_A:
                h.activate()
            if pressed & BTN_X:
                h.tertiary()
            if pressed & BTN_B:
                h.back()
            return
        if self._page_open:                                   # game page: drive it like a dialog
            self.pad_in_dialog(self.page, fire, d, pressed)
            if pressed & BTN_X:
                self.page.toggle_fav()
            return
        if self.big:                                          # Big Picture (Xbox-style home)
            h = self.bp_home
            if not h.hasFocus():
                h.setFocus()
            if fire:
                h.move(d[0], d[1])
            if pressed & (BTN_LB | BTN_RB):
                h.shoulder(1 if pressed & BTN_RB else -1)
            if pressed & BTN_A:
                QTimer.singleShot(0, h.activate)
            if pressed & BTN_B:
                h.back()
            if pressed & BTN_Y:
                h.secondary()
            if pressed & BTN_X:
                h.tertiary()
            if pressed & BTN_R3:
                h.toggle_panel()
            if pressed & BTN_START:
                QTimer.singleShot(0, h.options)
            if pressed & BTN_BACK:
                QTimer.singleShot(0, self.show_main_menu)
            return

        if fire or pressed & (BTN_A | BTN_X | BTN_Y | BTN_START) or any(trig_pressed):
            if not self.view.hasFocus():
                self.focus_grid()
        if fire:
            send_key(self.view, keymap[d])
        if pressed & BTN_A:
            self.ensure_selection()
            it = self.current_item()
            if isinstance(it, Folder):
                self.open_folder(it.id)
            elif it:
                if self.cfg_str("open_action", "page") == "launch":
                    QTimer.singleShot(0, lambda g=it: self.launch_from_pad(g))   # after poll_pad returns
                else:
                    self.open_game_page(it)
        if pressed & BTN_B:
            self.on_back()
        if pressed & BTN_X:
            self.fav_current()
        if pressed & BTN_Y:
            self.sort_box.setCurrentIndex((self.sort_box.currentIndex() + 1) % self.sort_box.count())
        if pressed & (BTN_LB | BTN_RB):
            step = 1 if pressed & BTN_RB else -1
            self.store_box.setCurrentIndex((self.store_box.currentIndex() + step) % self.store_box.count())
        if pressed & BTN_START:
            QTimer.singleShot(0, lambda: self.on_context(QPoint(), True))
        if pressed & BTN_BACK:
            QTimer.singleShot(0, self.show_main_menu)
        if trig_pressed[0]:
            send_key(self.view, K.Key_PageUp)
        if trig_pressed[1]:
            send_key(self.view, K.Key_PageDown)
        if abs(ry) > 8000:                    # right stick scrolls
            vs = self.view.verticalScrollBar()
            vs.setValue(vs.value() - int(ry / 32767 * 70))

    def pad_in_dialog(self, modal, fire, d, pressed):
        """Controller drives whatever dialog is open: d-pad moves focus, A presses, B cancels."""
        K = Qt.Key
        fw = QApplication.focusWidget() or modal
        if fire:
            if d[1]:
                if isinstance(fw, (QComboBox, QAbstractItemView, QSlider)):
                    send_key(fw, K.Key_Down if d[1] > 0 else K.Key_Up)
                else:
                    send_key(fw, K.Key_Tab if d[1] > 0 else K.Key_Backtab)
            elif d[0]:
                if isinstance(fw, QSlider):
                    send_key(fw, K.Key_Right if d[0] > 0 else K.Key_Left)
                else:
                    send_key(fw, K.Key_Tab if d[0] > 0 else K.Key_Backtab)
        if pressed & BTN_A:
            if isinstance(fw, QComboBox):
                fw.showPopup()
            elif isinstance(fw, QAbstractButton):
                send_key(fw, K.Key_Space)
            elif isinstance(fw, QPlainTextEdit):
                pass                                   # don't type a newline into your notes
            else:
                send_key(fw, K.Key_Return)
        if pressed & BTN_B:
            modal.reject()
        tabs = getattr(modal, "tabs", None)
        if tabs is not None and pressed & (BTN_LB | BTN_RB):
            tabs.setCurrentIndex((tabs.currentIndex() + (1 if pressed & BTN_RB else -1)) % tabs.count())

    def toggle_fav(self, g):
        g.favourite = not g.favourite
        if g.favourite and is_real_game(g):
            self.profile.add_event("favourite", f"Added {g.name} to favourites", g.id)
            self.profile.bump_day("favs", 1)
            self.queue_achievements()
        self.model.touch(g)
        self.proxy.invalidate()
        self.save()

    def remove_game(self, g):
        if self._page_open and self.page.game is g:
            self.close_game_page(select=False)
        self.model.remove(g)
        self.save()


_CRASH_LOG = None


def install_logging():
    """Write Python errors and hard crashes to NexusGrid/Logs (error.log, crash.log)."""
    global _CRASH_LOG
    try:
        import faulthandler
        import traceback
        _CRASH_LOG = open(LOG_DIR / "crash.log", "a", buffering=1)
        faulthandler.enable(_CRASH_LOG)

        def hook(etype, value, tb):
            try:
                with open(LOG_DIR / "error.log", "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(etype, value, tb)) + "\n")
            except Exception:
                pass
        sys.excepthook = hook
    except Exception:
        pass


def main():
    global ACCENT
    install_logging()
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NexusGrid.Launcher")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)  # keep running in the tray while a game is open / when closed to tray
    cfg = make_settings()
    saved = cfg.value("accent", "")
    if saved:
        ACCENT = str(saved)
    try:
        from PySide6.QtCore import qInstallMessageHandler

        def qt_log(mode, ctx, msg):
            try:
                if "qt.webenginecontext" in msg or getattr(mode, "value", 1) >= 1:    # warnings, criticals, setup
                    with open(LOG_DIR / "qt.log", "a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%H:%M:%S')} {getattr(mode, 'name', mode)}: {msg}\n")
            except Exception:
                pass
        qInstallMessageHandler(qt_log)
    except Exception:
        pass
    app.setStyleSheet(build_style(ACCENT))
    app.setWindowIcon(app_icon())

    hidden = "--tray" in sys.argv                          # started by Windows at login, minimised to tray
    use_splash = not hidden and as_bool(cfg.value("splash", True), True)
    splash = None
    if use_splash:
        splash = Splash()
        splash.show()
        app.processEvents()
    win = Launcher()                       # builds the window and starts the scan behind the splash
    if hidden:
        pass                               # lives in the tray until you click the icon
    elif splash:
        QTimer.singleShot(1100, lambda: splash.fade_out(win.show_initial))   # short: ~1.1s + a quick fade
    else:
        win.show_initial()
    sys.exit(app.exec())


# Nexus Grid logo (256px PNG, embedded so the launcher stays a single file)
LOGO_B64 = (
    "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"
    "RK5CYII="
)


# Bundled YouTube app (a zip of Apps/YouTube: main.py, README, example config, run scripts)
YT_APP_B64 = (
    "UEsDBBQAAAAIALK0b129cnEBST8AAHPNAAAHAAAAbWFpbi5wec19+3rbRrLn/3wKhP72EExIUJIvSahRZmVZnjhxbB1JnsSfo2VA"
    "EqQQgQSDiySOR/sE59t9kPNW+yRbv6puoBsAJTrJnN3MOTJJdBe6q6vr1lXV7Xa79T7Oz/Nx0HN+Gse3/Um8zJI4ioLESbN1FDh9"
    "Jw2X8yjoz0L6dh0kaRgvvVbrw5vgNk+dvyXh9MI5vwxTZxKv1g79O86X0yiYOjdhdulkl4FTtnQiP19OLoPEc44u/eU8SJ1ZEi+4"
    "VZyE83DpR8OWQy/9yfnCee6skiBNCVQWzwNqkziTKE6pE9r7q5Xj+lEaO77TPsLvbWdyGa6ccMnPo2AeLKfOOs6dib+knuHkqtsj"
    "4I5DfZ6Hc+cknGR5EvBjGZgTZo5Pneht3DHNEj+cX2bO2J9ceTywcwK9SuIJDcwJbsOM5h0F/jJaOzeXgbz5JlxO4xs1VkLVab4k"
    "wEPnl9U6u4yXzsIPl95q/YvnvMo0RtLyXeEyi51ZHkXpJAkIZLxs3VyG1IaQ7yziZZjFifmeeBUsU2rljNfONJj5eZQ5S/q976x8"
    "GmS/L+2CaSucYVadqZP4jM1L/5rw6CTBPI/8RMHr0UpQp2kwzuf9ub8IVv7UoZ7Uo2VQxzQO0mUnc9IgoPWLCUi6igl1vsL5jb/M"
    "8Ds958Em/g1RRpbRbCbxlOYbZh2aNC0RkRdjgmY4uSKCULNbEcr9OTd0lkEwTR13tb4JxtdhcNNrhctVnqU9Z7X2c6KOPOw5V8F6"
    "HPvJtEu/hqt+uEwzn5BIIyK000ucRZiClnsAvxS0tWioGVG/NyEqJMLxNVIxjTFRLAZA5IG94Rizp4mFGS3tj5c+prEMboTsiPzV"
    "FhEyfq1X11hPY5ncPA2MBQLYeJU5NKZuSW1RMCM0Z0S+9KZ8NaCmS+yzcEmvyqiXAZAALPyMCPmS5rCI04zpvTL6lBZrFScZ8BAm"
    "znvHvw2xp5I4n8uO/UXQ+4sThePET9YMxV3lKeMxXxHo6wCD6dIYCfAsnOeJPwaDCH01sBEPebQeWF9vGRThSnp5v6ZEuEKXoMdk"
    "euPTjpyGqT8nZKVNWOC1edEHWYLMmYhTolIi6sD5NR6nQ56Dahvd+OuUXwqKddKVn4V+5Cz963BOH+ntrkyGUIduy4AGkGZOEAWL"
    "gEiYGqh1C5cMBo2mYRJMuDNIHXghciC6o30aCbnL+DLiaXMgfBbe0kL53Ie2JAMiNILWS8ro8SQHCXMB2TpXzHkG1I7wMnV2dxRG"
    "Dh29M4krYKMRHTozP4x4HsRaiQM7buDRzvKd786IxUURDSLB8o0DAhcUc1nRJqMeGOMyTC9pmFHsY1N2Cb30mbg0yQKa1zIjJncV"
    "YkuBF6C/sSP0tmXkxTNZAmKSQFJfNjtWK4oJIcwlhBNfhasVfcdmDfypTO4EO4i+07yoOfFKJgC19RnKVRCswBiI7dFeIHbMwmkW"
    "R9MgEVr1VPPR1M/8Hq3qbSYrHGKiUdDVr8RYaYMHq0ta74QoI16SLExjGV44XwJlIbjwBJIiSUKilRiseBxkN6CLJMeSAllJ0KdB"
    "B4xlEDTD+Fsck/iktZ/EOc3IBfWC+/IqCN0rEeycJMEizBeOP+3PiCA0/roMB+x/rYSxkhqCrjMi1Uk2AKE44CdJQLN48faHgtxc"
    "Rcsey0CX6JSwQEJk7aGPq4QihBkGXtKjhaN0vaRR054Cn1WCOQWiMBkSfnFyxWCSIAqJE6whVYhkI8hJ6n4DAr0iPocvem9hLeIJ"
    "jVktvMkTXGKENHo/aly8KKC1x94jjPkrJVhkm5fcodilKesixFUJSj5hHgbyhByCfkOYPj0+fPHDsbdQAznEevfVvleKhI+twP3G"
    "Mb1toYmc909K/DaVSfI68UhkVM4kTxLZPMyB3HEeEqdmvYdXn5YppxWjeaxYOqWQLcS+NN8iysmZtwmbVFNmkZbSWmK+0GCIfi7D"
    "6TRYDjCYZaF+MYy/Y+fIeEhquTQ/YnNRSFSsxJWziknvwM5kzUfLxEF23VMUTBuY9pfT3t3pz+I4a7fQPpn5k8DTNI6tSYQRJMxP"
    "gQxGDljDNLgOJ/pXZtlz2mgtLXR4K89m4QS8+fzvA6KENI6AXOFZswiKCet6q4jUp4Ib/JqTjJuDGpJA1hsCEVtB8/M4WRDMNMwC"
    "T/TUaRJeKx3SeNiS3rRKmhd1W63XMe99RiWvFRBb2dDUPJwGpkJGb8Y+VLu6Vd3VfSFk+j9f9mqwGAe0cFA4iIrAbRXfjJeKpfqR"
    "VtN5rIyJZSxajT8GYfukHxGaX5z+AMBM5gSQfhmvoQVih7VeKCVBce1Uqc9CLiW9l0IMKjZBIwYHLtiFSvO6FMNe/T/WCzZJUlaQ"
    "fNFI7v+vFK8uI6gutMEST1lObh4LkN2nAQFJtINSqLVC5iByrRVuMRzsMGZ4JHZpbsxFiWL3LW6yBRxDhaENOwlEkOqxaZF4/39J"
    "QCbLkqW8It4SKdRdGB9pFl7jfzCkQLsljy/68OAGjigfVp9CDaHHhiJCPQ83vEZ1hPy5BuPCLBWjL6jBjRPB5BaTBl8FW4X+u5wG"
    "iYE2EMLz+4fBQ8frIOFMRZz4KTGTNUiboPx0PxS0JASsfJoFtX5/f2sYF8aW0qaJaHV+MrnEVnpO8E43Df4etJ9lfpJtfD/UVVLJ"
    "eBfGK0gpMOGWkgH3jPkSbGzA7EAM8ooEJBAnl+s0JF2ynJAFAUpAKgLwBhoOyUzav9O147L+J+xui/XGgNmy4J0mmiSQ5sCOYf1p"
    "vQp6WwCiRZeNe5Ql0RffAyeESlLkQm1mgyq9VrvdbrXCBRtFfjInW4IWWX2Xf8gQqv3gLYLMh36pn0Bx0Z/jVH9KClBpPlaug+KX"
    "dfGRRGHAenfxQ7gIWq3Dk5PRi1enzgFB9IgPXnrEHZek/Lv6uz8Gf7x0RyPoRqNRt9s6evvm5au/jU4Oz781Ov5KvM9V8HpO29C2"
    "2t3W2fnb08O/HT/Qx1KrqVer9Yjs1z/+H4E5y4KVszskPeiK1ht+mZtANAUmKBF2NwH7ApQNo5aCNXSyxv+koRAcdxWutAPCAbJ7"
    "zoKsowVJcaUvdYkjaG8EJBuRDDF2dqOIsyKLCc6YhTchWGnEE3C+vsN2ioZECkkMRwpPVTQIj4261IMhFkx7BAjqOo1lhDXpCVkz"
    "ax0tSIdlu3NyKWoFZDZ0cFgppOudHv/7u1enxy9oXY++p/U9o7X9ICZSu5hAm1b2iffE220rW8BtiwMAD3a8p+XPhbtFnnztPX1S"
    "PtRMQZ7tPpaOF0Qk02DmjHhXjdSsXfXvKM0SrVs4jMWURpgEHlmjUzJa3aT98/QLglhp3yqFoZPlqyhwaT+7qy4rmyu9ImmXmTxD"
    "JXFLJvFOr6vHoxxEwbQYE636CKutRpMl62HLFrwNm9+r9Vbm1CRYNTEL70To6k2cvSQFcnqcJHFSe88baMcy0GCJ3TCaBiRTSL2f"
    "0NK6eoSxngWW9YJ/YwSooTDZ6ukBKTVyKF9coINA3YebssPM6EOUhyEPLaZMREqLMmt/SGlSqwvnowZyh/akwJYAvHbX6lrOzCOK"
    "pokTmLL7NwcfjYndGX2J8mukVryk6/yl+swA020afE3INM7mY/GKO/HMqW892FyyL9sNkLBXhVuQQhoRo8gca16e3emPYail1gxo"
    "L/uWc5b5FrM7JJoySa6Q5eWaQxKTqklilOg6MFdQiFheWSWCV9KfNvEgX80TFnlD52On53RE2pSD6xbjviL1Zw7O8PFOz4NEp0dK"
    "WUb0vnAODpw28c7He+1yQtLnQ5t0GFaPZ5E/T9sXBKSUw94RWf7nx6M3b0c/vnrz4u2PrfrON1qzi3oEZ5pNGB8wmOA2mOQZvKDE"
    "//oLcEFaDvyjpoOP/b5MOqAvn5czvbC1mc8/l8GXv1pMxRw/L8WJfGNW4vg4mJhUF9aCXyzGis9LhIPAfUgb0Xk1E4qN4pi0uSi8"
    "whnBKkjYfQ55ZVNlO8Bbe8AZHGDKWMQo/CmEJs41cGLh/siyjVhynIhFXYGT5tPYcV+Hy/x2sPAnb8+6LOn08Bb+Mqd/18Ofl3bH"
    "WRt/P9pLcOf0F9bsCsQTqTVSWqu+zRI/JJlxtk6zYHFMxoO72xW/DeHXJO7Nm+a+zdJqNXJ26B9n/ozNaKUJLlkjJx5B6s+VfAnW"
    "Heg/BfvUSqOS6PprIa9bPG6R6hrsPMhGyo3cc94tV1EO3ywTUatV9PRODt+dHdOu2TF+e3n46vXZ4Uv8/NInwfqnK4J7w/JcQTwB"
    "6ogj7TnaM0gqFRyxCXu9cApiaLTdP00bfHH88vDd6/PR83fn52/fjA6Pzl/RPz8cnoAbMRG0n5+/GZ29fXf+bXvotLXVSxsc/z1y"
    "DstGx4dn52gDs049V42el43evD0VSGL0FO0eOe/LRj8eCySYpWyTFq/7qWx0/lrABFcj642PyPY0Wp0WrZSdKQ0fkXFqzO/88JTf"
    "SEalPXQxR91LfzHOExwWhBPts5aex6+Pj1RXeB9Gl0EEEI/EInWV10x63YGQzosDlDAtj3aKk+LSj1I/6WEfKxTmhnOecRDFN/ed"
    "0tBPqej7UwKh3Oee8xanu4YzOcLhxNRhC5dYVnG+2HfaOJtrs1+bDGeCAWUMPlvay2M4v3E8Vj/5ksNSmDCa2F6cHL5oJLV8BUSW"
    "Toy2iIg2cFJZbHnADp36AgPR+mViLZbvMKAPnfMkD3rlYoMB9K3DaX107sZLpzinftA2f3TfSXZ3n4+utwBSOTsVbiC+uLYcOE5J"
    "afkHaaY0ld29nZ2dnkyD2Ec8F8phB6fDfmha5/7jvS+ffeV5+OdLhULiz35GkGivERgybb7q6QGkAS0nKXDFYRC3TRWNCWUSORDJ"
    "i3evbR+OmggmaokgsGaOPmgtibs4cgWBNQC6JUDMiHsPABL/nsACzYgYe2Q6U4eW57T0AonDrcc0zZ4ZFvOmG/WRdsz52B/XtBLx"
    "YHLpL5dBNICTfAzruNyQEx92L2lncpJFc5JXmGv2lVoyhW4efl+mwseuUJ9l8VNmQ6Vn0III198IHpb0koxv0MJThvuIkaAALvPp"
    "PDAgipN2E8iFfztKV9QY0HbLUT7SDt1VeEs250AoBBo+to01AxJpkTAVa0nVC9b13bcFgRge5RqtKMAWsTifQDAG7JJ22nKsNBJ2"
    "SWgBh9ooNBVXmhLfbe5R4Xw9kQjf5qT+9WGAcJRB5BMvZ0IE8ShO3StOMhrdl15LQX19+Pz49VnJ7gqBTUxSzlI172Q+Sr8+N/ip"
    "EsrcVsSz/F4KYnp0Is7iExHMuqPmzNw3uHL6uzup9VRzZ93gC6MBxC5+L5y755DD+lkpV6nNt+LIPYMjFydeupnF19svazLktzxk"
    "ScHBVPr4qq1WwJ2GKfvAGfdk2PrYLJM4ipNuuRIr7SAWouhJENlUc1eCw/aEPtWqxdWog1x9rAwdl8zidRnXkq+8liKq6jqaWpjb"
    "PoS19Wh358uj3R3tojJ1MLf9nFu82Hn8ZO/IaqF0K7f9E7fY2Xn2+Pmu1UKraG77PTd5+fL51zv2a1j3ctuv5S1PnuN/doNTbnC6"
    "sYHWuNw2a1gbm2n1ym3/XfnyjGa8dji065Pclr0cJ1MjXqO6TXqFJGSEs9MVmk7rkVKPiiUKOThGnWGWa1isz4tXZyevD9+P3p6+"
    "OD4t3I7GMvWMBekZqO+ZSLZR2itw1zOR1LNQwT5H5iNVEhHt6UX/hLS+n/O93a93NXxW3Oxnj6u6lfFsp6ZeGQ/3eM/wAGo4wBB6"
    "6nU9BbqnwWhXKSIEpqPLfDoKyfJM3RqH7TkVBqp8V+12+1yOB4VTI+5ABxbIgYfP2itOhT6qjcxbmMz34DYT3wqCKNN76EP2ZhkW"
    "wWo3E0sgKptxXCexDUxMHOfjhstJlE/5SNZQg7XilnZb2hWpwk7GAdoiMAzRGlMepofjGm4H7ACrH9s8GazD6/4Z5FabDzhoZvhN"
    "7wf6Df3x0w/0yvbdRatwmZZnzuHSqa9d6U4RpNNbK0vgkTntFlAsNylcbtLQdjFi04TLvDw75glpd95H2z2iZ2gQ9ofidRX/UcPU"
    "7ecKDZZE5AloUSr/dstud90SVxw4Q2hq2ugNiKqRL78JQDZgydHvEBexYzH8B1BoUjW92+r6AUAvPgnbAq4ZubJz/gTE1gdT0jMH"
    "QtvUfPTk8c6eRc22xL6zTkcYsGIsOFAaycbTRwjyDeQcTjLXNgm7RosPDXreRbVbTd2zIVS1vlr/ivJXOq31cSTp8WmWusb5puG1"
    "t7y2zIjgZYRRaXYAtyXcBUsiBuItB+08m/W/anfhsZwNazYnaXDJqEASuBUfy7kz2xuPbXEVrHvOtR/lvDmMjp6w8W4dOk0N4XTU"
    "3G3Ab6+uKDfAMDBMsC68fAUPo8sD6dZa4xjsQRg0U+7efJojDS+c13w8WTJv8dB8NHBtncyI7/qY/8Emr/qpG99xFOfRVJmJU1Nm"
    "9AjBEA3aLTh0PhK04sBAEb90+NO9k4+Hznf+tU/KeIgzvuWvEnfGEraICQRJlNbuQLuTCMb3QcASGX5pknGkSElYi790RqNbqMSr"
    "JJiFt1B7/es4pIlHvhhmIGmCoLZ6RwJgUx5H+uedgL968x0pUscvRt9BeUogbwcD8YOJEiihb7SCMsEx+zriFa2CL3Z/BzpIvhgv"
    "/TBywgXhg1gfgCh/AHQDMrp88RZKFKP27fURv+xohwGpMIi59ZzzMIs4Gn9qwOnDW+ecH/90TorN8ooewykXROE4gBeCBvzm7bkj"
    "WgetUJ8DBjlei2AUIxyosbRXkhKStqmlDEki9QDEd0jhCZJw4mT+uM9aNIDc+NFVoRSxQ3Gg3AmcsBH5cA8hwJLjrImKgyTtIYw1"
    "Qz4LdK2ejKWMclG2k4Rz4rSnvwiWecrHIYi9BFhM1qOO6KtjA1c0OmJqE9a4koC2ZDhXyiAHay45zDS5ylcDEFTKrk4EXgIIQjB5"
    "6SSiVNCLiGdkXnD8ziKc9hfhXDnktceLiFbirgnGL+ts2v/8F1I9iRoWfR3rlU4QXM1wfeRK0PjbUTyhcTgcP4EghqiNyGsA8VMJ"
    "rSW03yQck9xl/ColkN0T8dRfd1LHnAU7Xf053G59mY8zJqYBkpj7KidIIonDRSErnZSVXb/Alsfnk7RKalFC9ZJwhuMcicEG7rWX"
    "gVd1TNviJlYhuXJSg/BdhpDkCBVN4jQtV2SIJ46z6znIHxG6J5g0oXenryVEkSP8XLWZiMZIu0ZcLwIZ+JXXvPhK0SpWSwdO6dmJ"
    "h23CqVYKoyoQmlEDv50GogfB5LJIg+g60K/YkwD80hI4+/bw5FjHPzM2aLeY+z3lRBpS4FwoSPoVYIhnPAcyN3Do5qjttg8i0uyA"
    "LMsFGCOoRbsNNIT0t9zn5rSIxAxYTzT5AI8FhBD5ksglg2DEFUCSgJ6GEqcKoqc22WUXc6ROU9IvCC05MiHSgh+0BTXlTCDoRPtS"
    "SGuIdj86OxPaCYQrISYl8tdeSwX5CKs/EzYnjidWWTvYRDSCKWcTJOt+gtPBBPKhs9+ye79K/w4C+ZbEBfWc5UsWMu4lfe8qo5dU"
    "DPcz9YMWizN4//YdsfeuCev5vj73pkY5gUJGExEjQ+qR3jjhTe8xHOpIlAVvjBtsAoq3Dv7Hz4pmfx4MvCxIMzdnNY7jSIye2CS6"
    "o/qpbOgcHBBOBrwFOs6//Rs9Ej554ic+qcyXfup2rjvd/dZdHTtHQh3//+FH/QYUuYqEfx7887//c0J/oTn+POjWUSZTJCI8VVHA"
    "HZG1PaejxSrLiCUczS7bUyyWcW48AQlyrHi3QoFHwt/Wr7F5DCQFkUYRMHApKAwiWDOHGclkElOB28Hvna7zz386nc5+0Topmj5H"
    "jBPtqKMoJGo+JSJ3u/sF4hOPd5/zlwNnBzAS7zJgJxV+MHCHGZnrwTIIqonqPyh67hc2g7tpp8jy68nhP0Lpa8jpib8KDD7m/s/d"
    "Z8OvS4/Yrrfb3/OecQQDTtATP8xMGMLcLABfD3eflQB2vCf9He/Lp8RtDoWF9cFETBjMwpQamYL8RQIQU1vkRFHgbCTJB2BscNAg"
    "dSlEOpEJQxK/Hu/s3O49cfrfKGzt7n3R1dklM86MJGLg3IVxTCg0488hPpHQm6iUCNKsWX/yTHvdFbDfHAAv2JjynVaOcMTf1eJQ"
    "i2fmYiqq1etZA0dYssABYwJPEccWAE264R/vNtKFwSMaKeMNcZu+SB5TZ8USwqPeT+Ibss4jyUxUgc+xJIOaYNQObSv1ISlSWSwJ"
    "BnIoxBhLr8q6sGhTDjK1mp1PWKoClxq5u97jp5XF2rVWD232dkycGPgt+M5+6c94cA2s32tM+yVCsmFRpCYzMllR4fa7UJv9MElI"
    "rq6SOIsRGe6RonGMQ0U7UGtKgDkN7rectDgtcA+pyX0C2fDUNHBGE8fE7+LZLA2yHwWRxNEIjcWv3yraFb4m7h4ctwGaSbnyqbvN"
    "1DbPqON/AClfdGjHbxg20ZIx3IE1SJD6jnKL8qnPcEnKuc7dSKs0WURAQOSU6W5E3VPwGMedxFHkr5D+QWoVNMueE2QTr2sTaQWB"
    "WiZUMWiLhioX2SjaMP3NmL/TCDe9ZoW8fRH682UMs86hOQ05YICdS9U8eU6+vQ6qtiBgsGtD8rIJXxtOx9mIVlu6wCNCoZEkDyiv"
    "ZlD3JfEM2TDB7Yp9DxAuchzD5gHbIChMkNADJPfy65AiZtjOtAqlH0HsQm6LIzrqJmbDVJlPfclpmQbXbNbEyB8zbSAxGgOVr6FV"
    "FtL/IFJpFfLFSqJnSAe5DioqyAsg8Qiz2rjpNYYOfu8+9qJgOc8uSx1FBCsRU6+wn+jL/r942/0phI7hczLtgXMvve9br5Ue0KZF"
    "XgI2f/riCxM4B8rb7TWXRw/1uexzp16jds5HvVRD/aEnbxnKPwW2hwXa74qN9nZJGhHs87/wwfE3ZCfOi6MoTtZxLgk3EfBTOF3a"
    "CKECy4LoZU8VMR2SoQEboqQg3a5IOodZj/tKiRPEWid5xEkm6uxSV8WAUgUgZCeuJQJGzrYV96O94sazjAsCXIdpOEY+8lIsyqEM"
    "kcdeIfFX7CQ845oqDSQOfBd0RWrzsbzs+frV1O0IhD6D7XTrJMH7Q0EugHDYc6DguB3VWZZKBhiCfmzg5mOoIkeEGnhwDgrq6Kj5"
    "9FVC38eO80X5UCF++GR1S6sT0RsevXy5Q/85n0nQqb/M9pt69IXyh3urW/s56eRRMNz1dh7bv9MSLtOQz9TgLdjxdp+miBUI7Gar"
    "WDXSVr/9+B99WqPgdrhLQ7SfjNnB10d8ep4Ov6oO606hqkA3PHvq+OboMoymLiOxySo9UueyB8qqYRFqHNiCY2saNxMm4YpjV1m3"
    "AvAsyFhpajbe6kJRXmMypabnHvsuXodp5iUB4n8KOlQrr4lJaHDDFAOl+hFvK+H50+kmYGJhU2v+3f0ITziSoSTuZSj+tLuaiT34"
    "nHWPmdYdeywBO1zUInA+H6hBElzJjny1zGJES9ALxgFJ7DAmdtVJFzFZQmRDj+GdpB8mXM6BfgiXTNbFL8z0FM86uyTeM9URp32V"
    "9kz/0gKOkTQu7gocWEixFja3eDWN3F1Wo6aVI3edUUUTwTGRETvHsawklOdE0pD6tHeUH3dCYEKcABXqWk8iOIjVIZ5mEi84oz6J"
    "Fz3lki2OkXusa7BXu6g4VMILxZU8RlK3ROOAM7dLxyrrgFwbgwtctCss8I1KCDfItABOgyTyh3ugVw7HlP+TW+rnqjYehwB/obt4"
    "HD7RdQbOXineJ2uzA7wgZXup4FDpMJaxleaK/vFsglzDA+fVEiVSsrXSFMuxa/XA3SDzt/WI6LYBzzXRs0xq8ysarqWhzC6pz0s3"
    "nAIige0THitPAILg0JN15YleBtTgYA+2mnghr4qQCVYS8hXrB0Y3gtyfrvelM335QaWrutPbbqPGUYGIKJkGmH8EJFDaOMzbJpjr"
    "OszaYO7p2Iwt6gYfiS3GLcyniuDMLl+ot3xuLi3gSuO/lJRatU1NEubG+7XHBqMuOXrVJELDgukZ4TIpL73zT7Ve+MBYxgcm3A5k"
    "VxqgIlfAMvtaKuwQmM67k86g8+Ltj286A1iEZZD/idRKY/8U8sphctQ8l8Qa3whPtJSqGgMxiMA54wRSow3tntfxTZAc0Rj1TjTd"
    "DBu8E6YbU2xKMTCYznYaVpeZUg4+0CiP4SwotIlxPF170EvJ9EqbxXd5sv7XRuFbPB5WWJpigjQOjOav+LuJLVHfj6giMISRhEXl"
    "D7yo/En4DX2ECl/ho02sXyFKDj8r3FKTGdkDnx3w2Ahz3UaxoOgSvrm4CHNLg0jX5CJZ6M4Q6y2mKeI1uvroF+exjgq4tuQcO8r5"
    "uA9l+zLtg6Vf+8yHjaC0aawj3gmCrsNCxmsoweKzMEmRpDdBTt4sj6x9zILTK08eGFu09J/RPIAFhT1G1Iedi33TcYlnaNOo/clT"
    "tK9rnLRPxEzdZHpsUBA3KYZcRkq/inBwko/pNz6qXxfnl5VNDNM55ZNE1KgitV8XimGDrUnlQWiDUnmCAddxGPDiqOQK9pLAKdEr"
    "lKeiKAprV1xOh1USqCM3YWoXzlHeCxkjQPAwdfa+JNrhgBGWpSTYQ/kuPR6h6aMJl1VlZxu+VEP992r2b1cov1XB//djDdSAtF+x"
    "BTcySLNPnUQ+gTweGOMJFsqtjauJytI4+QH2TAOh0aMjTWYPTFCG7jbNCoH7f3xOHF2nWzTPrGDcZYy9srxZ19BPkYZSxt27hXah"
    "NAu7LJuxvzCPPinuKxxrR6HPpde0uRDc+rRy8ygec74bYiGS4Lc84CQ15Tth8tYGin9NO3UOw0gjwSRYV8QxlEjjqUZyKYIrRN5I"
    "/PutGohqU/l13163Q105aENjTKff7zsv6yWe3KTMZOqikYqROTcyk6wondKmihHfZAftiNu0ZztNG6KTSLpwFU6Ed+gzNl0h9cZf"
    "qvAYXe1MRQENOPpnYEX0SBTCIvAlbicJuG4iwUAOhHil2BdVlkRh2jE9se5ZPkZEGGr6WmFGThspIQCyIC2w3eNwBA4HVzH9yivv"
    "lFXZJPLDQCkgRqlMhgY691fiCEcUUELqg08CoF54a6jqboV26S3Fthtqa/WEqnGIIPnni5isZxqNWb/v9fHLcyZsybJbLIJpKDFf"
    "zeW1uC5mWrj7dMFdgGioGFcx0FWhQJNBcZ4NG+JCIJLo3ajB4vsiXkipTquYKWdWeXUXkeKN4F4cN9DU4CdxWDc8eS9Pmh49R3LB"
    "73VBCjI3+SDZg77JATkNr7WTh+xgy/eooBYPxf84SdNzuBV05ojtz+OKqoh8gHq6gw+sn/InPrgc7n21usU3OTRWX0tICz8hHjjs"
    "78JXuUP/40/oYLv+nu78N7MbKGWeQFceJvOx7+49fdrbwf+8x0+7Ze/h46oH1AICh+ulT0szxJtpZA4D21GgnjEk7Zzc233y5ZOv"
    "Hj978qUBgwjuCNFxRHVDk84XUHIl9HAsdKcdiBJjGmakKM9UCVoFSLV4SZR7wvsUp0CqdrNO5mBfus7mGAdLWltmEObScJBhX4oX"
    "8YkhZmGeIHa49YVUX+jsa3qwzR7TicpnGHfNhHzKYVGbzotsWnyAnEtLTkyRggbZyywVRpTLOWIRSeZ44378gtBw29vw+L087naa"
    "lXRuw0l7v0djKXYjFlQqSsETF3GpUJIM8SpfFcp1o8dW8wbTsG1mOjBwYbM0Mx5l/m50LivGxc4S3kj6LSHJ1+RHFc+zZ/jENjC4"
    "DRDU6VkJ4m7TdBWD5fCzP5lwdLYkkQ07kzv7m4chhOxupnQWNA1E8aDM+JfNiTfzPWSM9Krna8v2uSULfm0F3d1jDtyn8tsUtPBv"
    "iW2qz8RV6rTUd3Z7m3br9Lar5nsfjd33DkVtm16CPU/T3vySBxefzaB/LZdrWNiSi5RFGJWEEQlSDCEjORpk5jCq4qSRizRj63ds"
    "KCYlGcQmpxtmwDkKB2q0nnLyuB2/V4TbfyClMDhQaTjtC/oh88csgS96Ujmnp5xMki3pk34jkY8CtPRK/XsOlF2HaS6ZsbOQNEox"
    "MVRs8DxWiNWBWsUYtzsmS4PsPFwEcZ65Jl00gtl4eufc9VAHQbvTzL5ieTNJalvrbeGisWWJ477Xla4H6qjLtLvKSvVQKsiUKfr5"
    "WeZzlWKOw2ariS0BqPcqE0IKgnDeiGGz9cukk+sw4eLZBVDDS5cla8kVkGNBI1FCp82URUVdcfCoYmyS0mwWbUUQCKf3dDkK58eK"
    "A8qxzd6Baa0ODFcEx76I8wtg+FTOHHFccVz11fUZcHrpda4ZC6YmsMFc+H787whVwebp1B4pt973fJgkruJqk9c+aoKfosa51sY/"
    "dHah3e3hz2P8eYI/T/HnGf58iT9f4c/X+LPTUSmqHzq/sYqOP6wfJqzE488af3L8CfGH45pXZT8f31NWKfFnhj9zVvDx51f8uWI7"
    "AH/+z3/8Z9nzH/iFVfsJ/lyzmo4/S/xZ4E+P689xz//9vzoXrYsams5WAaqTn7J+Jjj4KOmhQ6dzdnJ4dNzR2Z04Sl75E0wPIZz6"
    "1LpX6XT0+vjw1OzEAVudpoZvz47thkQR1BDDrI7zkBu9jC3NmCGZwlcqaPCRGFBlBtPCwpHhmyzW7EAYMjvIPupUQtsRNASbribW"
    "fvCv4E07jmoD7El13umhOowG8kxpd8UkuoWBSQ2FAb7hBILC0Lwa9+lRp2xkxbgIvkutqUhgNkcFfr8J4wrNJXjU+yRW7RWQ5EMp"
    "5HzjaJLxBbyaPfFjMTKFYDy/x0Q2LNRHj6f4H1M+0oeHj2azWd3EfVaxjMNlX0xoGDIuRyH/lbb63g61c4j8nu7Rh25pWvN3A4A2"
    "+WZRwLvOj8L5ss9HGsMipKKD45hwtuZ4LPqxeFLCmdGTfhr+IxjuKlse69UvjLJhTqZigrNF5hwpzUgEtDI+K5Bm/iKM1sPTeBxn"
    "ce8wod3cSwkUdUrC2QbbVFEz4bzJvwx7SfPf3+tTIaIUS21d9asUlKieb0f8qrHtYjFeYrf6JF8LTnCVj+Ve50TNS6IdG1+Kj8Ty"
    "CRjdTEIB8fRJOd6OVEg5wOFlX51IPgGp1he0QOjKX27rrOKm2+63vV38r77DdveEftUwh7yVjOHLdoNt8SUe9Zyv965vlDvJcBXt"
    "0jMHc7PdRUDqH6X1AjW/KU1hC9Rw0xqd8a8ds8WWyGMnWZVTlTxgr8JlNC6BW2f3WZPvTnENcfVp2thtYHeWj5CW45JosM9CcEg6"
    "af8m8Vf8O0Hvc+rkkP/28cMGxiF0YzqzGBka1Rs1rHowEWmDtWii+OZ4S/plJobmtWWw+PT+3F9xmKONLPzSvPv2Oxb8+rAtncMe"
    "iYmVZt1A9TbmcGd8ruOW4Wp/j0nOaaG2bYsws8eWOLsfQxsVyTrO8G4TZdZYtsAa2ntKl+LPRWk0fGF9ykBSMyrNd5qYvAyX2XYY"
    "RMs65tS+9n1/3xDsCPhlqc68X2NOESF8+9yiYwC2dbaOFF4aOGdSuxCeJ47gH6qLsfDl+dCREin0H2mveMSmH77+x38O+XxVqbwb"
    "kII3a2Ro0Wk+5x4PurNVzyZnz/dj8QRpQ63R3fPbFt6eggMbvpTfoFf8VsFco4WIPE6n2bNHlBtkZWTvNqHGpnG50SVsNvojQccV"
    "S/ZTIo8b5vqviysjVRJmdDXFJI3CSfBggolnWzJdY5EB9/fGoFm42zIQzVrcjdFoZisjJA2DRXjTVlFjZUl3TLEaPXZl2KJXVuxY"
    "0e+eELeyUbFyOgGviXcXO4DDrbBdELfVSEFn6+XkNPCjV3AdbsxlVA+bl9zt8PNHOoKcv31AjMeBKkQ54vZt7aH8AFLSz9oXJg/g"
    "51YmEn7wpF5RMzfYr7SF7EP42jFO+FykwMsnGSUpSB+dcT5GcGQRqF9s1yYE5eNFmN3H69yNPKpD2ysJF26jY70Sr2OzQdXUStmH"
    "HBkQqaGQ0F9NxB7AAuZSUcG701dH8WJFFgvN+LfuBhbJ0U+bpiT+i43boxzoZ1fMLxuO2AtPQt29YGS+N7oVGnH5xUHN3WAFJVWh"
    "iZS8D5wSHxshlO6ljVCaqVE4JA5i+rvde18hfrR7wHc6D/SHe+1T48Ca8SW+sYaxCPXbYJpYjqEWuBuYksloGk+S4Bp+yE3xp8TD"
    "1YdnuUg2TMB0Tv8eDNgOcDnOvc93soUjptyLWmcjLNg+E+NEih1dlpDlGODvbX9lhbVXRXn5St25iWpKAVS0aj6HNRfpwcPj5jOE"
    "/2Kd7iGNziwk8F+5utZh92DgfI86djopdYKb5VDKO71CqHhxaMSFbya5lCFCwjcuTyVRE9B8cBEq16hCiDFHpctBVz1Qw8xJlWMs"
    "HAtZx1gcAVc7ybrxBRzeqjLc8kRfm8aVoLiUYjCt5lQoy+ghgjE5SiPLOY9X9wCRLLzz2C3TC+p5eEWSXaVgx/maq+f9/igZq+BQ"
    "/RidC4Jrw9byAn/2GVk70I+Rwp358zdFfaJXb07enXdUFrf1BLXpDk+PD1lXv9tvOE9VlXRRvVzKLeMWYb8oBFe9QJgr9Fmz/Daf"
    "/l0ykUv2V2fC1MrEGXtZrViCW6l9tcVeusynVjiB6skH9uqz3utdiy1WMvg3uxCooe1YxCvLR5/ktS4DAyVzZUcchezfssLymnza"
    "9dMMdkzDIzgUt2DZf5Ob2unAUbS7Y/uAn8FtWfEDb/KbPxO/eWOI3ad5fi2X6u5j+/W269VIsY5JYQszFDDakyzrTR5kleBT9Wvh"
    "56oPE0HJ9yWsr/ylKRnQ/AF3XP2ciT10T1e3+6ZHl7guLRx8StZ4pKL99gPi9hWfCt7tGceIZbvNFFtdeJgcDEdV5r736K5CQJZj"
    "HH5x5dDmJb8RJ/eYZEKFDHb1aUnzohahPjL0bRHEtbob8IOfDMi8sKaXjBFWXXmzAQBYC5FYj9G+6gre7JOj3t0qY9HkXjFEDD77"
    "V6ezy2ehO51G4YcLGb4NotWDgtTi3Z9teLS/iYFux58xRdjmv3N+peZT5q6o6imXXPe3qP+jC4pxFlzZVgWE6aRPuSS8LCHPZXPD"
    "TKV1ifRltYmj8lFA6OYS5TYyvsE59VqlpJ5O2QMB5RK1Wd1O+VYpX9PpNZvivwuPnylE1s3yehBcLREIuoFFharggQSk1SmSSXdT"
    "CRGoXHxp8Z9c2PjJsHYlR/8b7XQYJyE42Z9WZZhvYZ3npJ4F0xFpvEp160H7LS80OAuWVkyWZIu4Ul2ZSaWs5Soqt1QwEkc+yYYB"
    "X8siWTtBcNV18iXX6fSFn83CgHRko/YG6oBD02Zy1aVRDYTovJ5JFI/H9L2IkF/HOQosZKyeFpcUWFXJM626Fhf9+lGOe3x/Td12"
    "o5rrdtvW3Y9F/ewGoHIVn3nPJz8xroIsLvBjLMJlXFyHi9tfRkSGahXKBZCTE+OaLmCyvKWZH1zGtHPVnZTOHFqqj5Qlt4I6ueNa"
    "ymtwfhW+pt1mXCl0EIOh7mT5t7aqIl5UEMd8LhzVW11uWRYJ57dBW9a3bnuIp0zcPQ+Zzv5iPPWHqDrPN0e5O92u3PblzwJim0va"
    "6riBNF4EwveugmClzDeFA1JFrmUpMm/qkyoMsxq3Oanf+LYqXPbYkhrG36WHq7BA+etAJdAioQtmXmEryGKw6oSjAmGRzEAl3YhR"
    "KRIPizpCwYrRyEXErlE6Hl+9kVrOA765V98EFQk6JFuEywUMjZuu0yuUS1aXTyOCU8qApihVvJIkYE/APnw7+yMmopQ5vx9d6fWW"
    "TY3r5GGrXgsHai/8W75+OwkQhYyFnxIBXDI94L4uXfcds8bKV2dckrc5d+BfrhswLolR1yAJEahRDev7o1e7ParS48EtrnMqi+t8"
    "1O1QnwrnOaeLFlCKG6o+FY5t0xfw5OapTwVG9r8Bw7wdq4rYJgnQvjKmU96btVXXX+2u5aVaW/WOit7WdVlb9Z0ZODNv5Pp03Gn1"
    "UVB4p64zeq/vguzvDh25SQgf+DIhdeHQT1YTdb0QPsoNQ3cFw/mbRGVrzalgPac5X8HIlbERJF0aJopX7gvj8ZIcGlW8nIgIoJ1h"
    "KLcsRcd8ayDbCNMiW1nK4KPKIN8e4cm2PbeTIo27PJtu8uxrEc56Jm7aVId89QRgyVrjiiOhup/SQcAgu+TKu/8YAPRS1tS2SCWV"
    "ArpFCqmoCZI1W1YhMIZclPNl092zrjhVEf3qmlNGlL6RlO8i1TeRktJTkGSPdRmdsIucU2F/9nUhZaFYJTfLq08rfsZQl1KQC5xE"
    "3Z4Snw9UQXsF6R75ojM6elxKY8QT7akbOHoO18k8YA2lKogKOVSRHPywhIXyNcUXu1Ht6hQUN9l8bY3duYqzg8331VSHZl4wavSr"
    "3Dxa6WZeJ2p0sm4ZrQ4RyIN1gX8rUpx2s74FlA0r1PjdaWrDnUcTXWUTQr/SDLeU8h3rWkBrBySSykgKc2VGiGusBriOqOEgJ76m"
    "SGqnkHYSJ9NuaeA/kjz9xv1JelAWeI6+UzOeBgWnSPybVvPNn6QLpHJBjVTWF6Vy7egcMizYuaia0BANKMUuU5ooruMGkTvictbX"
    "ScV5WnADIueQWIABRCe08DDEFadypZIgCv0xcQ0ohDQ0As33vKAQO7sjzQlhGMrkLS+K5dMAXgfebmvPXiITT6a+X23QQJTVu1Uv"
    "GjvaF6XWu1cuUq2SNhZJX0nLV4ZvfLzmxwY+zvWVoyo7J18NQGIDMOmBoplgmXLdFbhp5c50rnpXAinq7nZQkgHZ96SjGxZIOuDl"
    "lruerVuAO6kB5r2sLd8kEfLFdealEeZL+K6PmzBDdAaXIw1NOFxEXd1iznfkKPLD9SzGwU566SN2yCmFvNfEadaKPbmWbi2SXl0o"
    "qPSCO5gn6rIkXD5WvXXYvhZKqo806hS6SbdpPLf3jEfUDYHFWsj9Q7rdPKQGHaYcVSGJoI1UVH6xBNsf1Ha9cH70+TYZProjrFcd"
    "He67s+co+POc+EoGbtAlI8QYl+xVmNUVweSF6SilKVXu5qpdR8YTE3Zx4BAORmpkbmXuIqrfLVdRTlrA9DhJ4qQOCczYSyNiNO6u"
    "t1O/6Kt2M9595jOIlXkWl7wS3sgmLDL2FnzlAPs5am8pzO0Cy9w34DFb1vbvGnvtjjXRBEiVU0cgrbpVeQiGTfozHO+DskK26ip3"
    "I/jOd2eix2ZNs3rE/gJosBP2HZmKK/g86kCmSF2EEguSWgdZtwGKUYzhKuSXhVB84xUxJ9aqbnC9GY03Et8TaKoBTMWLwhVGuAZZ"
    "6XnlwkBRUe6nDoMrb6ore1JkWUqCFck8WuF4jPNh0mfXi1UWL7z6gjXRsqE4kMyOApcx3HDl3DY3v20mKKYlrt0SYaYfZc9BJ7k7"
    "UF9Yi7irXAHHCqoameinMr7yvYDRExUEAaMFXNVSwFpVHA1tDGqKK30/Qz0EdWeleHYgEi7d9uHzs9G3h+ftbnfDNXdqklJF/sL5"
    "yLOCCiYzaltalOhYrByQfRmw/DhQOpcywWhEfMerny8nl1K8FZymqHkDa866dFNf5enWrt/la3kr416yms67F38qjAv4EVweELtu"
    "bSAVGr7cw4mIjgY/Ed+kiCF9uJLPPYc3fNldX6coXk5UwcW1Ck+9nYuh2rwxe+NpMOyVDHAFUyOxkbJtDuvqoumCxhIz9jCYAkpU"
    "2Q/vI26DtmVJSbXGKhJ1N/DKBu+Vdl41NZWzidbDVz4a+CRb0BW6h1HQbTURyAY7i73qGxZd3kCSWXVwm2EINfQc+0JYYy614dCr"
    "9Nbaed82xqCv7mUdatNgYNW5ZbMP3PXCuKgyanrPT03v+Wm79/y05Xvet5tgLfz1OBDF21D/hfV0G9rDD5FlUTAdLf1r5e6EOjsq"
    "Apylb6+mW3bvG9xP/+8Gd/vg4E4bR1c1R4Sfb5wEAgbum0XDa98/9Nr1739tKcQqW0gngsPiyJOJ6U+Z4ZhBebSNa4ctlj/jmr3Y"
    "6fcLpQsnX6II5lKf/3U+yoe7DjPmj/J2647XGqeZLW2WZU+Kt4hMqcybGBoi78eH/FWlMaa9VSFKt8YGDFeVotMXViKJoNuzDDDl"
    "b+Nb0kk7z7lwR9Vg1164vnG0PYAXTmlxYz4QIyymquzEpT7TNg1UOavzVcieV1MuRnqKI764uNu06Zb1kp6te2RAETPe5O6659ry"
    "pivLazxdk+KsLavwsYBlK2L2tCqmWk291CWn4zgy6ecen7kZ6dfubr52uPE94kgpB7txZzKpyv4c1pbOdM7AxBynaiuj3neTd2Z4"
    "36ptRxBqAspIqGwSP4IxtoY7JZVbB5gppff5lIozysZ12W4tylJmxen177gBmoFcNOj0m2RO4+KQPbhcS/DAMkeAl7KBUDxSud2a"
    "y0Kyrmzu3Ca3Pu495Xv5ImR6OaXjTNiKdmkZYArPHN8Gwa4IrqpnF4mO+Y4sdjyqhQ0Nn/cjVTkmlYoxuImBb2FYBYQNei2KZAbq"
    "8mLlMWXzkFT7ZWyCMXvhRizcYY3LAVHXtDjLUMymbfGqwg2yNdHbnsN7Sf9BV+cfoktUU/uX0GVVjynpscf+RNZibLbB2uRfnH7D"
    "eULjPtdgPvR3L+qAvnE+GY4BRj3ic4FyUoWs2Symt16NWbuxSrDbMYRG5/evDA31ouZysoz2JlkWxXPraMQ1l7T28CHBVRyvbEeR"
    "xsVn7h+auPYgTKCKyFEECePy7hYZV7NHri541Cw+O9h0ftSIyIZDJvnw8KiLq1TYZ8+q1RDeEPS2N1ndgGgmSpqFdUfFm4fk7Wbf"
    "RuFmaD5w+6Z+rLcRPdY5XdX7sVm504f29QMu1whg4k9G3FZxlhWtRaJUbhdSB3Li167EA6jjEJMJG0fp5YGWw+IlEaPYPJTBTYeS"
    "Lc93pYmRNhCjSXvBi3qyfFIWsDRVIpn9K5k6u5OjMKy/vuJBx+DZB25zjNxnHz+SU43zORnJUl3gXvpS1XEQFwHUVab7XHJaIYeG"
    "E0XFpQAMJorjK4kBCIJI6lNzJThlZshJIg2pDIxT56Y4LoK7ZZsj9Eitgz5AN8/M8xUKRnpFT4kvO4Dm1v2Ek3X9CpSXUh/tBkyr"
    "nHBwzVfd7DoD51ntcHnh347SFSGoflhYPGpfQNtYEcFEKZlMEywtjChjSasH3p96fqmOk26LHtYpk+p8SxqQhCA0dl7f23lNnRtw"
    "PLIiFMrtKKnRButCu+ZAPOMoic+R7j3WAh/ZwKgqJ1Sjh4+oynOY+op3P8H1O82En9EWxvisZ2rA4HZVd3ExVE2AptY33PJYiO+2"
    "UhPGZYHiUShBWl6gnk0tXefzKhV/TpOx4a+3h7+24a+3gQ9JdetwTWVwFbyOPm87+8aTmS0VMLNWr/txejv09mZ3PTLh1/yp2+5u"
    "dXi4+VBws65sYBKumBuEZwFltgSHNUEPS1viXqXWYk0rHx6Px3tfPvtSS27duWi08OfEQXM2MFB7yy3eV+mByzAAsefguNL5/HP6"
    "9ylYGlKh4A3GpX+OpByVQwjnS+aK5WtoSgSer4bmE+5+8aiqhfdVb+ohiJEO8uufHvb/dGjGd0uB3z8vzv/923fn754fj96dviZs"
    "tC+zbJUOB4ObmxsPRa/yceBN4sWgjVkdGtV6JfK8raOQJEVC6sUa2SPI5OA487FfVLcnLMWJ0ndYpne9FhfRHB19++pk9B2ifROI"
    "4SJDzflzskOsJI9N5ZilukBzIsn4nvwqwUORIVm5Q4Gh6keVGkk/53tf7j6V4PGiTS2XrpJdiFSBfXV/h3q0b5ZrO3ryeGfv+X6Z"
    "nbavrj6wb1K1/2tIX9s389W42lM1W61aL6qYQrycVKpj8zqqW0QVryui1z1/FXocHF69P/RO319WTSUbcyJOmW0jvAsnvCNVJuQy"
    "zsoY4FL5frtSrhx1L88R0sS/L7PC+fSf64qqLPi0zOpW8aBFSjfUYsSbcqz/jDX2QJxLuvLyoDPolLDJmupkKm0ZacRhVgQyqCuU"
    "EX7r815CfJTcDpov2e3dnIshl/fa9wds9qwK/7cooP1BEHXh/KKB/OKsaEKIqoAa4F/7YcTbvs9ZBit9y0vbhlPBp2BJT13iaot6"
    "1Nr/ifsD0yqgMhRWsz2V9xOQht4Q+GQeRlq40fNBNpomh7aUBWiXuSRbZANgKvACdGshTBp3atb9b9RCFplPBbG0PzVNRoNmxwGv"
    "RBLModokmnSlhRFcwbtg4YeFM3rlJynbEX4y58/eYTJnFnbCT9xpgAuJeCQHbTXoHgdg9Y24FmZIagICklHqK1glQbX7fSM4Xx+H"
    "HbRxe1YwQgJ8u0yJRQz+Qfs1R0RAVBt5iS57SMOUNVdQAk3MzyNU87/SNR5sqnHacgtXOA6jMFsPZFppV71vu7HLspNV9IkjRw7c"
    "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"
)


if __name__ == "__main__":
    main()
