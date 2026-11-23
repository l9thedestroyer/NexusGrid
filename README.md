# Nexus Grid

**Nexus Grid** is an all-in-one, local-first PC game launcher built in Python with **PySide6**, designed for smooth desktop library management and an Xbox-inspired **Big Picture** couch experience.

---

## Key Features

### Unified Game Library & Smart Scanning
* **Multi-Store Auto-Detection:** Automatically scans local installations for **Steam**, **Epic Games**, **GOG**, and **Ubisoft Connect**.
* **Batch Folder & Manual `.exe` Importer:** Point Nexus Grid at one or multiple parent folders (`+ Add folder`). It automatically filters out installers, uninstallers, crash handlers, redistributables, and engine runtimes (e.g., Ren'Py `lib`/`renpy` or Unreal `Engine` subfolders) to select the real game executable.
* **Custom Folders / Collections:** Organize games into custom folders (such as grouping by creator, genre, or status) that you can click into just like Steam collections.
* **Playtime Tracking:**
  * Reads existing playtime and last-played timestamps directly from Steam's local `localconfig.vdf`.
  * Tracks playtime automatically in the background for Epic, GOG, Ubisoft, and manually added `.exe` games by monitoring active processes inside each game's install directory.

### Artwork, Views & Customization
* **Automatic Cover Art & Icon Fallbacks:**
  * Pulls Steam's local artwork cache automatically.
  * Fetches real poster art for non-Steam games via **SteamGridDB** (optional free API key) with Steam store fallback and intelligent title cleaning.
  * Generates purple/blue gradient cards from each game's `.exe` icon when no poster is available, and supports custom user-assigned cover images.
* **Flexible Views & Auto-Scaling Grid:**
  * Switch between a **Grid View** (where tiles automatically scale to fill window width cleanly with no right-hand gaps) and a **List View**.
  * Filter by store, favorites, or custom folders; sort alphabetically, by recently played, or by most played; and search instantly.
* **Purple Aesthetic & Custom Branding:** Features a deep purple theme with customizable accent colors, embedded Nexus Grid branding, and a lightweight startup splash screen.

### Xbox-Style Big Picture Mode & Integrated Apps
* **Console-Style Home Screen:** Launch into **Big Picture Mode** with a smooth transition animation. Features a clean, ad-free Xbox-inspired layout with a featured/recent tile row, quick-access shortcuts, a full **Games** grid, a dedicated **Apps** section, and a quick side panel (`Right Stick Click`).
* **Smart Resource & Tray Management:**
  * In **Desktop Mode**, launching a game hides Nexus Grid to the system tray and automatically restores it once the game exits.
  * In **Big Picture Mode**, Nexus Grid stays open in the background on minimal resources while a game runs so you return immediately upon exiting.
* **Integrated Desktop YouTube App:** Launches a dedicated Python + `pywebview` (Edge WebView2) YouTube experience (`NexusGrid/Apps/YouTube`) supporting standard `youtube.com`, full Google account sign-in, fullscreen playback, and Xbox controller navigation.

### Native XInput Controller & Keyboard Navigation
* **Zero-Config XInput Support:** Uses Windows' built-in XInput via `ctypes`—no extra controller libraries required.
* **Smart Hint Visibility:** Controller button prompts stay hidden until an actual physical controller input is detected (preventing virtual/phantom controllers from cluttering the UI).
* **Controller-Support Pre-Launch Check:** Before launching a game with a controller (`A`), Nexus Grid checks whether the title has controller support and warns you first if it doesn't (with a *"Don't ask again for this game"* option).

### Profile & Roadmap
* **User Profile:** Includes a customizable profile with bio, gaming stats, recent activity, badges & titles, showcase, goals, and a Year in Review.
* **Built-In Roadmap:** Tracks upcoming features and ideas alongside their development status.

---

## Folder Structure

Nexus Grid keeps its configuration, artwork cache, apps, and documentation organized inside your `Documents` directory:

```text
Documents/
└── NexusGrid/
    ├── Apps/
    │   └── YouTube/        # Integrated controller-friendly YouTube app
    ├── Config/             # Settings, library data, and cached thumbnails
    └── Docs/               # Documentation and guides
```

*(Note: Legacy library data from `%APPDATA%/GameLauncher` is preserved/migrated seamlessly.)*

---

## Getting Started

### Requirements
* **OS:** Windows 10 / 11
* **Python:** **Python 3.13.16** (recommended; the launcher startup script can prompt before downloading and installing the exact `3.13.16` release)
* **Dependencies:** `PySide6`, `psutil` (automatically installed on first run if missing)

### Running Nexus Grid
1. Clone or download this repository.
2. Run the launcher via the provided start script or directly with Python:
   ```bash
   python game_launcher.py
   ```
   *(When launched with `python.exe`, Nexus Grid automatically relaunches itself under `pythonw.exe` so no background terminal window stays open.)*

---

## Controls

### Controller (Xbox / XInput)
| Input | Action |
| :--- | :--- |
| **D-Pad / Left Stick** | Navigate grid, menus, and dialogs |
| **Right Stick** | Smooth scroll |
| **Right Stick Click (R3)** | Toggle Big Picture side panel |
| **A** | Launch selected game / Confirm |
| **B** | Clear search / Cancel / Back |
| **X** | Toggle Favorite |
| **Y** | Cycle Sort order |
| **LB / RB** | Switch filter tabs |
| **LT / RT** | Page Up / Page Down |
| **Start** | Open game options menu |
| **Back / Select** | Toggle Fullscreen |

### Keyboard Shortcuts
| Key | Action |
| :--- | :--- |
| **Arrow Keys** | Navigate games |
| **Enter** | Launch selected game |
| **Type anywhere** | Jump straight into search |
| **Ctrl + F** | Focus search bar |
| **Ctrl + D** | Toggle Favorite |
| **Shift + F10 / Menu** | Open context menu |
| **F11** | Toggle borderless fullscreen |
| **Esc** | Clear search |

---

## Settings & Configuration
* **Startup & Tray Behavior:** Configure whether closing the window quits the app or minimizes to the system tray, and toggle **Start with Windows**.
* **SteamGridDB Integration:** Open **Covers -> Get a free API key** to generate a SteamGridDB key and enable automatic poster downloads for non-Steam and indie games.
* **Appearance:** Adjust default tile minimum sizes and customize the UI accent color.

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPLv3)**.

You are free to use, study, modify, and distribute this software, provided that any distributed modifications or derivative works are also made available under the **GPLv3** license with their full source code disclosed.
