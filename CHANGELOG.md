# Changelog

## 0.9.0a5 — alpha

### Cue IDs must be numeric, or Rekordbox hangs when you click them

`djmdCue.ID` is a TEXT column and the cue writer filled it with a UUID. SQLite
accepts that, `integrity_check` and `foreign_key_check` both pass, and the cue shows
up in Rekordbox at the correct position with the correct name. But **clicking it
makes the application hang** — Rekordbox stores a 32-bit unsigned integer there
(every one of the 26,377 cues it wrote on the reference library is numeric) and
parses it back to an int on access.

Now `str(random.randint(1, 4294967295))`, checked against existing IDs.

Found only after a user reported that cues placed by this tool felt unresponsive
while their own felt fine. Three earlier fixes to the same symptom — `InFrame=0`,
`rb_local_usn` being set where Rekordbox leaves it NULL, and four loop/colour fields
set to `0` instead of `NULL` — were all genuine defects and are all still worth
having, but none of them was the cause. Confirmed by elimination: identical
positions and identical values in every other column, slow with UUID ids and fast
with numeric ones.

**Why it took so long:** four separate column-by-column comparisons were run against
Rekordbox's own cues, and all four excluded `ID` as "obviously different per row".
It is different per row. It was not supposed to be a different kind of value.

## 0.9.0a4 — alpha

### Cues were written with a contradictory position

A `djmdCue` row records where it is twice: `InMsec` in milliseconds and `InFrame`
in frames at 150fps. The cue writer set the first and hardcoded the second to
zero, so every cue this tool placed claimed to be both a minute into the track and
at frame zero. Rekordbox accepts the row, but goes slow and unresponsive
reconciling it whenever those cues are clicked.

`InFrame` is now `int(InMsec * 0.15)` -- a relationship that holds for 46,990 of
46,990 of Rekordbox's own cues, with no exceptions. `ContentUUID` is set to the
track's UUID as well; all 52,333 of Rekordbox's cues set it and cloud sync uses it.

**Nothing in the test suite could have caught this.** `PRAGMA integrity_check` and
`foreign_key_check` both passed on every affected write, because neither knows
that two columns are supposed to agree. It surfaced when the user clicked a cue
and noticed the application hesitate.

To repair a library already written to, re-run `cues --fix --rebuild --write` with
the same `--tag`, or see the note in CLAUDE.md.

## 0.9.0a3 — alpha

Verified end to end on macOS. See the notes under 0.9.0a2, which this supersedes.

## 0.9.0a2 — alpha

### `intake` and `finish`: getting new music in

Ten commands now, not eight. Previously the tool could only work on tracks that were
already in your library and already analysed; getting them there was left to you.

- **`intake <folder>`** — adds new music to the library. Reads the tags the files
  already carry (title, artist, album, genre, label, remixer, BPM), creates the
  artist/album/genre/label rows it needs without duplicating existing ones, and
  leaves every file exactly where it is. Records the batch as a playlist named
  `_intake <date>`.
- **`finish`** — checks Rekordbox actually analysed the batch, then reports what the
  rest of the tool can now do with it. Changes nothing on its own.
- **`intake --open`** — launches Rekordbox afterwards. It deliberately does not try
  to click the analysis dialog: driving another application's UI by simulating input
  fails silently and needs accessibility permissions this tool has no business
  asking for.

**How the analysis actually works, since it took an experiment to find out.**
Rekordbox decides what to analyse by looking for collection entries that have no
analysis yet, and it makes that check at startup. Four identical copies of one file
were injected with different starting states to find out what it keys off:

| armed with | analysed? |
|---|---|
| `Analysed=0`, nothing else | yes |
| `Analysed=NULL`, nothing else | yes |
| `Analysed=0` plus a row in `networkAnalyze6.db`'s `manage_tbl` | yes — the queue row was ignored entirely |
| `Analysed=NULL` plus a pre-assigned `AnalysisDataPath` | yes — used the given path, did not need it |

So the minimum viable entry is a row with `Analysed` left unset. No queue table, no
path pre-assignment, no dragging to a deck. One confirmation covers the whole batch.

Writing the analysis files directly was ruled out: `pyrekordbox` round-trips `.DAT`
and `.EXT` byte-identically but cannot rebuild `.2EX`, and Rekordbox 7 also writes a
fourth format, `.3EX`. Analysis stays Rekordbox's job.

### Cloud Library Sync: a correction, and the bug it caused

Rekordbox stores cloud-synced tracks under a path that is not a filesystem path
(`/contents_<id>/artist/album/...`) while the file itself sits in the user's music
folder. This was originally read as "a streaming stub with no file behind it", and
`intake` was built on that assumption: an incoming file matching such an entry was
**added** rather than skipped.

That was wrong. On the reference library, 2,116 of 2,756 cloud entries (77%) resolve
to a real file on disk by filename + exact byte size. Running `intake` over that
library's own music folder would have created 2,116 duplicate entries, each splitting
the track's cues and play history in half.

- **`intake`** now treats filename + exact size as a duplicate regardless of the
  stored path. `--force` still adds them, and now prints a warning saying what it is
  about to do and to how many.
- **`rename`** indexed nothing and used `os.path.exists(FolderPath)`, so it reported
  every cloud entry as "file not found on disk" — 2,756 of 3,214 tracks. It now
  resolves them through the music-root index and separates three distinct cases:
  cloud-synced (file present), moved (run `relocate`), and genuinely missing. It
  deliberately refuses to rename cloud-synced entries: renaming rewrites
  `FolderPath`, and repointing a synced entry at a local path breaks the sync.
- **`disk`** was unaffected — it already matched by filename anywhere under the
  scanned roots rather than trusting the stored path.

### Also

- **Music root is inferred from the library.** `disk` and `relocate` no longer need
  `--music-root`: the folder is read off the paths the database already stores.
  Grouped by drive first, so a library spread over an internal disk and two externals
  does not collapse to `/`, and strays are dropped before taking the common ancestor
  so one file in `Downloads` cannot drag the root up a level. It says out loud what
  it inferred, and distinguishes "no idea" from "your library says the T7, which is
  unplugged".
- **Streaming and cloud entries are told apart from local files by path shape.**
  Rekordbox stores them with a fake absolute path (`/contents_4056005572/...`) that
  no column in the database distinguishes from a real file — same `FileType`, same
  `FileSize`, same `AnalysisDataPath`. On the test library that is 2,756 of 3,214
  entries, and treating them as real files produced a bogus music root.
- **`intake` catches duplicates inside a single batch** by size plus a hash of the
  first megabyte, so the same download saved twice under two names does not become
  two entries with your cues on only one of them.

### Now verified on macOS

Run on macOS 15.6 (Darwin 25.6.0, arm64), Python 3.12, against a real 3,214-track
Rekordbox 7 library: **17 of 17 checks passed.**

- `setup` installed both dependencies from cold. Homebrew's Python refused with PEP 668
  `externally-managed-environment`; the `--break-system-packages` fallback carried it.
  `sqlcipher3-wheels` arrived as a prebuilt arm64 wheel, so no compiler and no
  `brew install sqlcipher` was needed.
- Database auto-detect found `~/Library/Pioneer/rekordbox/master.db` with no flag.
- The Rekordbox process guard (`pgrep -x`) ran correctly on darwin.
- All ten read-only commands ran against the real library.
- All four write paths ran against a throwaway copy; `PRAGMA integrity_check` returned
  ok and `foreign_key_check` found 0 problems afterwards.
- The real library was SHA-256 identical before and after the entire run.

**Still unverified:** `launch_rekordbox()`'s `open -a rekordbox` branch; the write
guard's refusal while Rekordbox is actually running; the cloud-sync fix to `rename`,
which postdates that test run. Windows remains entirely untested.

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

Eight commands, all read-only unless you pass `--write`. (Ten as of a2.)

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
