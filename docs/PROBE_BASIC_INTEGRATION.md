# How `PB_Conversational` hooks into Probe Basic and LinuxCNC

One rule I set for myself when building this was **never to edit or overwrite any file inside the installed `probe_basic` package**. If you hack `probe_basic.ui` or its Python files directly, the next `apt upgrade` or `git pull` on Probe Basic wipes out your work (or leaves you maintaining a fork forever).

Instead, `PB_Conversational` uses Probe Basic's standard `USER_TABS_PATH` mechanism and docks its screens into the main window at runtime once Qt finishes building the UI.

## `USER_TABS_PATH` and two Probe Basic quirks to watch out for

To load user tabs, Probe Basic looks for this line under `[DISPLAY]` in your machine `.ini`:

```ini
USER_TABS_PATH = user_tabs/
```

On startup (`load_user_tabs`), it scans every subdirectory inside `user_tabs/` and imports `<folder>/<folder>.py`, expecting a `UserTab(QWidget)` class.

If you configure this manually rather than using the installer, watch out for two classic traps in Probe Basic / QtPyVCP:
1. **The trailing slash (`user_tabs/`) is required.** Probe Basic passes `USER_TABS_PATH` through `os.path.dirname()`. Without the trailing `/`, `os.path.dirname("user_tabs")` returns `""` and it looks in your config root instead of `user_tabs/`.
2. **Never put an inline `#` comment on that line.** Some template `.ini` files ship with `#USER_TABS_PATH = user_tabs/ # PATH TO USER TABS...`. If you uncomment the start of that line and leave `# PATH TO...` at the end, LinuxCNC's INI parser includes the comment text as part of the path string (QtPyVCP issue #154) and startup crashes. Put comments on their own line above.

The automatic installer checks for and fixes both of these automatically.

## Runtime docking into Probe Basic's tabs

Stock Probe Basic (mill) ships with several empty placeholder tabs inside `CONVERSATIONAL` and an unused frame on the left side of `SETTINGS`:
- `CONVERSATIONAL > HOLE OPS` has `FACING`, `PATTERN`, `XY COORD`, and an empty `THREAD MILL` tab (`objectName="thread_mill"`).
- `CONVERSATIONAL > POCKETS` (`pockets_tab`) has 8 blank, untitled sub-tabs.
- `CONVERSATIONAL > PERIMETER` (`perimeter_tab`) also has 8 blank, untitled sub-tabs.
- `SETTINGS` (`settings_tab`) has an empty container widget on the left (`widget_59`).

When `UserTab` in `pb_conversational.py` initializes, it schedules `_dock()` via `QTimer.singleShot(0, ...)` so the main Probe Basic window is fully constructed first:

| Probe Basic Area | Stock Probe Basic | With `PB_Conversational` |
|---|---|---|
| `CONVERSATIONAL > HOLE OPS` | Empty `THREAD MILL` tab | `THREAD MILL` becomes **INT. THREAD** (Internal Thread + Peck Drill), and **EXT. THREAD** is inserted right next to it |
| `CONVERSATIONAL > POCKETS` | 8 empty, untitled tabs | Tabs 1 & 2 become **RECT. POCKET** and **CIRC. POCKET**; empty tabs 3–8 are hidden |
| `CONVERSATIONAL > PERIMETER` | 8 empty, untitled tabs | Tabs 1 & 2 become **RECT. CONTOUR** and **CIRC. CONTOUR**; empty tabs 3–8 are hidden |
| `CONVERSATIONAL > MISC` | Empty | Left untouched for your own widgets |
| `SETTINGS` | Left frame (`widget_59`) is empty | Populated with the **BACKLASH COMPENSATION** panel and dial-indicator guide |

Once all screens have been moved into their natural slots inside `CONVERSATIONAL` and `SETTINGS`, `UserTab` removes its own temporary `CONVERSATIONAL+` tab from the main tab bar (and from the `START UP TAB` dropdown in `SETTINGS`) so it doesn't clutter the top bar.

If you use a customized Probe Basic `.ui` (or a vertical screen layout) that doesn't have one of those target widgets, nothing breaks: any screen that can't find its target slot simply stays inside the `CONVERSATIONAL+` user tab.

## Tool Table (`tool.tbl`) integration

Whenever you type a `TOOL NUMBER` in any screen, `conv_common.py` queries QtPyVCP's `tooltable` plugin (`TOOL_TABLE.getToolTable()`) and shows the tool's remark/comment (`R` column) right next to the box.

If that tool number doesn't exist in your LinuxCNC `tool.tbl`, the field highlights in red and displays `TOOL NOT IN TOOL TABLE`. That's intentional: if you post a program with `T5 M6` and `T5` isn't in your Tool Table, LinuxCNC will refuse to run the `M6`.

When running a screen standalone outside LinuxCNC (for testing on a desktop PC), `TOOL_TABLE` and `STATUS` are `None` and the UI falls back cleanly.

## PyQt5 (Bookworm) and PySide6 (Trixie)

Probe Basic currently spans two Debian / Qt generations:
- **Probe Basic 0.6.x on Debian 12 Bookworm** runs on **PyQt5** and registers the font `Bebas Kai`.
- **Probe Basic 1.0.x on Debian 13 Trixie** sets `QT_API=pyside6`, runs on **PySide6**, registers `Probe Basic Bebas Mono`, and refuses to load user tabs that import `PyQt5` directly.

All widgets in `PB_Conversational` go through `qtpy` (`qtpy.QtCore`, `qtpy.QtGui`, `qtpy.QtWidgets`) and detect which font family and `QFontDatabase` API are active at runtime, so the exact same package works on both Bookworm and Trixie.
