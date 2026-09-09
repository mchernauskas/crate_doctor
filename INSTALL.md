# Installing crate_doctor

Latest version: **https://github.com/mchernauskas/crate_doctor**

## The short version

**Double-click the setup file for your computer.** That's it.

| | |
|---|---|
| Mac | `setup-mac.command` |
| Windows | `setup-windows.bat` |
| Linux | `setup-linux.sh` (from a terminal) |

It checks you have Python, installs the two packages crate_doctor needs, finds your Rekordbox
library, fetches the key that unlocks it, writes a settings file with your paths already
filled in, and takes a backup. Then it tells you what to try first.

If something is missing it says exactly what and where to get it, and you double-click
again after.

---

## What it needs from your computer

**Python 3.9 or newer.** That's the only thing you might have to install yourself.

- **Mac** — Newer Macs don't ship with it. If setup says so, get it from
  [python.org/downloads/macos](https://www.python.org/downloads/macos/), run the installer,
  double-click setup again. (If a dialog offers to install "command line developer tools",
  say yes — that works too.)
- **Windows** — Get it from [python.org/downloads/windows](https://www.python.org/downloads/windows/).
  **On the first installer screen, tick "Add python.exe to PATH."** That box is the
  difference between it working and not. Don't use the Microsoft Store version — it's a
  stub that opens the Store instead of running.
- **Linux** — `sudo apt install python3 python3-pip` or your distro's equivalent.

**Internet, once.** To install the two Python packages (`pyrekordbox`, `numpy`) and to
fetch the Rekordbox decryption key. After that it works offline.

**Rekordbox 6 or 7**, opened at least once on this machine so a library exists.

---

## Where it looks for your library

Setup checks these automatically:

| | |
|---|---|
| Mac | `~/Library/Pioneer/rekordbox/master.db` |
| Windows | `%APPDATA%\Pioneer\rekordbox\master.db` |

If yours is somewhere else — an external drive, a second user account — setup will ask you
to paste the path, and remembers it in `crate_doctor.ini` so you never type it again. Or pass it
directly:

```bash
python3 crate_doctor.py setup --db /path/to/master.db
```

---

## The key

Rekordbox encrypts its database. `pyrekordbox` knows how to fetch the key, and setup does
it for you the first time (`python -m pyrekordbox download-key` if you ever need it by
hand). The key is **not** included in this folder and is never stored by crate_doctor.

---

## Doing it by hand instead

If you'd rather not run a setup script:

```bash
pip install pyrekordbox numpy
python -m pyrekordbox download-key
python crate_doctor.py setup            # still worth running: finds your library, writes config
```

Or install it as a proper command so `crate-doctor` works from any folder:

```bash
pip install .
crate-doctor setup
```

If `pip install` complains about an "externally managed environment" (Homebrew Python,
newer Debian/Ubuntu), either add `--user --break-system-packages`, or use `pipx install .`.

---

## Two spellings, same command

Once installed, both work — use whichever you prefer:

```bash
crate-doctor cues      # hyphen: the usual CLI convention, no shift key
crate_doctor cues      # underscore: matches the file and module name
```

Running the file directly is always `python3 crate_doctor.py` — Python filenames can't
contain hyphens.

## After setup

```bash
python3 crate_doctor.py cues       # Mac / Linux
py crate_doctor.py cues            # Windows
```

Everything is read-only until you add `--write`. Settings live in `crate_doctor.ini` next to
the script. Or open the folder in the Claude desktop app and just ask — `CLAUDE.md` gives
it the full picture.

---

## If something goes wrong

Run setup again — it's safe to repeat and it says which step failed. The most common ones:

- **"could not find master.db"** — Rekordbox has never been opened on this machine, or the
  library is on a drive that isn't plugged in.
- **"database will not open"** — the key hasn't been fetched. Setup offers to do it; you
  need to be online.
- **"pip could not install"** — usually a permissions thing. Try the by-hand commands above.
- **"rekordbox is running"** — fine for looking. Quit it fully (the background agent too)
  before any `--write`.
