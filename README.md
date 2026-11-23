
NEXUS GRID
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
