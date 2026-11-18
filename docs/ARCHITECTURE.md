# Code structure and module notes

When I started writing these conversational screens, the first thing I wanted to avoid was burying the G-code math inside Qt widget callbacks. If you've ever tried to debug a helical lead-in radius or a stepover loop when it's tangled up with `QLineEdit` signals and LinuxCNC status polling, you know how painful that gets.

So the project is split into pure-Python toolpath generators on one side, and the Qt/Probe Basic UI on the other.

```text
pb_conversational.py (UserTab)
 ├── ThreadPage  -> conv_common.py, thread_mill_gen.py, thread_sketch.py
 ├── PocketPage  -> conv_common.py, pocket_gen.py, pocket_sketch.py
 ├── ContourPage -> pocket_page.py, contour_gen.py, pocket_sketch.py
 └── Holgura     -> backlash_ini.py
```

## Pure-Python G-code generators

`thread_mill_gen.py`, `pocket_gen.py`, and `contour_gen.py` only import `math` (plus a couple of number-formatting helpers they share with each other). None of them import Qt, `qtpyvcp`, or `linuxcnc`. You can import them from a normal Python shell on any PC, pass a params object, and inspect the resulting G-code lines.

Each generator follows the same flow: populate a parameters object (`ThreadMillParams`, `PocketParams`, or `ContourParams`), run `validate(p)` to get a list of blocking errors, call `plan(p)` to compute the geometry (radii, Z levels, entry moves, compensated center feedrates), and call `generate(p)` to get the commented `.ngc` program as a list of lines.

- `thread_mill_gen.py` handles 60° internal and external thread milling (metric ISO and Unified UNC/UNF), tangential helical lead-in/lead-out so the cutter doesn't leave a witness mark on the flank, optional 120° mouth chamfering with the lower flank of the tooth, and full-retract peck drilling (`drill_pecks()`, `drill_tip_z()`). It also includes the lookup table for BB single-form thread mills (`BK2055` / `BF2055`), which are the ones I use most in the shop.
- `pocket_gen.py` generates inside-out clearing for rectangular and circular pockets centered at `X0 Y0`. On small mills or retrofits with a bit of screw wear, zig-zag clearing constantly reverses load and eats backlash on every pass, so these toolpaths always cut in a single direction (climb or conventional) from the center outward, with helical or center-ramp entry and tangential arc lead-in/out on the finishing pass.
- `contour_gen.py` subclasses `PocketParams` (`ContourParams`) and reuses the loop and G-code emission routines from `pocket_gen.py`, working from the outside of a rectangular stock block (`STOCK X` x `STOCK Y`) inward to leave a finished rectangular or circular island at `X0 Y0`.

## UI screens and live sketches (`qtpy`)

All UI modules import from `qtpy` rather than `PyQt5` or `PySide6`. That way the same files run unmodified on Probe Basic 0.6.x (Debian 12 Bookworm, PyQt5) and Probe Basic 1.0.x (Debian 13 Trixie, PySide6).

`conv_common.py` holds `ConvPage`, the base widget shared by the six conversational screens. It takes care of reading `[DISPLAY] PROGRAM_PREFIX` and `[TRAJ] LINEAR_UNITS` from the machine `.ini`, talking to QtPyVCP's `status` and `tooltable` plugins when running live inside LinuxCNC, picking the right Probe Basic font (`Bebas Kai` vs `Probe Basic Bebas Mono`), saving the last-used values per unit system (`MM` / `IN`) into `last_*.json`, and writing `.ngc` files with auto-incremented names (`_1`, `_2`...) so you don't accidentally overwrite a program you already proved out.

The screens themselves live in `pb_conversational.py` (`ThreadPage` and the `UserTab` container that docks everything into Probe Basic on startup), `pocket_page.py` (`PocketPage`), and `contour_page.py` (`ContourPage`, which subclasses `PocketPage` and swaps in the stock fields and island geometry).

For the visuals (`thread_sketch.py` and `pocket_sketch.py`), instead of static bitmaps I wrote `QPainter` widgets that draw both a schematic cross-section (with the Z input boxes aligned to their reference planes) and a scaled top-down plan view. Because the plan view renders the actual output of `plan(p)` / `sample_paths(p, k)`, you can immediately see the helix diameter, number of radial passes, cutter size relative to the bore or island, and climb/conventional arc direction before posting anything.

## Backlash panel and `.ini` parser

`backlash.py` and `backlash_ini.py` follow the same separation: `backlash_ini.py` is pure Python and deals with parsing and rewriting `[JOINT_n] BACKLASH` in the LinuxCNC `.ini` file without trashing comments or formatting, while `backlash.py` (`Holgura`) builds the touchscreen-friendly panel that docks into the empty left frame inside Probe Basic's `SETTINGS` tab.
