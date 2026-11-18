# How the installer works (and what it touches on disk)

I hate installers that scatter files across system directories or silently rewrite your machine config without an easy way back. Everything in `PB_Conversational` lives inside your LinuxCNC config folder, needs no `sudo`, and keeps a complete log and original backup so `uninstall` can put your `.ini` back byte-for-byte.

## The `.run` single-file package

`installer/build_package.py` packs `pb_conversational/`, `installer/pb_conversational_installer.py`, `README.md`, and `LICENSE` into a `.tar.gz` archive (stripping out `__pycache__`, `.pyc`, and any local `last_*.json` / `ultimos_*.json` files) and appends it in base64 at the end of a short bash header to produce `dist/pb_conversational_1.5.0.run`.

When you run `bash pb_conversational_1.5.0.run` on the machine:
- It checks that `python3` is present (already there on any Probe Basic install), unpacks the archive into a `mktemp -d` folder that is automatically cleaned up on exit, and hands control to `pb_conversational_installer.py`.
- If you just want to inspect the source files without touching any config, run:
  ```bash
  bash pb_conversational_1.5.0.run --extract
  ```

## What happens during `install`

If you don't pass an `.ini` path on the command line, the script scans `~/linuxcnc/configs/*/*.ini` for Probe Basic mill configurations (`[KINS]` section present and `DISPLAY = probe_basic...`, skipping `probe_basic_lathe`). If you have more than one mill config, it lists them and asks which one to use.

Once an `.ini` is selected:

1. It creates a hidden state folder inside your config directory:
   ```text
   <config_dir>/.pb_conversational/<ini_name>/
   ├── ini_original.ini                # byte-for-byte copy of your .ini before any edit
   ├── install_log.json                # record of every line or folder touched
   └── pb_conversational_installer.py  # local copy so you can uninstall anytime
   ```
2. It checks `USER_TABS_PATH` under `[DISPLAY]` in your `.ini`:
   - If `USER_TABS_PATH` isn't enabled, it inserts `USER_TABS_PATH = user_tabs/` (right under any commented `#USER_TABS_PATH` line if one exists).
   - If you already had `USER_TABS_PATH` active, it makes sure it ends with a trailing `/` and splits any inline `#` comment onto its own line above (see [Probe Basic integration notes](PROBE_BASIC_INTEGRATION.md) for why those two quirks matter).
   - It also scans the rest of `[DISPLAY]` and prints a heads-up if any other key has an inline `#` comment that LinuxCNC might misread.
3. If `USER_TABS_PATH` wasn't active before and your `user_tabs/` folder still had Probe Basic's stock example directories (`template_main`, `template_sidebar`), it moves them into `.pb_conversational/<ini_name>/moved_tabs/` so Probe Basic doesn't suddenly pop up blank template tabs. (If you already had `USER_TABS_PATH` enabled and want those templates moved aside too, pass `--set-aside-templates`.)
4. It copies `pb_conversational/` into `<user_tabs_dir>/pb_conversational/`. If you run `install` again later to update to a newer version, it preserves your saved form parameters (`last_*.json` and legacy `ultimos_*.json`).

## What happens during `uninstall`

Running `bash pb_conversational_1.5.0.run uninstall` (or invoking the saved `pb_conversational_installer.py uninstall` inside `.pb_conversational/`) reads `install_log.json` and reverses the installation:

- Deletes `user_tabs/pb_conversational/` (unless you have a second `.ini` in the same config folder that still has `PB_Conversational` installed and shares that `user_tabs/` directory).
- Moves any stashed folders in `moved_tabs/` (`template_main`, etc.) back into `user_tabs/`, and removes `user_tabs/` only if the installer created it and it's now empty.
- Compares the SHA-256 hash of your current `.ini` against the hash recorded right after installation:
  - If you haven't edited the `.ini` since installing, it restores `ini_original.ini` byte-for-byte.
  - If you *did* edit the `.ini` in the meantime (tuned velocities, updated backlash, changed HAL files, etc.), it keeps your edits and only reverts the `USER_TABS_PATH` lines the installer touched, while also dropping a copy of the pre-install backup at `<config_dir>/ini_before_pb_conversational.ini` just in case.
- Cleans up `.pb_conversational/` and leaves any `.ngc` programs you posted in `nc_files` untouched.
