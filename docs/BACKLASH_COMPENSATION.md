# Backlash compensation panel (`SETTINGS`)

Anyone who runs a converted benchtop mill, a machine with rolled ballscrews, or an older iron mill with acme screws knows that mechanical backlash is a fact of life. Even a few hundredths of a millimeter shows up when you interpolate a bearing bore or thread-mill a tight fit.

LinuxCNC has built-in per-joint backlash compensation (`BACKLASH = ...` under each `[JOINT_n]` section in the `.ini`), and it works well for light to moderate play. The annoyance is having to close LinuxCNC, open a text editor, hunt down each `[JOINT_n]` block, edit or comment out the values while measuring with a dial indicator, and hope you didn't fat-finger anything in the `.ini` late at night.

I wrote `backlash.py` and `backlash_ini.py` to put that workflow directly inside Probe Basic's `SETTINGS` tab (docked into `widget_59`, the empty space on the left side of `SETTINGS`).

## How it maps joints and reads live values

On startup, `backlash_ini.py` reads the active `.ini` file from `INI_FILE_NAME`:

- To figure out which axis letter (`X`, `Y`, `Z`...) belongs to `[JOINT_0]`, `[JOINT_1]`, etc., `letras()` checks `coordinates=...` on the `[KINS] KINEMATICS` line (e.g. `trivkins coordinates=XYZ`), falling back to `[TRAJ] COORDINATES`. If you run a dual-motor gantry (`coordinates=XYYZ`), it labels the second joint `Y2`.
- It reads `[TRAJ] LINEAR_UNITS` so the input validator and limits match your machine (`0.150 mm` max in metric, `0.0060 in` in imperial: if you have more than 0.15 mm of slop, software compensation isn't the right fix; you need to check your nut preload or angular contact bearings first).
- For each joint, it parses any `BACKLASH` line in `[JOINT_n]`, whether active (`BACKLASH = 0.035`) or commented out (`#BACKLASH = 0.035`), and also polls `linuxcnc.stat().joint[n]['backlash']` to show what LinuxCNC actually has active in memory right now (`ACTIVE NOW`).

## How `.ini` changes and backups work

When you hit **SAVE** (which is blocked if the interpreter isn't idle):

1. Before touching your `.ini`, `guardar()` copies the current file into `<config_dir>/.holgura/<ini_name>_<timestamp>.ini` and rotates the folder so the **last 30 backups** are always kept on disk.
2. Only the `BACKLASH` line inside the modified `[JOINT_n]` section is edited. Everything else in the file - comments, blank lines, indentation, encoding, and `\n` vs `\r\n` line endings - is preserved byte-for-byte. The new file is written to a temp file in the same directory and swapped in atomically with `os.replace()`.
3. When you uncheck an axis and save, it doesn't delete your measurement: it comments the line out (`#BACKLASH = 0.035`). That way, when you disable compensation to re-measure with an indicator, your old number is still sitting there in the box for comparison.
4. If a section had duplicate active `BACKLASH` lines from past hand-edits, it keeps the first one and comments out the duplicates so there's no ambiguity about which value LinuxCNC reads.

Because LinuxCNC only reads `[JOINT_n] BACKLASH` when the motion controller initializes, the status banner reminds you which axes have pending changes saved to disk and need a LinuxCNC restart to take effect.

## Mesa / stepgen acceleration headroom (`STEPGEN_MAXACCEL`)

If you use software stepgen or a Mesa card (`hostmot2`), enabling backlash compensation means the step generator has to accelerate and inject extra steps on every direction reversal faster than the trajectory planner's axis acceleration. The LinuxCNC and PNCconf documentation recommend setting `STEPGEN_MAXACCEL` to roughly **2x `MAX_ACCELERATION`** when backlash compensation is active; otherwise you can get following errors or stepgen stalls on reversals.

`aviso_aceleracion()` checks both parameters in each `[JOINT_n]` section and shows a yellow technical note at the bottom of the panel if an axis has backlash enabled with `STEPGEN_MAXACCEL < 2 * MAX_ACCELERATION`.

## Measuring backlash with a dial indicator

The bottom half of the panel has the exact checklist I use at the machine so you don't forget to turn compensation off before measuring:

1. Uncheck the axis, click **SAVE**, and **restart LinuxCNC** so active compensation is `OFF` (otherwise you're measuring your old compensation on top of the screw).
2. Put a dial test indicator or plunge indicator against the table (`X` / `Y`) or spindle head (`Z`).
3. In **MAN** mode with a `0.01` step, jog into the indicator until the needle preloads, then zero the bezel.
4. Jog **1.00 mm** (or `0.050 in`) further in that same direction so all nut and bearing play is taken up one way.
5. Jog the exact same distance back in the opposite direction. On a zero-backlash axis the needle lands back on `0.00`. Whatever it falls short by (say `0.035 mm`) is your lost motion.
6. Do it three times in the part of the travel you actually machine in, average the readings, enter the number, check the box, click **SAVE**, and restart LinuxCNC.
7. Run the same back-and-forth check once more with compensation active: the needle should come right back to `0.00` (±0.01). If it jumps *past* zero, the value is too high and circular pockets/interpolated holes will come out undersized.
