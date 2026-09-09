# Changelog

## 0.9.0a1 — alpha

First release. Every command has been exercised against a real 3,214-track Rekordbox 7
library — 3,214 tracks, 30,884 cues, 227 playlists — including all five write paths and a
verified rollback.

**Where it has actually been run: Linux only.** The library under test lives on a Mac, but
the code itself executed on Linux against those files over a mount. That means:

- **Linux** — every command run end to end, including `setup-linux.sh`. Rekordbox itself
  does not run on Linux, so this is only useful for working on a copied library.
- **macOS** — never executed. The macOS-specific branches (database auto-detect,
  `pgrep -x` for a running Rekordbox, config and knowledge-base locations) were verified to
  produce correct absolute paths by forcing `sys.platform`, but no line has run on a real
  Mac. `setup-mac.command` was tested by running its payload, not by double-clicking.
- **Windows** — never executed. Same treatment: the `win32` branches were checked for
  correct paths and the `py` launcher, but `tasklist`, `%APPDATA%` resolution and
  `setup-windows.bat` are unproven.

Alpha means exactly that: expect rough edges, and expect command names, flags and config
keys to move. `pip` will not install this without `--pre`.

Path from here: `b1` once a few people have run it and nothing structural needs changing,
then `1.0.0` once it is proven on macOS and Windows both. From `1.0.0` on, anything that
would break an existing `crate_doctor.ini` requires a major bump.

Eight commands, all read-only unless you pass `--write`.

- `setup` — first run. Installs missing packages, finds your library, fetches the
  decryption key, writes a config with your paths, takes a backup. Runs on a bare Python.
  Double-click launchers for macOS, Windows and Linux.
- `cues` — measures your own cueing habit and lists the tracks that break it. `--fix`
  removes dead-air cues and can top each track back up, placing the final cue on the
  outro's kick-energy drop.
- `sound` — decodes the colour waveform Rekordbox already computed into per-track numbers
  (brightness, kick weight, breakdown share, loud share). No audio decoding.
- `tags` — audits whether your My Tags actually narrow the library, and proposes new ones
  from a knowledge base of 316 researched record labels plus your own waveforms.
- `playlists` — builds crates from ratings, play counts, play history and the waveform.
- `relocate` — repoints entries whose file moved or got renamed, matching on exact byte
  size. Rekordbox's Relocate, in bulk.
- `rename` — brings filenames into one standard from your library metadata, updating the
  database in the same operation.
- `dupes`, `disk` — the same track twice; drive versus library.
- `backup` — timestamped, self-contained copy of the database.

Configuration lives in `crate_doctor.ini` (`crate_doctor init` or `crate_doctor setup` writes it),
including user-defined waveform tags. `CLAUDE.md` gives an AI assistant the full context
to drive the tool conversationally.

### Fixed before release

- Backups defaulted to the system drive beside `master.db` at ~130 MB each. They now go
  wherever `[paths] backups` points, prune to `keep_backups` (default 5), and fall back
  gracefully if that drive is unplugged.
- The backup prune sorted on file mtime, which `shutil.copy2` copies from the source — so
  every backup claimed the same age and the wrong ones were deleted. It reads the timestamp
  from the filename now.

### Testing

Against a 3,214-track Rekordbox 7 library on macOS: every command read-only, every write
path on scratch copies, plus one live `relocate` of 6 entries.

**Not yet verified:** the double-click launchers as actual double-clicks, anything on
Windows, and database auto-detection (the test bridge runs Linux, where pyrekordbox's
macOS lookup never fires).
