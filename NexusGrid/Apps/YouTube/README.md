> **Nexus Grid:** this app lives in `NexusGrid/Apps/YouTube` and is started from Big Picture's Apps shelf.
> Press **X + B** together on the controller (or click **Close** in the bottom bar) to close it.
> Copy `config.example.json` to `config.json` to change its settings.

# YouTube (Xbox Mode)

A small desktop app that puts regular YouTube in a window and lets you
drive it with an Xbox controller instead of a mouse. No `pygame`
anywhere - the window is a native OS webview, and the controller support
is a pure-Python library (`inputs`) that just reads raw HID events.

## Why not `youtube.com/tv`?

An earlier version of this pointed at `youtube.com/tv`, Google's old
"10-foot" remote-control UI. In practice, Google now reserves that page
for devices that have gone through its official TV/console pairing flow
(the same process a real smart TV or console uses) - a plain desktop
webview isn't one of those, so the page just bounces back to the normal
site. Trying to spoof that pairing isn't something a webview can do
reliably.

Instead, this app drives regular `youtube.com` directly, using its own
built-in keyboard shortcuts for playback (`k` for play/pause, `j`/`l` to
seek) and a small injected script for navigation (see below). No
redirect to fight.

## Real spatial navigation

Rather than just stepping through the page's Tab order, the D-pad/stick
moves to whichever clickable element is actually nearest in the
direction you pressed - based on where things are drawn on screen, the
same idea a real TV/console UI uses. It's implemented as a small
JavaScript helper injected into the page once it loads; see
`INJECTED_JS` in `main.py` if you're curious how it scores candidates.

It's not going to be pixel-perfect on every layout YouTube ships (their
DOM changes over time), but it moves in the direction you'd expect
between thumbnails, buttons, and links, which plain Tab order can't do.

## About login & YouTube Premium

This app doesn't touch ads, DRM, or YouTube's backend in any way - it's
a real embedded browser session pointed at the real youtube.com. Log in
with your own Google account the first time you launch it, the same way
you would in Chrome or Edge. If your account has YouTube Premium, you'll
get the same ad-free playback here that you'd get anywhere else, because
it *is* the same account and the same site.

The window keeps its own profile folder (`.webview_data`, created next
to `main.py` the first time you run it) instead of a throwaway one, so
your sign-in carries over between launches - you shouldn't need to log
in again every time you start the app. Delete that folder if you ever
want to sign out / start fresh.

## Setup

Everything - the dependency check/install and the app itself - lives in
one file. Just run it:

```bash
python main.py
```

It launches straight into fullscreen by default, on whichever monitor
the window opens on. Pass `--windowed` if you'd rather have a regular
resizable window instead:

```bash
python main.py --windowed
```

The first run installs whatever's missing (`pywebview`, `inputs`,
`pyautogui`, `keyboard`) and prints its progress; every run after that
just starts straight up, since there's nothing left to install.

Other ways to launch it:
- **`main.pyw`** (Windows only) - identical to `main.py`, but double-click
  it to run with no console window at all. Since a console-less process
  has nowhere to print to, its messages go to `app.log` next to it
  instead - check there if a controller isn't being picked up, an
  install fails silently, or navigation doesn't seem to be injecting.
- **`run.bat`** - double-click on Windows if you don't want to open a
  terminal (shows the console, same as `python main.py`).
- **`run.sh`** - `./run.sh` does the same on Linux/macOS.

Notes:
- **Windows 10/11**: pywebview uses the built-in Edge WebView2 runtime,
  which almost every current Windows install already has. If it's
  missing, Windows will prompt you to install it (or grab it from
  Microsoft's site) the first time you run the app.
- **Xbox controller**: plug it in via USB, or pair it over Bluetooth,
  before or after launching - the app polls for it continuously.
- **Global Ctrl+K hotkey**: the `keyboard` package's system-wide hotkeys
  need elevated permissions on Linux/macOS (run with `sudo`) and
  sometimes on Windows too. If it can't register the hotkey it just
  prints a message and skips it - the Y button and typing directly into
  the on-page search box both still work with no extra permissions.

## On-screen legend

While the app runs, a translucent bar along the bottom of the page shows
every button and what it currently does - built from your actual
mapping (including any `config.json` overrides), so it never goes out
of sync with what's really bound. Press **View** (the small button
left of the Xbox logo, next to Menu/Start) to hide or show it. It
follows the page into fullscreen video too, so it stays visible there
as well.

## Controls

| Input                 | Action                                       |
|-----------------------|-----------------------------------------------|
| Left stick             | Move to the nearest element in that direction |
| D-pad up               | Toggle fullscreen                             |
| D-pad left / right     | Seek back / forward 10s                       |
| A                      | Activate the focused element                  |
| B                      | Back (or exit fullscreen, if a video is playing full-screen) |
| X                      | Play / pause                                  |
| Y                      | Jump to search                                |
| LB / RB                | Seek back / forward 10s                       |
| Start                  | Scroll to top of page                         |
| View                   | Hide / show the on-screen legend              |
| Keyboard (anywhere)    | Type to search once the search box is focused |
| Ctrl+K                 | Force-focus the search box                    |

The stick always does navigation; the D-pad is separate and only does
the three things listed above (D-pad down is unbound by default - see
below to give it a job).

Once a video is loaded, YouTube also responds directly to several other
keys you can still use from a real keyboard: arrow up/down for volume,
arrow left/right for a 5s seek, `f` for fullscreen, `m` to mute, `c` for
captions, and `0`-`9` to jump to that tenth of the video. Play/pause and
seek are skipped automatically if you're currently typing into a text
box, so the controller won't clobber a search you're mid-typing.

## Customizing the controls

Copy `config.example.json` to `config.json` (same folder as `main.py`)
and edit it - no code changes needed. It's loaded automatically if
present:

```json
{
  "fullscreen": true,
  "stick_deadzone": 12000,
  "repeat_delay": 0.18,
  "invert_stick_y": true,
  "invert_stick_x": false,
  "button_action_map": {
    "BTN_SOUTH": "activate",
    "BTN_EAST": "back",
    "BTN_NORTH": "search",
    "BTN_WEST": "playpause",
    "BTN_TL": "seek_back",
    "BTN_TR": "seek_forward",
    "BTN_START": "top",
    "BTN_SELECT": "toggle_help"
  },
  "dpad_action_map": {
    "up": "fullscreen",
    "left": "seek_back",
    "right": "seek_forward"
  }
}
```

`invert_stick_y`/`invert_stick_x` flip the left stick's sense if pushing
it a direction moves the opposite way on your particular controller -
this is independent of the D-pad, which isn't affected by these
settings. `button_action_map` covers the face/shoulder/menu buttons;
`dpad_action_map` covers the D-pad's four directions separately (`up`,
`down`, `left`, `right` - only include the ones you want bound; `down`
is free by default). Each maps to one of the named actions defined in
`ACTIONS` in `main.py`: `activate`, `back`, `search`, `playpause`,
`seek_back`, `seek_forward`, `top`, `fullscreen`, `toggle_help`. You
only need to include the settings you want to change - anything you
leave out keeps its default. The on-screen legend rebuilds itself from
whatever mapping is active, so a remap shows up there automatically too.

If you edit `main.py` directly instead (e.g. to add a brand-new action),
copy the same change into `main.pyw`, since it's otherwise identical.

## How it works, briefly

- Checks whether `pywebview`, `inputs`, `pyautogui`, and `keyboard` are
  importable, and `pip install`s anything missing.
- Opens a native window (via `pywebview`) loading `https://www.youtube.com/`,
  with a persistent profile folder so login carries over between runs.
- Once the page loads, injects a small JavaScript helper
  (`INJECTED_JS`) that adds spatial navigation, a reliable "activate"
  (`element.click()`), "back" (`history.back()` / exit fullscreen), and
  search-focus, plus a helper to tell whether you're currently typing.
- Runs a background thread that reads raw controller events with
  `inputs` and calls into that JS helper for navigation/activation, or
  sends real OS-level key presses (via `pyautogui`) for the couple of
  actions that map directly onto YouTube's native keyboard shortcuts
  (play/pause, seek).
- A global Ctrl+K hotkey (via the `keyboard` package) is a fallback way
  to focus search from anywhere, in case focus has landed somewhere
  unexpected.

## Troubleshooting: controller stops responding

Previously, a single unexpected controller event (e.g. a button press
that fired before the page finished loading) could silently kill the
controller-reading thread for the rest of the session - the window
would stay open and usable with a mouse/keyboard, but the controller
would just go quiet with no error visible anywhere. That's now caught
and logged instead of crashing the thread, so this specific failure
mode shouldn't happen anymore.

If the controller still isn't doing anything:

1. Click into the app window once - the controller drives whichever
   window has OS focus for its keyboard-shortcut actions (play/pause,
   seek, fullscreen).
2. Run `python main.py --debug-gamepad` from a terminal. This prints
   every raw event your controller sends (its code and value) as you
   press things. If you press A and see nothing printed at all, the
   controller isn't being read at the OS level (driver/permissions
   issue, unrelated to this app). If you see events printed but they
   don't match the codes this app expects (`BTN_SOUTH`, `ABS_HAT0X`,
   etc.), your controller/driver is naming things differently - copy
   whatever codes you do see into `config.json`'s `button_action_map`
   or `dpad_action_map`.
3. Check the console (or `app.log` for `main.pyw`) for `[gamepad]`
   error lines, which now describe what specifically went wrong instead
   of failing silently.

## Troubleshooting: nothing opens

The app now checks that installed packages are new enough, not just
that they're installed (an old cached `pywebview` from before can
satisfy "it's installed" but still be missing features this app needs,
which used to fail silently). If nothing still opens:

1. Run it from a terminal with `python main.py` (not `main.pyw`) so you
   can actually see what happened - a crash now prints its full error
   and waits for you to press Enter before the window closes.
2. If you did run `main.pyw`, check `app.log` next to it for the same
   error.
3. If it mentions a `pip install` failure, try running the command it
   prints manually, or `pip install --upgrade pywebview inputs pyautogui
   keyboard`.
4. On Windows, make sure the Edge WebView2 runtime is installed (it
   almost always already is on 10/11) - pywebview needs it.

## Known limitations

- The window needs focus for keyboard-shortcut actions (play/pause,
  seek) to reach it, so click into it once before using the controller.
  Navigation/activate/back go through JavaScript instead, so those work
  even without window focus being exactly right.
- YouTube periodically changes its DOM, so the search-box selector and
  the "focusable elements" selector in `INJECTED_JS` may occasionally
  need a tweak if Google renames something.
- `main.pyw`'s log goes to `app.log` instead of a console, since a
  console-less process has no stdout to print to.
