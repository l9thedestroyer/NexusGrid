"""
YouTube, Xbox-controller style - single-file version.

[Nexus Grid] This copy is bundled with the Nexus Grid launcher. Changes from the original:
  - X + B pressed together closes the app (also a "Close" chip in the legend you can click),
    so Big Picture can launch it and get you straight back.
  - The process exits cleanly when the window closes.

Run it: `python main.py`. It launches straight into fullscreen on
whichever monitor the window opens on by default now - pass --windowed
if you'd rather have a regular window, or --debug-gamepad if the
controller doesn't seem to respond and you want to see the raw button
codes it's sending. It checks for the packages it needs (pywebview,
inputs, pyautogui, keyboard), pip-installs anything missing, then opens
youtube.com in a window and bridges an Xbox controller to it.

What's new in this version:
  - Launches fullscreen by default (use --windowed to opt out).
  - The left stick's up/down is inverted by default to match how most
    Xbox controllers report their Y axis through the `inputs` library
    (pushing up moves up) - configurable via invert_stick_y/invert_stick_x
    in config.json if your hardware disagrees.
  - The left stick and D-pad now have separate jobs: the stick always
    does spatial navigation (moves to the nearest element on screen in
    the direction you push it), while the D-pad triggers fixed actions -
    up for fullscreen, left/right to seek back/forward 10s.
  - A gamepad event that fails to handle (e.g. a JS call firing before
    the page has finished loading) no longer silently kills the
    controller for the rest of the session - it's now logged and
    skipped instead.
  - Persistent login. The webview now keeps its own profile folder
    (.webview_data, next to this file) instead of an ephemeral one, so
    signing in once carries over between runs - no re-entering your
    Google account (and therefore your YouTube Premium ad-free session)
    every single launch.
  - Select/back use real DOM actions (element.click(), history.back(),
    exiting fullscreen) instead of synthetic key presses, so they work
    reliably regardless of what kind of element is focused.
  - config.json (optional, next to this file) lets you remap buttons
    and D-pad directions without touching the code - see README.md.
  - An on-screen legend along the bottom of the page shows what every
    button currently does (built from your actual mapping, so it stays
    accurate if you remap things), and can be hidden/shown with the
    View button.

(An earlier version pointed at youtube.com/tv, Google's old "10-foot"
interface. Google now reserves that page for devices that have gone
through its official TV/console pairing flow, so a plain webview just
gets redirected back to the normal site. This drives the normal site
directly instead.)

Log in with your own Google account inside the window to use YouTube
Premium ad-free - this is a real embedded browser session on the real
YouTube site, so nothing about ads or DRM is touched or bypassed.

Default controls (also shown on-screen while the app is running):
  Left stick ................... move to the nearest element in that
                                  direction (real spatial navigation)
  Right stick ................... free-moving cursor - point at anything
                                  and press A to click it; touching the
                                  left stick cancels the cursor and
                                  returns to normal navigation
  D-pad up ...................... toggle fullscreen
  D-pad left / right ............ seek back / forward 10s
  A ............................ activate the focused element (or click
                                  whatever's under the cursor)
  B ............................ back (or exit fullscreen if playing)
  X ............................ play / pause
  Y ............................ open on-screen keyboard to search
  LB / RB ...................... seek back / forward 10s
  Start ........................ scroll to top of page
  View .......................... hide / show the on-screen legend
  Physical keyboard ............ works everywhere already (it's a real
                                  page) - click the search box and type,
                                  or press Ctrl+K to force it into focus.
"""

import argparse
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STORAGE_PATH = os.path.join(APP_DIR, ".webview_data")


# --------------------------------------------------------------------
# Step 1: make sure we have everything we need before importing it.
# --------------------------------------------------------------------

# (pip package name, minimum version). pywebview in particular needs to
# be reasonably recent - older versions don't have window.events.loaded,
# storage_path, or private_mode, which this app relies on.
REQUIRED_PACKAGES = [
    ("pywebview", "4.4.1"),
    ("inputs", "0.5"),
    ("pyautogui", "0.9.54"),
    ("keyboard", "0.13.5"),
]


def _parse_version(version_string):
    parts = re.findall(r"\d+", version_string)
    return tuple(int(p) for p in parts) if parts else (0,)


def _installed_version(pip_name):
    try:
        return importlib.metadata.version(pip_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def ensure_dependencies():
    to_install = []
    for pip_name, min_version in REQUIRED_PACKAGES:
        installed = _installed_version(pip_name)
        if installed is None:
            print(f"[setup] {pip_name} is not installed.")
            to_install.append(f"{pip_name}>={min_version}")
        elif _parse_version(installed) < _parse_version(min_version):
            print(
                f"[setup] {pip_name} {installed} is installed, but this "
                f"app needs at least {min_version}."
            )
            to_install.append(f"{pip_name}>={min_version}")

    if not to_install:
        print("[setup] All dependencies already installed and up to date.")
        return

    print(f"[setup] Installing/upgrading: {', '.join(to_install)}")

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", *to_install],
            **kwargs,
        )
    except subprocess.CalledProcessError as exc:
        print(
            "[setup] pip install failed. If this looks like a permissions "
            "error, try re-running as administrator (Windows) or with "
            "sudo (Linux/macOS), or install manually:\n"
            f"    {sys.executable} -m pip install --upgrade {' '.join(to_install)}"
        )
        raise SystemExit(1) from exc

    print("[setup] All dependencies installed and up to date.")


ensure_dependencies()

# Safe to import now that we know they're installed.
import webview
import pyautogui
from inputs import get_gamepad, UnpluggedError

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False


# --------------------------------------------------------------------
# Step 2: configuration (defaults, optionally overridden by config.json)
# --------------------------------------------------------------------

DEFAULT_BUTTON_ACTION_MAP = {
    "BTN_SOUTH": "activate",     # A
    "BTN_EAST": "back",          # B
    "BTN_NORTH": "search",       # Y
    "BTN_WEST": "playpause",     # X
    "BTN_TL": "seek_back",       # LB
    "BTN_TR": "seek_forward",    # RB
    "BTN_START": "top",          # Start (hamburger icon)
    "BTN_SELECT": "toggle_help", # View (window icon)
}

# The D-pad is separate from the left stick: the stick always does
# spatial navigation (below), while the D-pad triggers these named
# actions. Only directions listed here do anything - "down" is free
# for you to bind via config.json if you want it.
DEFAULT_DPAD_ACTION_MAP = {
    "up": "fullscreen",
    "left": "seek_back",
    "right": "seek_forward",
}

DEFAULT_CONFIG = {
    "fullscreen": True,        # auto-fullscreen on launch (on whichever
                                # monitor the window opens on); pass
                                # --windowed to override
    "stick_deadzone": 12000,   # analog sticks run roughly -32768..32767
    "repeat_delay": 0.18,      # seconds between repeats while stick is held
    "invert_stick_y": True,    # flip if pushing the stick up moves down
    "invert_stick_x": False,   # flip if pushing the stick right moves left

    # Right stick: free-moving on-screen cursor, for clicking anything
    # the curated video/channel/sidebar navigation can't reach.
    "cursor_deadzone": 8000,        # right-stick push needed to start the cursor
    "cursor_exit_threshold": 1500,  # left-stick nudge needed to cancel the cursor
    "cursor_max_speed": 1100,       # cursor pixels/second at full right-stick deflection
    "invert_cursor_y": True,        # flip if pushing the stick up moves the cursor down
    "invert_cursor_x": False,       # flip if pushing the stick right moves the cursor left

    "button_action_map": DEFAULT_BUTTON_ACTION_MAP,
    "dpad_action_map": DEFAULT_DPAD_ACTION_MAP,
}

# Human-readable label for each action, shown on the on-screen legend.
ACTION_LABELS = {
    "activate": "Select",
    "back": "Back",
    "search": "Search",
    "playpause": "Play / Pause",
    "seek_back": "Seek -10s",
    "seek_forward": "Seek +10s",
    "top": "Scroll to Top",
    "toggle_help": "Hide / Show This",
    "fullscreen": "Fullscreen",
    "quit": "Close YouTube",
}

# (display label, badge color) for each physical button, styled roughly
# like the real Xbox controller buttons so they're easy to match up.
BUTTON_LABELS = {
    "BTN_SOUTH": ("A", "#107C10"),
    "BTN_EAST": ("B", "#D0342C"),
    "BTN_WEST": ("X", "#0063B1"),
    "BTN_NORTH": ("Y", "#FFB900"),
    "BTN_TL": ("LB", "#4B4B4B"),
    "BTN_TR": ("RB", "#4B4B4B"),
    "BTN_START": ("Start", "#4B4B4B"),
    "BTN_SELECT": ("View", "#4B4B4B"),
}

# Left-to-right order for the on-screen legend, roughly matching where
# these buttons sit on a real controller.
BUTTON_DISPLAY_ORDER = [
    "BTN_SOUTH", "BTN_EAST", "BTN_WEST", "BTN_NORTH",
    "BTN_TL", "BTN_TR", "BTN_START", "BTN_SELECT",
]

DPAD_LABELS = {
    "up": "D-Pad \u2191",
    "down": "D-Pad \u2193",
    "left": "D-Pad \u2190",
    "right": "D-Pad \u2192",
}
DPAD_DISPLAY_ORDER = ["up", "down", "left", "right"]


def build_hud_items(button_action_map, dpad_action_map):
    """Turns the current mapping into a list of {label, color, text}
    chips for the on-screen legend, so the legend always matches
    whatever's actually mapped (including config.json overrides)
    instead of being hardcoded text."""
    items = [{"label": "L-Stick", "color": "#4B4B4B", "text": "Move"}]

    for direction in DPAD_DISPLAY_ORDER:
        action = dpad_action_map.get(direction)
        if not action:
            continue
        items.append({
            "label": DPAD_LABELS[direction],
            "color": "#4B4B4B",
            "text": ACTION_LABELS.get(action, action),
        })

    for code in BUTTON_DISPLAY_ORDER:
        action = button_action_map.get(code)
        if not action or code not in BUTTON_LABELS:
            continue
        label, color = BUTTON_LABELS[code]
        items.append({
            "label": label,
            "color": color,
            "text": ACTION_LABELS.get(action, action),
        })
    items.append({"label": "X + B", "color": "#C4302B", "text": "Close YouTube"})
    return items


def load_config():
    config = dict(DEFAULT_CONFIG)
    config["button_action_map"] = dict(DEFAULT_BUTTON_ACTION_MAP)
    config["dpad_action_map"] = dict(DEFAULT_DPAD_ACTION_MAP)

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            for key, value in user_config.items():
                if key in ("button_action_map", "dpad_action_map"):
                    config[key].update(value)
                else:
                    config[key] = value
            print(f"[config] Loaded overrides from {CONFIG_PATH}")
        except Exception as exc:
            print(f"[config] Couldn't read config.json, using defaults: {exc}")

    return config


# --------------------------------------------------------------------
# Step 3: JavaScript injected into the page for navigation/actions.
# Keeping all of it under an __xbox prefix to avoid clashing with
# YouTube's own scripts.
# --------------------------------------------------------------------

INJECTED_JS = r"""
// Only these count as navigable stops: a video's thumbnail image, a
// channel's avatar icon, and the left-hand sidebar entries. Titles and
// channel-name TEXT links are deliberately NOT included - just the
// thumbnail/avatar "pictures" - and this is NOT a generic tab-order
// walk, so the D-pad/stick never lands on page headers, filter chips,
// the search button, hidden sub-menus, or footer links.
//
// YouTube periodically redesigns the internal markup/class names of
// its video and channel cards (it's mid-migration between an older
// `ytd-*` custom-element scheme and a newer "lockup view model" one
// as of this writing), so hardcoding today's class names is fragile -
// it breaks again the next time YouTube ships a redesign. Instead,
// this classifies every link on the page by two things that stay
// true across redesigns:
//   1. What the link's URL points to (a video watch/shorts page vs.
//      a channel page) - YouTube can't change this without breaking
//      the links themselves.
//   2. The on-screen SHAPE of the link - a thumbnail is a wide (or,
//      for Shorts, tall) picture; an avatar is small and roughly
//      square; a title or channel-name link is flat and wide (short
//      relative to its width). That distinguishes "picture" links
//      from "text" links regardless of what CSS classes are in play.
window.__xboxSidebarSelector = 'ytd-guide-entry-renderer a';

window.__xboxIsVideoHref = function(href) {
    if (!href) { return false; }
    var u;
    try { u = new URL(href, location.href); } catch (e) { return false; }
    if (/^\/shorts\//.test(u.pathname)) { return true; }
    return u.pathname === '/watch' && u.searchParams.has('v');
};

window.__xboxIsChannelHref = function(href) {
    if (!href) { return false; }
    var u;
    try { u = new URL(href, location.href); } catch (e) { return false; }
    return /^\/(channel\/|@|c\/|user\/)/.test(u.pathname);
};

// Returns 'video', 'channel', or null (not a stop we care about).
window.__xboxClassifyLink = function(el) {
    var href = el.getAttribute('href') || '';
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) { return null; }
    var ratio = r.width / r.height;

    if (window.__xboxIsVideoHref(href)) {
        // Landscape thumbnail (~16:9, roughly 1.1-2.6) or portrait
        // Shorts thumbnail (~9:16, roughly 0.4-0.75). A title-text
        // link to the same video is much flatter/wider than either
        // (e.g. 300x24 -> ratio 12+), so it falls outside both and
        // is correctly excluded.
        if (ratio >= 1.1 && ratio <= 2.6 && r.width >= 60) { return 'video'; }
        if (ratio >= 0.4 && ratio <= 0.75 && r.height >= 60) { return 'video'; }
        return null;
    }

    if (window.__xboxIsChannelHref(href)) {
        // Near-square avatar icon. A full-row "click anywhere on this
        // channel" link or a plain channel-name text link is wide and
        // flat instead, so it's correctly excluded.
        if (ratio >= 0.75 && ratio <= 1.35 && r.width >= 16 && r.width <= 120) {
            return 'channel';
        }
        return null;
    }

    return null;
};

window.__xboxFocusables = function() {
    var items = [];

    Array.prototype.forEach.call(
        document.querySelectorAll(window.__xboxSidebarSelector),
        function(el) {
            if (el.offsetWidth > 0 && el.offsetHeight > 0) { items.push(el); }
        }
    );

    Array.prototype.forEach.call(document.querySelectorAll('a[href]'), function(el) {
        // offsetWidth/offsetHeight are 0 for display:none elements and
        // anything not currently laid out (collapsed shelves, etc.).
        if (el.offsetWidth <= 0 || el.offsetHeight <= 0) { return; }
        if (window.__xboxClassifyLink(el)) { items.push(el); }
    });

    return items;
};

// Diagnostic aid: run with --debug-gamepad to have the D-pad/stick
// print how many video/channel/sidebar stops it's currently finding.
// If a count is unexpectedly 0 on a page that clearly has videos or
// channels, YouTube's markup has likely changed again - open dev
// tools on the page and check the pathname/shape assumptions above.
window.__xboxDebugCounts = function() {
    var sidebar = document.querySelectorAll(window.__xboxSidebarSelector).length;
    var video = 0, channel = 0;
    Array.prototype.forEach.call(document.querySelectorAll('a[href]'), function(el) {
        if (el.offsetWidth <= 0 || el.offsetHeight <= 0) { return; }
        var kind = window.__xboxClassifyLink(el);
        if (kind === 'video') { video++; }
        else if (kind === 'channel') { channel++; }
    });
    return { sidebar: sidebar, video: video, channel: channel };
};

// One-time <style> tag for the focus highlight, so the "you are here"
// outline is an explicit, high-contrast rule we control rather than
// relying on each element's (often invisible) native :focus style.
window.__xboxInjectStyle = function() {
    if (document.getElementById('__xbox-style')) { return; }
    var style = document.createElement('style');
    style.id = '__xbox-style';
    style.textContent =
        '.__xbox-focused{' +
        'outline:4px solid #FF0000 !important;' +
        'outline-offset:2px;' +
        'scale:1.03;' +
        'transition:all 0.15s ease;' +
        'position:relative;' +
        'z-index:1000;' +
        'border-radius:8px;' +
        '}';
    document.head.appendChild(style);
};

window.__xboxCurrent = null;   // the currently highlighted element (card mode)

window.__xboxSetFocus = function(el) {
    if (window.__xboxCurrent) {
        window.__xboxCurrent.classList.remove('__xbox-focused');
    }
    window.__xboxCurrent = el;
    el.classList.add('__xbox-focused');
    try { el.focus({ preventScroll: true }); } catch (e) { /* not focusable, that's fine */ }
    el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
};

// Shared spatial-nearest-neighbour search, used by both card navigation
// and the on-screen keyboard's key navigation below - given a set of
// candidate elements, where we're coming from, and a direction, find
// whichever candidate is the best match for "the next thing over there".
window.__xboxNearest = function(candidates, curRect, direction) {
    var cx = (curRect.left + curRect.right) / 2;
    var cy = (curRect.top + curRect.bottom) / 2;
    var best = null;
    var bestScore = Infinity;

    candidates.forEach(function(el) {
        var r = el.getBoundingClientRect();
        var ex = (r.left + r.right) / 2;
        var ey = (r.top + r.bottom) / 2;
        var dx = ex - cx;
        var dy = ey - cy;
        var directional, cross;

        if (direction === 'up') { directional = -dy; cross = Math.abs(dx); }
        else if (direction === 'down') { directional = dy; cross = Math.abs(dx); }
        else if (direction === 'left') { directional = -dx; cross = Math.abs(dy); }
        else { directional = dx; cross = Math.abs(dy); }

        if (directional <= 2) { return; }

        var score = directional + cross * 2;
        if (score < bestScore) {
            bestScore = score;
            best = el;
        }
    });

    return best;
};

// direction is 'up' | 'down' | 'left' | 'right' (case-insensitive, so
// 'UP'/'DOWN'/etc. from the Python side work too).
window.__xboxCardNavigate = function(direction) {
    direction = String(direction).toLowerCase();
    var items = window.__xboxFocusables();
    if (items.length === 0) { return; }

    var cur = (window.__xboxCurrent && document.body.contains(window.__xboxCurrent))
        ? window.__xboxCurrent
        : null;
    var curRect = cur ? cur.getBoundingClientRect() : { top: 0, left: 0, right: 0, bottom: 0 };
    var best = window.__xboxNearest(items.filter(function(el) { return el !== cur; }), curRect, direction);

    // No current selection yet (fresh page load) - just land on
    // whichever stop is closest to the top-left instead of doing
    // nothing until the first successful directional match.
    if (!best && !cur) { best = items[0]; }

    if (best) { window.__xboxSetFocus(best); }
};

window.__xboxCardSelect = function() {
    if (window.__xboxCurrent) { window.__xboxCurrent.click(); }
};

// Public entry points the Python side calls. These route to the
// on-screen keyboard's own navigate/press/close while it's open,
// and to normal card browsing otherwise, so nothing on the Python
// side needs to know or care which mode the page is currently in.
window.__xboxNavigate = function(direction) {
    if (window.__xboxKeyboardOpen) { window.__xboxKbNavigate(direction); return; }
    window.__xboxCardNavigate(direction);
};

window.__xboxSelect = function() {
    if (window.__xboxKeyboardOpen) { window.__xboxKbPress(); return; }
    if (window.__xboxCursorMode) { window.__xboxCursorClick(); return; }
    window.__xboxCardSelect();
};

window.__xboxBack = function() {
    if (window.__xboxKeyboardOpen) { window.__xboxCloseKeyboard(); return; }
    if (document.fullscreenElement) { document.exitFullscreen(); }
    else { history.back(); }
};

// Back-compat aliases, and the exact global names requested for the
// spatial-nav engine (window.xboxNavigate('DOWN') / window.xboxSelect()).
window.xboxNavigate = window.__xboxNavigate;
window.xboxSelect = window.__xboxSelect;
window.__xboxActivate = window.__xboxSelect;

// --- Free-moving cursor (right stick) ---
//
// The curated D-pad/stick navigation only ever lands on videos,
// channels, and sidebar entries - by design, so it doesn't wander
// through headers/chips/footer links. That means there's no way to
// reach everything else on the page (Subscribe, filter chips, "Show
// more", playlist buttons, etc.) through it. The right stick fills
// that gap with an ordinary free-moving cursor: point it at anything
// and press A to click it, exactly like a mouse. Touching the LEFT
// stick immediately cancels the cursor and hands control straight
// back to the normal card navigation - see __xboxCursorHide below,
// called from the Python side the moment the left stick moves.

window.__xboxCursorMode = false;
window.__xboxCursorX = 0;
window.__xboxCursorY = 0;

window.__xboxCursorBuild = function() {
    if (document.getElementById('__xbox-cursor')) { return; }
    var el = document.createElement('div');
    el.id = '__xbox-cursor';
    el.style.cssText = [
        'position:fixed', 'top:0', 'left:0', 'width:28px', 'height:28px',
        'margin:-14px 0 0 -14px', 'border-radius:50%',
        'background:rgba(255,0,0,0.35)', 'border:3px solid #FF0000',
        'box-shadow:0 0 8px rgba(0,0,0,0.6)', 'z-index:2147483647',
        // Critical: the cursor must never be the element under itself -
        // elementFromPoint has to see whatever is actually beneath it.
        'pointer-events:none', 'display:none'
    ].join(';');
    document.body.appendChild(el);
};

window.__xboxCursorRender = function() {
    var el = document.getElementById('__xbox-cursor');
    if (el) { el.style.transform = 'translate(' + window.__xboxCursorX + 'px,' + window.__xboxCursorY + 'px)'; }
};

window.__xboxCursorShow = function() {
    if (window.__xboxKeyboardOpen) { return; } // don't overlap the popup keyboard
    window.__xboxCursorBuild();
    if (window.__xboxCursorX === 0 && window.__xboxCursorY === 0) {
        window.__xboxCursorX = Math.round(window.innerWidth / 2);
        window.__xboxCursorY = Math.round(window.innerHeight / 2);
    }
    window.__xboxCursorMode = true;
    var el = document.getElementById('__xbox-cursor');
    if (el) { el.style.display = 'block'; }
    window.__xboxCursorRender();
};

window.__xboxCursorHide = function() {
    window.__xboxCursorMode = false;
    var el = document.getElementById('__xbox-cursor');
    if (el) { el.style.display = 'none'; }
};

window.__xboxCursorMoveBy = function(dx, dy) {
    if (!window.__xboxCursorMode) { return; }
    window.__xboxCursorX = Math.max(0, Math.min(window.innerWidth - 1, window.__xboxCursorX + dx));
    window.__xboxCursorY = Math.max(0, Math.min(window.innerHeight - 1, window.__xboxCursorY + dy));
    window.__xboxCursorRender();
};

window.__xboxCursorClick = function() {
    var el = document.getElementById('__xbox-cursor');
    if (el) { el.style.display = 'none'; } // don't click the cursor itself
    var target = document.elementFromPoint(window.__xboxCursorX, window.__xboxCursorY);
    if (el) { el.style.display = 'block'; }
    if (!target) { return; }

    var clickable = target.closest('a, button, [role="button"], [tabindex], input, select, textarea') || target;

    // Quick visual confirmation of what got clicked.
    clickable.classList.add('__xbox-focused');
    setTimeout(function() { clickable.classList.remove('__xbox-focused'); }, 150);

    clickable.click();
};


// --- On-screen popup keyboard (Y button / search) ---
//
// A gamepad has no keyboard attached to it, so pressing search opens a
// D-pad/stick-navigable virtual keyboard instead of trying to focus
// YouTube's own search box (which needs a real keyboard to type into).
// While it's open, __xboxNavigate/__xboxSelect/__xboxBack above route
// here instead of to card browsing - see those functions.

window.__xboxKeyboardOpen = false;
window.__xboxKbQuery = '';
window.__xboxKbCurrentKey = null;

window.__xboxKbLayoutRows = [
    ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
    ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
    ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', '⌫'],
    ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '⏎']
];
window.__xboxKbSpecialRow = [
    { label: 'SPACE', action: 'space', wide: true },
    { label: 'CLEAR', action: 'clear' },
    { label: 'CLOSE', action: 'close' }
];

window.__xboxKbActionFor = function(label) {
    if (label === '⌫') { return 'backspace'; }
    if (label === '⏎') { return 'search'; }
    return 'char';
};

window.__xboxMakeKeyEl = function(label, forcedAction, wide) {
    var key = document.createElement('div');
    key.className = '__xbox-kb-key';
    key.textContent = label;
    var action = forcedAction || window.__xboxKbActionFor(label);
    key.dataset.action = action;
    if (action === 'char') { key.dataset.char = label; }
    key.style.cssText = [
        'background:#3d3d3d', 'color:#fff', 'border-radius:6px',
        'min-width:' + (wide ? '220px' : '52px'), 'height:52px',
        'display:flex', 'align-items:center', 'justify-content:center',
        'font-size:18px', 'text-transform:uppercase', 'user-select:none',
        'font-family:Roboto,Arial,sans-serif'
    ].join(';');
    return key;
};

window.__xboxBuildKeyboard = function() {
    if (document.getElementById('__xbox-kb-overlay')) { return; }

    var overlay = document.createElement('div');
    overlay.id = '__xbox-kb-overlay';
    overlay.style.cssText = [
        'position:fixed', 'inset:0', 'z-index:2147483647',
        'background:rgba(0,0,0,0.75)', 'display:none',
        'align-items:flex-end', 'justify-content:center',
        'padding-bottom:40px'
    ].join(';');

    var panel = document.createElement('div');
    panel.style.cssText = [
        'background:#212121', 'border-radius:12px', 'padding:20px',
        'width:min(720px, 92vw)', 'box-shadow:0 10px 40px rgba(0,0,0,0.5)',
        'font-family:Roboto,Arial,sans-serif'
    ].join(';');

    var query = document.createElement('div');
    query.id = '__xbox-kb-query';
    query.style.cssText = [
        'background:#000', 'color:#fff', 'font-size:22px',
        'padding:12px 16px', 'border-radius:8px', 'margin-bottom:16px',
        'min-height:28px', 'white-space:pre-wrap', 'word-break:break-word'
    ].join(';');
    panel.appendChild(query);

    window.__xboxKbLayoutRows.forEach(function(row) {
        var rowEl = document.createElement('div');
        rowEl.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;justify-content:center;';
        row.forEach(function(label) {
            rowEl.appendChild(window.__xboxMakeKeyEl(label));
        });
        panel.appendChild(rowEl);
    });

    var specialRowEl = document.createElement('div');
    specialRowEl.style.cssText = 'display:flex;gap:8px;justify-content:center;';
    window.__xboxKbSpecialRow.forEach(function(spec) {
        specialRowEl.appendChild(window.__xboxMakeKeyEl(spec.label, spec.action, spec.wide));
    });
    panel.appendChild(specialRowEl);

    var hint = document.createElement('div');
    hint.style.cssText = 'color:#aaa;font-size:12px;text-align:center;margin-top:12px;';
    hint.textContent = 'D-Pad / Stick: Move    A: Select    B: Close    ⏎: Search    ⌫: Backspace';
    panel.appendChild(hint);

    overlay.appendChild(panel);
    document.body.appendChild(overlay);
};

window.__xboxKbRenderQuery = function() {
    var q = document.getElementById('__xbox-kb-query');
    if (q) { q.textContent = window.__xboxKbQuery || ' '; }
};

window.__xboxKbSetCurrent = function(el) {
    if (window.__xboxKbCurrentKey) {
        window.__xboxKbCurrentKey.classList.remove('__xbox-focused');
    }
    window.__xboxKbCurrentKey = el;
    el.classList.add('__xbox-focused');
};

window.__xboxKbNavigate = function(direction) {
    direction = String(direction).toLowerCase();
    var keys = Array.prototype.slice.call(document.querySelectorAll('.__xbox-kb-key'));
    if (keys.length === 0) { return; }

    var cur = (window.__xboxKbCurrentKey && document.body.contains(window.__xboxKbCurrentKey))
        ? window.__xboxKbCurrentKey
        : keys[0];
    var best = window.__xboxNearest(
        keys.filter(function(k) { return k !== cur; }),
        cur.getBoundingClientRect(),
        direction
    );
    window.__xboxKbSetCurrent(best || cur);
};

window.__xboxKbSyncRealInput = function() {
    var input = document.querySelector('input#search, input[name="search_query"], input[type="search"]');
    if (input) {
        input.value = window.__xboxKbQuery;
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
};

window.__xboxKbSubmit = function() {
    var q = (window.__xboxKbQuery || '').trim();
    window.__xboxCloseKeyboard();
    if (q) { window.location.href = '/results?search_query=' + encodeURIComponent(q); }
};

window.__xboxKbPress = function() {
    var key = window.__xboxKbCurrentKey;
    if (!key) { return; }
    var action = key.dataset.action;

    if (action === 'char') { window.__xboxKbQuery += key.dataset.char; }
    else if (action === 'space') { window.__xboxKbQuery += ' '; }
    else if (action === 'backspace') { window.__xboxKbQuery = window.__xboxKbQuery.slice(0, -1); }
    else if (action === 'clear') { window.__xboxKbQuery = ''; }
    else if (action === 'close') { window.__xboxCloseKeyboard(); return; }
    else if (action === 'search') { window.__xboxKbSubmit(); return; }

    window.__xboxKbRenderQuery();
    window.__xboxKbSyncRealInput();
};

window.__xboxOpenKeyboard = function() {
    if (window.__xboxKeyboardOpen) { window.__xboxCloseKeyboard(); return; }

    window.__xboxBuildKeyboard();
    window.__xboxKbQuery = '';
    window.__xboxKbRenderQuery();
    window.__xboxKeyboardOpen = true;

    var overlay = document.getElementById('__xbox-kb-overlay');
    if (overlay) { overlay.style.display = 'flex'; }

    var firstKey = document.querySelector('.__xbox-kb-key');
    if (firstKey) { window.__xboxKbSetCurrent(firstKey); }
};

window.__xboxCloseKeyboard = function() {
    window.__xboxKeyboardOpen = false;
    if (window.__xboxKbCurrentKey) {
        window.__xboxKbCurrentKey.classList.remove('__xbox-focused');
        window.__xboxKbCurrentKey = null;
    }
    var overlay = document.getElementById('__xbox-kb-overlay');
    if (overlay) { overlay.style.display = 'none'; }
};

// Kept for the ctrl+k desktop-keyboard shortcut and any old references -
// now just opens the popup keyboard rather than focusing YouTube's own
// search box (which was the old behaviour before this existed).
window.__xboxSearch = function() {
    window.__xboxOpenKeyboard();
};

window.__xboxTop = function() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
};

window.__xboxIsTyping = function() {
    if (window.__xboxKeyboardOpen) { return true; }
    var el = document.activeElement;
    return !!(el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'));
};

// --- On-screen legend showing what each button currently does ---

window.__xboxHudVisible = true;

window.__xboxBuildHud = function(items) {
    var existing = document.getElementById('__xbox-hud');
    if (existing) { existing.remove(); }

    var bar = document.createElement('div');
    bar.id = '__xbox-hud';
    bar.style.cssText = [
        'position:fixed', 'left:0', 'right:0', 'bottom:0',
        'z-index:2147483647', 'display:flex', 'flex-wrap:wrap',
        'justify-content:center', 'gap:10px', 'padding:6px 10px',
        'background:rgba(0,0,0,0.65)', 'pointer-events:none',
        'font-family:Roboto,Arial,sans-serif', 'font-size:13px',
        'color:#fff', 'transition:opacity 0.2s ease'
    ].join(';');

    items.forEach(function(item) {
        var chip = document.createElement('span');
        chip.style.cssText = 'display:flex;align-items:center;gap:5px;white-space:nowrap;';

        var badge = document.createElement('span');
        badge.textContent = item.label;
        badge.style.cssText = [
            'background:' + item.color, 'color:#fff', 'border-radius:10px',
            'padding:2px 8px', 'font-weight:bold', 'font-size:11px'
        ].join(';');

        var text = document.createElement('span');
        text.textContent = item.text;

        chip.appendChild(badge);
        chip.appendChild(text);
        bar.appendChild(chip);
    });

    document.body.appendChild(bar);
    bar.style.opacity = window.__xboxHudVisible ? '1' : '0';
};

window.__xboxToggleHelp = function() {
    window.__xboxHudVisible = !window.__xboxHudVisible;
    var bar = document.getElementById('__xbox-hud');
    if (bar) { bar.style.opacity = window.__xboxHudVisible ? '1' : '0'; }
};

// Fullscreen video hides anything outside the fullscreen element, so
// move the legend into it while active and back out when it exits.
document.addEventListener('fullscreenchange', function() {
    var bar = document.getElementById('__xbox-hud');
    if (!bar) { return; }
    var target = document.fullscreenElement || document.body;
    target.appendChild(bar);
});

window.__xboxInjectStyle();
"""


# --------------------------------------------------------------------
# Step 4: Xbox controller -> action bridge.
# --------------------------------------------------------------------

def _guarded_key(window, key):
    """Sends a real keypress (for native YouTube shortcuts like
    play/pause and seek) unless a text field currently has focus, so
    the controller doesn't clobber whatever you're typing."""
    try:
        typing = window.evaluate_js("window.__xboxIsTyping()")
    except Exception:
        typing = False
    if not typing:
        pyautogui.press(key)


def _quit_app(window):
    """Close the window and make sure the whole process goes away (the controller thread blocks on reads)."""
    try:
        window.destroy()
    except Exception as exc:
        print(f"[quit] destroy failed: {exc}")
    t = threading.Timer(2.0, lambda: os._exit(0))   # safety net if something keeps the process alive
    t.daemon = True
    t.start()


class JsApi:
    """Lets the page's on-screen Close chip call back into Python."""

    def __init__(self):
        self._window = None      # leading underscore: pywebview skips private attributes (a public .window
                                 # makes it walk window.native... forever -> "maximum recursion depth exceeded")

    def quit(self):
        _quit_app(self._window)


ACTIONS = {
    "quit": lambda window: _quit_app(window),
    "activate": lambda window: window.evaluate_js("window.__xboxSelect()"),
    "back": lambda window: window.evaluate_js("window.__xboxBack()"),
    "search": lambda window: window.evaluate_js("window.__xboxOpenKeyboard()"),
    "top": lambda window: window.evaluate_js("window.__xboxTop()"),
    "playpause": lambda window: _guarded_key(window, "k"),
    "seek_back": lambda window: _guarded_key(window, "j"),
    "seek_forward": lambda window: _guarded_key(window, "l"),
    "fullscreen": lambda window: _guarded_key(window, "f"),
    "toggle_help": lambda window: window.evaluate_js("window.__xboxToggleHelp()"),
}

DPAD_Y_MAP = {-1: "up", 1: "down"}
DPAD_X_MAP = {-1: "left", 1: "right"}


class GamepadListener:
    """Runs on its own background thread; call .run() once the app
    window has been created and the page has loaded.

    The left stick always does spatial navigation - unless the free
    cursor (right stick) is active, in which case any left-stick
    movement immediately cancels the cursor and falls straight
    through to normal navigation the same event. The D-pad is
    separate and triggers named actions (fullscreen, seek, etc.) from
    dpad_action_map instead, except while the popup keyboard is open,
    when it drives that instead."""

    def __init__(self, window, stop_event, config, debug=False):
        self.window = window
        self.stop_event = stop_event
        self.button_action_map = config["button_action_map"]
        self.dpad_action_map = config["dpad_action_map"]
        self.stick_deadzone = config["stick_deadzone"]
        self.repeat_delay = config["repeat_delay"]
        self.debug = debug
        self._last_stick_move = 0.0
        self._last_debug_counts = None
        self._held = {}      # button code -> time it went down (for the X + B close chord)

        # Free-cursor (right stick) state. cursor_mode and the raw
        # right-stick axis values are read by CursorMotionThread on a
        # separate thread to drive smooth continuous movement, since
        # gamepad axis events don't reliably keep arriving at a fixed
        # rate while a stick is just held steady.
        self.cursor_mode = False
        self.cursor_deadzone = config["cursor_deadzone"]
        self.cursor_exit_threshold = config["cursor_exit_threshold"]
        self.right_stick_x = 0
        self.right_stick_y = 0

        # The stick's own up/down/left/right sense is independent of
        # the D-pad's - on some controllers/drivers the analog stick's
        # Y axis is flipped relative to the D-pad's hat switch, so this
        # is configurable separately rather than sharing DPAD_Y_MAP.
        self.stick_y_map = (
            {-1: "down", 1: "up"} if config.get("invert_stick_y")
            else {-1: "up", 1: "down"}
        )
        self.stick_x_map = (
            {-1: "right", 1: "left"} if config.get("invert_stick_x")
            else {-1: "left", 1: "right"}
        )

    def run(self):
        print("[gamepad] Waiting for an Xbox controller (USB or Bluetooth)...")
        while not self.stop_event.is_set():
            try:
                events = get_gamepad()
            except UnpluggedError:
                time.sleep(1.0)
                continue
            except Exception as exc:  # keep the thread alive no matter what
                print(f"[gamepad] read error: {exc}")
                time.sleep(1.0)
                continue

            for event in events:
                # A single bad/unexpected event (e.g. a JS call that
                # fails because the page hasn't finished loading yet)
                # must never kill this loop - that would silently stop
                # the controller from doing anything at all for the
                # rest of the session, with no obvious symptom.
                try:
                    self._handle(event)
                except Exception as exc:
                    print(f"[gamepad] error handling {event.code}={event.state}: {exc}")

    def _handle(self, event):
        code, state = event.code, event.state

        if self.debug and (state != 0 or code.startswith("ABS_HAT")):
            print(f"[gamepad-debug] {code} = {state}")

        # X + B held together = close the app (so a launcher can get control back)
        if code in ("BTN_WEST", "BTN_EAST"):
            now = time.time()
            if state == 1:
                self._held[code] = now
                for k in [k for k, t in self._held.items() if now - t > 5.0]:    # forget stale presses
                    del self._held[k]
                if "BTN_WEST" in self._held and "BTN_EAST" in self._held:
                    print("[gamepad] X + B -> closing")
                    _quit_app(self.window)
                    return
            else:
                self._held.pop(code, None)

        if code in self.button_action_map and state == 1:
            self._run_action(self.button_action_map[code], code)
            return

        if code == "ABS_HAT0Y" and state in DPAD_Y_MAP:
            self._run_dpad(DPAD_Y_MAP[state])
        elif code == "ABS_HAT0X" and state in DPAD_X_MAP:
            self._run_dpad(DPAD_X_MAP[state])
        elif code == "ABS_Y":
            self._maybe_exit_cursor_mode(state)
            self._throttled_nav(self._axis_direction(state, self.stick_y_map))
        elif code == "ABS_X":
            self._maybe_exit_cursor_mode(state)
            self._throttled_nav(self._axis_direction(state, self.stick_x_map))
        elif code == "ABS_RX":
            self.right_stick_x = state
            self._maybe_enter_cursor_mode(state)
        elif code == "ABS_RY":
            self.right_stick_y = state
            self._maybe_enter_cursor_mode(state)

    def _run_action(self, action, source):
        fn = ACTIONS.get(action)
        if fn is None:
            print(f"[gamepad] unknown action '{action}' for {source}")
        else:
            fn(self.window)

    def _run_dpad(self, direction):
        # While the popup keyboard is open, the D-pad drives it too
        # (moving between keys), rather than triggering its usual fixed
        # actions - fullscreen/seek would be surprising to hit while
        # typing a search.
        if self._keyboard_open():
            self._navigate(direction)
            return
        action = self.dpad_action_map.get(direction)
        if action:
            self._run_action(action, f"D-pad {direction}")

    def _keyboard_open(self):
        try:
            return bool(self.window.evaluate_js("window.__xboxKeyboardOpen"))
        except Exception:
            return False

    def _maybe_enter_cursor_mode(self, state):
        if self.cursor_mode or abs(state) <= self.cursor_deadzone:
            return
        if self._keyboard_open():
            return  # the popup keyboard already owns both sticks
        self.cursor_mode = True
        try:
            self.window.evaluate_js("window.__xboxCursorShow()")
        except Exception as exc:
            print(f"[cursor] {exc}")

    def _maybe_exit_cursor_mode(self, state):
        # Any real nudge of the LEFT stick hands control straight back
        # to normal navigation - a much lower threshold than the stick
        # deadzone used for actual directional moves, since the point
        # here is to catch "the person touched this stick at all", not
        # "the person pushed it far enough to navigate".
        if not self.cursor_mode or abs(state) <= self.cursor_exit_threshold:
            return
        self.cursor_mode = False
        try:
            self.window.evaluate_js("window.__xboxCursorHide()")
        except Exception as exc:
            print(f"[cursor] {exc}")

    def _axis_direction(self, state, axis_map):
        if state < -self.stick_deadzone:
            return axis_map[-1]
        if state > self.stick_deadzone:
            return axis_map[1]
        return None

    def _navigate(self, direction):
        try:
            self.window.evaluate_js(f"window.__xboxNavigate('{direction}')")
        except Exception as exc:
            print(f"[nav] {exc}")
        if self.debug:
            self._log_debug_counts()

    def _log_debug_counts(self):
        try:
            counts = self.window.evaluate_js("window.__xboxDebugCounts()")
        except Exception as exc:
            print(f"[nav-debug] couldn't read candidate counts: {exc}")
            return
        if counts != self._last_debug_counts:
            self._last_debug_counts = counts
            print(f"[nav-debug] candidates on screen: {counts}")

    def _throttled_nav(self, direction):
        if direction is None:
            return
        now = time.time()
        if now - self._last_stick_move > self.repeat_delay:
            self._last_stick_move = now
            self._navigate(direction)


class CursorMotionThread(threading.Thread):
    """Continuously moves the on-screen cursor while GamepadListener's
    cursor_mode is active, at a fixed tick rate independent of how
    often ABS_RX/ABS_RY events actually arrive - a stick held at a
    steady deflection doesn't reliably keep generating new axis events
    on every controller/driver, but a mouse-like cursor still needs to
    look and feel like it's moving smoothly the whole time it's held."""

    def __init__(self, window, listener, config):
        super().__init__(daemon=True)
        self.window = window
        self.listener = listener
        self.tick_interval = 1 / 60.0
        self.max_speed = config["cursor_max_speed"]  # pixels/sec at full deflection
        self.deadzone = config["cursor_deadzone"]
        self.invert_x = config.get("invert_cursor_x", False)
        self.invert_y = config.get("invert_cursor_y", True)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        last = time.time()
        while not self._stop_event.is_set():
            time.sleep(self.tick_interval)
            now = time.time()
            dt = now - last
            last = now

            if not self.listener.cursor_mode:
                continue

            dx = self._scaled(self.listener.right_stick_x, self.invert_x) * self.max_speed * dt
            dy = self._scaled(self.listener.right_stick_y, self.invert_y) * self.max_speed * dt
            if dx == 0 and dy == 0:
                continue

            try:
                self.window.evaluate_js(f"window.__xboxCursorMoveBy({dx:.2f}, {dy:.2f})")
            except Exception as exc:
                print(f"[cursor] {exc}")

    def _scaled(self, raw, invert):
        if abs(raw) <= self.deadzone:
            return 0.0
        span = 32767 - self.deadzone
        magnitude = min((abs(raw) - self.deadzone) / span, 1.0) ** 1.5  # ease in near center
        signed = magnitude if raw > 0 else -magnitude
        return -signed if invert else signed


# --------------------------------------------------------------------
# Step 5: the window itself.
# --------------------------------------------------------------------

YOUTUBE_URL = "https://www.youtube.com/"

# A clickable "Close" button appended to the legend bar (the bar itself ignores the mouse).
CLOSE_CHIP_JS = r"""
(function () {
    var bar = document.getElementById('__xbox-hud');
    if (!bar || document.getElementById('__xbox-close')) { return; }
    var b = document.createElement('button');
    b.id = '__xbox-close';
    b.textContent = '\u2715 Close';
    b.style.cssText = 'pointer-events:auto;cursor:pointer;background:#C4302B;color:#fff;border:0;' +
                      'border-radius:10px;padding:2px 12px;font-weight:bold;font-size:12px;';
    b.onclick = function () { try { window.pywebview.api.quit(); } catch (e) {} };
    bar.appendChild(b);
})();
"""


def _start_search_hotkey(window):
    """Optional global Ctrl+K shortcut that force-focuses YouTube's
    search box - a fallback for when the Y button/'/' shortcut can't
    reach it because focus has landed somewhere unusual."""
    try:
        import keyboard
    except Exception:
        print(
            "[hotkey] `keyboard` package not available - skipping the "
            "global Ctrl+K search shortcut. The Y button already works "
            "while the window is focused."
        )
        return

    try:
        keyboard.add_hotkey("ctrl+k", lambda: window.evaluate_js("window.__xboxSearch()"))
        print("[hotkey] Ctrl+K -> focus YouTube search box")
    except Exception as exc:
        print(f"[hotkey] could not register global hotkey: {exc}")


def main():
    parser = argparse.ArgumentParser(description="YouTube, Xbox-controller style")
    parser.add_argument(
        "--fullscreen", action="store_true",
        help="Launch in fullscreen (this is now the default - kept for "
             "compatibility/scripts)",
    )
    parser.add_argument(
        "--windowed", action="store_true",
        help="Launch in a regular window instead of fullscreen",
    )
    parser.add_argument(
        "--debug-gamepad",
        action="store_true",
        help="Print every raw controller event (button codes/values) to "
             "help diagnose a controller that isn't responding.",
    )
    args = parser.parse_args()

    config = load_config()
    if args.fullscreen:
        config["fullscreen"] = True
    if args.windowed:
        config["fullscreen"] = False

    os.makedirs(STORAGE_PATH, exist_ok=True)

    api = JsApi()
    window = webview.create_window(
        title="YouTube (Xbox Mode)",
        url=YOUTUBE_URL,
        width=1280,
        height=720,
        resizable=True,
        fullscreen=config["fullscreen"],
        js_api=api,
    )
    api._window = window

    def inject_helpers():
        window.evaluate_js(INJECTED_JS)
        hud_items = build_hud_items(config["button_action_map"], config["dpad_action_map"])
        window.evaluate_js(f"window.__xboxBuildHud({json.dumps(hud_items)})")
        window.evaluate_js(CLOSE_CHIP_JS)
        print("[nav] Spatial navigation ready. On-screen legend shown "
              "(View button to hide/show).")

    window.events.loaded += inject_helpers

    stop_event = threading.Event()

    def on_started():
        _start_search_hotkey(window)
        listener = GamepadListener(window, stop_event, config, debug=args.debug_gamepad)
        cursor_thread = CursorMotionThread(window, listener, config)
        cursor_thread.start()
        try:
            listener.run()
        finally:
            cursor_thread.stop()

    try:
        webview.start(
            on_started,
            gui=None,
            debug=False,
            storage_path=STORAGE_PATH,
            private_mode=False,  # keep cookies/login between runs
        )
    except TypeError:
        # Older pywebview versions don't have storage_path/private_mode -
        # fall back to starting without persistent login rather than
        # crashing outright.
        print(
            "[setup] Your pywebview version doesn't support persistent "
            "login (storage_path/private_mode) - starting without it. "
            "Run `pip install -U pywebview` to get that feature."
        )
        webview.start(on_started, gui=None, debug=False)

    stop_event.set()
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)     # the controller thread may still be blocked reading; don't wait for it


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        # If we're in a real interactive console (not a redirected log
        # file, as in main.pyw), pause so the window doesn't vanish
        # before you can read the error.
        if getattr(sys.stdout, "isatty", lambda: False)():
            try:
                input("\nSomething went wrong (see above). Press Enter to exit...")
            except Exception:
                pass
        raise
