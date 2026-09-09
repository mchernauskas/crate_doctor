# Context for Claude (or any AI) working on a Rekordbox library

You are helping a DJ manage their Rekordbox library. This folder contains `crate_doctor`, a
Python CLI that reads and edits the Rekordbox database directly. Read this whole file
before you touch anything — most of it is hard-won and not guessable.

Your job is **not** to make the DJ learn a CLI. They will talk to you in plain language
("which of my tags are useless?", "fix the cues that are too close to the end"). You run
the tool, read the output, explain it in their terms, and ask before changing anything.

---

## The hard rules

These are not style preferences. Breaking any of them can destroy someone's library.

1. **Never run `--write` without showing the dry run first and getting an explicit yes.**
   Every write command works without `--write` and prints exactly what it would do.
   Show that. Wait for a real answer. "Go ahead" counts; silence does not.

2. **Rekordbox must be fully quit, including `rekordboxAgent`.** The agent keeps writing
   to the database for a while after the app window closes. Writing while it holds the
   file can corrupt the library. crate_doctor checks and refuses, but confirm with the user
   before a write — asking "is Rekordbox closed?" takes one line and saves an afternoon.

3. **Never delete a file on disk.** Not audio, not anything. If the user wants files gone,
   move them into a `_to_delete/` folder they can inspect and empty themselves. Say where
   you put them.

4. **Never touch cues, grids or tags the DJ set by hand** unless they specifically ask.
   Tag anything a script creates (`--tag "CUE(claude)"`) so it can be told apart later.
   There is no other way to distinguish hand work from script work.

5. **Every threshold is a starting point, not a law.** Ask, or measure the user's own.
   Note what the real defaults are, because they are not what you might assume:
   `--floor-bars` defaults to **auto** (the user's own 5th percentile, never below 8 bars),
   and `--target` defaults to **0**, which means *delete only, never add cues back*.
   `crate_doctor init`'s config sets `target = 10`, but with no config file present
   `cues --fix --write` will only remove cues. Say which you mean.

6. **If a number surprises you, verify it before reporting it.** This matters more than it
   sounds. Real examples from building this tool: "114 tracks have no genre" was actually
   36 sample one-shots plus 78 streaming entries and zero real songs. "6,139 audio files"
   was double-counting macOS `._` sidecar files. Both were caught by the user, not by me.
   When a count looks wrong, it usually is. Go check.

---

## What the DJ probably wants, in the order it makes sense

This is the sequence that worked. Do them one at a time, finish each before starting the
next, and report in plain language between steps.

```bash
crate-doctor backup                              # always first
crate_doctor cues                                # measure their cueing habit
crate_doctor sound -o sound.jsonl                # ~5 min for 3000 tracks, needed below
crate_doctor tags                                # audit their My Tags
crate_doctor dupes                               # same track twice?
crate_doctor disk --music-root /path/to/music    # junk on the drive
crate_doctor playlists --sound sound.jsonl       # proposed playlists
crate_doctor rename                              # filename consistency
```

Everything above is read-only. Nothing there can change the library.

---

## How the data actually works

Rekordbox stores the library in **`master.db`**, a SQLCipher-encrypted SQLite file.
`pyrekordbox` opens it; the key comes from `python -m pyrekordbox download-key` (one time,
per machine — the key is not shipped in this folder).

Locations:
- macOS `~/Library/Pioneer/rekordbox/master.db`
- Windows `%APPDATA%\Pioneer\rekordbox\master.db`

Alongside it, `share/PIONEER/USBANLZ/` holds per-track **analysis files** written when the
DJ analyzed each track:

| file | tag | what it holds |
|---|---|---|
| `.DAT` | `PQTZ` | beat grid — every beat's time in ms and its position in the bar |
| `.EXT` | `PWV5` | colour waveform, 150 samples/sec, 3 bits per frequency band + 5-bit height |
| `.EXT` | `PSSI` | phrase markers (intro / build / chorus / outro) |
| `.2EX` | `PWV7` | colour waveform, 150 samples/sec, **one byte per band** |

**Prefer PWV7 over PWV5.** Same timeline, but PWV5's 3-bit bands clip badly — on a
bass-heavy track the low band sits pinned at its maximum roughly half the time. PWV7
never hits its ceiling. 16× the resolution, same files already on disk.

Band order is lows, mids, highs. Verified empirically: the low byte peaks around 60-80 Hz,
the high byte climbs monotonically into the kHz range, and genre means line up (raw techno
reads high on lows, dance-pop reads high on highs).

**This means you can tell a breakdown from a peak without decoding any audio.** That is
the whole reason `sound` runs in minutes instead of hours.

### The WAL gotcha

`master.db-wal` being large after Rekordbox is closed is **normal**. Rekordbox does not
checkpoint on exit. A big `-wal` does **not** mean Rekordbox is running.

The only honest test is whether `PRAGMA wal_checkpoint(TRUNCATE)` returns `busy = 0`.
crate_doctor does this. Do not judge by file size, and do not tell the user their database is
in trouble because the WAL is big.

Also: if you ever copy `master.db` yourself, **copy or checkpoint the `-wal` too**, or you
silently discard whatever Rekordbox wrote in its last session.

### Tables worth knowing

| table | notes |
|---|---|
| `djmdContent` | tracks. `FileNameL` is the real filename — `basename(FolderPath)` is **truncated** on cloud-synced entries |
| `djmdCue` | `Kind = 0` is a memory cue. `Kind 1-9` are Rekordbox's own auto hot-cue suggestions, not the DJ's work |
| `djmdMyTag` | `ParentID`: `1` = Style, `2` = Components, `3` = Situation, `4` = Vibe |
| `djmdSongMyTag` | tag ↔ track |
| `djmdPlaylist` | `Attribute 0` = playlist, `1` = folder. `SmartList` holds intelligent-playlist XML |
| `djmdHistory` | **`DateCreated` is a string, not a date.** Compare against `'2025-09-08'`, not a `date` object |

Deletions everywhere are **soft**: set `rb_local_deleted = 1`. Rekordbox hides the row; the
data survives.

---

## Matching files to database rows

This is the single most error-prone thing in the whole project. It was got wrong three
times in a row before it worked. All five of these are required:

1. **Use `FileNameL`**, not `os.path.basename(FolderPath)`. Cloud-synced entries store a
   truncated stub path like `/contents_4056005572/...` that will never match anything.
2. **Normalize Unicode both ways** with `unicodedata.normalize('NFC', ...)`. macOS stores
   filenames decomposed, the database stores them composed. Skip this and every track with
   an accent looks like an orphan.
3. **Ignore `._*` files.** macOS writes an AppleDouble sidecar per file on exFAT/FAT
   volumes — the format most DJ drives use — and they carry audio extensions. Count them
   and your file total doubles.
4. **Use both name and size.** The orphan test is deliberately conservative: a file on
   disk is only called an orphan if *neither* its name *nor* its byte size appears
   anywhere in the library. That hides a few real orphans behind coincidental size
   matches, which is the right way to be wrong for a tool that must never delete.
5. **Even then, expect spelling drift.** A real case: the database said "All Apollogies",
   the disk said "All Apologies". That track looked like an orphan and nearly got binned.
   Never delete on the strength of a name match.

Also exclude Rekordbox's bundled **Sampler** one-shots (`/rekordbox/Sampler/` in the path).
They are hundreds of tiny files, they are not tracks, and they wreck every percentage.

---

## The two ideas the tool is built on

### Cues: a cue near the end of a track is worthless

There is nothing left to mix into. On the library this was built against, the DJ's own
habit worked out to about 16 bars — but that is *their* number, not a constant. The tool
measures rather than assumes: `crate_doctor cues` computes the 5th percentile of "bars after
last cue" across every track the DJ cued by hand and reports their own floor, with a hard
minimum of 8 bars.

Note the circularity: since the floor *is* their 5th percentile, about 5% of tracks always
sit below it. Present that list as outliers to eyeball, never as a to-do list.

When placing a replacement final cue, put it on the **biggest drop in kick energy** in a
window 20-36 bars from the end. That is where the outro actually starts, and where the DJ
wants to be cued to mix out — not in the dead air after it.

### Tags: a tag is only useful if it narrows

Most DJs' My Tags are useless, always for the same reason: the tags don't split the
collection. A tag on 60% of the library tells you nothing when you search it. A tag on
zero tracks is clutter.

**A tag earns its place by covering roughly 3-25% of the library.** `crate_doctor tags` sorts
theirs into *too broad*, *dead*, *thin*, *ok*.

Be prepared for the honest answer to be "delete most of them." Combinations do the work:
`Raw` + `Hypnotic` finds a far more specific record than any single cleverly-named tag, and
two tags at 15% beat ten tags at 2%.

`knowledge.json` ships 316 record labels mapped to sub-genres and moods, plus artists,
genres and title patterns. **This is the file to improve.** `crate_doctor tags --propose` prints
the labels in their collection it doesn't recognize, ranked by track count. Look those up
(actually search the web — don't guess from the name) and add them. If a label genuinely
is just generic melodic/tech house with no identity, record that as an empty entry rather
than inventing a category. An honest blank beats a wrong tag.

---

## Commands

The command installs under both `crate-doctor` and `crate_doctor`; run from the folder it
is `python3 crate_doctor.py`. Use whichever the user has been using.

Global: `--db PATH`, `--config PATH` — accepted before or after the command name. Nothing
changes without `--write`.

**`setup`** — first run. Checks Python, installs `numpy`/`pyrekordbox` if missing (asks
first unless `--yes`), finds the library (or asks for the path), fetches the decryption key,
writes `crate_doctor.ini` with the path filled in, takes a backup. Works on a bare Python with
no dependencies installed. Safe to repeat. `--db PATH` `--yes` `--force`

If a user has just unzipped the folder and nothing works yet, this is the first thing to
run. The double-click launchers (`setup-mac.command`, `setup-windows.bat`) just call it.

**`backup`** — timestamped, self-contained copy of `master.db`. No flags of its own, but
two globals matter: `--backup-dir PATH` and `--keep-backups N` (default 5, `0` keeps all),
both settable as `backups =` and `keep_backups =` under `[paths]` in the config.

**Backups default to sitting next to `master.db`, which is on the system drive, and each is
~130 MB.** Nine of them is over a gigabyte. If the user keeps their music on an external
drive, point `backups` there — suggest it once, early, rather than after their disk fills.

**`init [path]`** — writes `crate_doctor.ini`. `--force` overwrites.

**`cues`** *(can write)* — measures their habit; `--fix` repairs.
`--fix` `--tag NAME` `--all` `--floor-bars N` `--target N` `--min-space N`
`--outro-lo N` `--outro-hi N` `--limit N` `--write`

`--fix` refuses to run without `--tag` or `--all`. That guard is deliberate — leave it
alone. If `--outro-lo` lands inside the floor the tool moves the window and says so,
otherwise the delete pass and the top-up pass fight each other forever.

**`sound`** — waveform → per-track numbers. `-o FILE` `--limit N`

One JSON object per line. Fields: `id`, `title`, `bpm`, `length_s`, `rating`, `wave`,
`brightness`, `kick_mean`, `breakdown_frac` (share of the track well below its own kick
level), `loud_frac`, `bars`.

`wave` is `pwv7` or `pwv5` — which waveform format the row came from. Their brightness
scales differ, so `tags` and `playlists` keep only the majority format and say what they
dropped. If a library shows many `pwv5` rows, the fix is to re-analyze those tracks in
Rekordbox 7, not to compare the numbers.

**`id` is load-bearing** — `tags --sound` and `playlists --sound` join on it. Never
hand-build or filter a `sound.jsonl` without keeping `id`, or those commands will silently
match nothing.

The four measures are all **relative to the track itself**. A `breakdown_frac` of 0.4 means
40% of *that* track is quiet compared to *that* track's own peaks — which is what actually
matters.

**`tags`** *(can write)* — audit, and `--propose` to suggest.
`--propose` `--sound FILE` `--kb PATH` `--min-pct` `--max-pct` `--min-confidence 1-3`
`--create-tags` `--force` `-o FILE` `--limit N` `--write`

Refuses to create a tag that would land on more than `--max-pct` of the library without
`--force`. Also leave that alone.

**`playlists`** *(can write)* — Never Played 4+, Dusty Bangers, Workhorses, Underrated
Workhorses, The Turn, Sunrise, Cold Opens, Peak Time.
`--sound FILE` `--folder NAME` `--stale-days N` `--workhorse-plays N`
`--sunrise-lo` `--sunrise-hi` `--limit N` `--write`

Skips any playlist name that already exists rather than merging into it.

**`rename`** *(can write)* — filenames from library metadata, default `{artist} - {title}`.
`--pattern` `--limit N` `--write`

Renames on disk and updates the database as one operation. If the database write fails,
every file is renamed back. Never overwrites: a name already taken is skipped and reported.

Two things to warn the user about. It faithfully reproduces bad metadata — if the Title
field says `Magical - Enamour Remix feat. Zolly (Original Mix)`, that whole string becomes
the filename, contradiction included. And some artist names legitimately end in a period
(`Lello B.`, `Rodriguez Jr.`), which is why field cleaning does not strip trailing dots.
Always scroll the dry run.

**`relocate`** *(can write)* — repoint entries whose file moved or was renamed. Rekordbox's
Relocate, in bulk. Matches by **exact byte size** first (conclusive), then by name once
case and punctuation are folded, then, only with `--fuzzy`, by a single close name whose
size is within 2%. Never claims a file another entry already uses. Changes only the
database, so cues, tags, ratings, playlists and play counts all survive.
`--music-root PATH` (repeatable) `--fuzzy` `--path-as SEARCHED=STORED` `--write`

**`--path-as` is the one that will bite you.** If you reach the drive through a mount, a
VM, or a linked computer, the path you FIND a file at is not the path Rekordbox can open.
Writing the wrong one silently breaks every entry it touches. This happened for real:
paths like `/sessions/.../mnt/Music--rekordbox/...` were written into a live library where
Rekordbox needed `/Volumes/T7/Music/rekordbox/...`. The tool now warns when the database
path contains `/sessions/`, `/mnt/`, `/media/` or `/run/`, but **you** must pass
`--path-as /the/mount=/what/Rekordbox/sees` and check the dry run's `->` line, which shows
the path that will actually be stored.

**`dupes`** — same filename + byte size (near-certain), and same title/BPM/length
(maybe a remix pack). `--limit N`. Never changes anything.

**`disk`** — audio on the drive nothing references, and entries whose file is missing.
`--music-root PATH` (repeatable) `--limit N`. Never moves or deletes anything.

Refuses to run if a root doesn't exist — an unplugged drive looks exactly like a library
where every file vanished. It also refuses if it finds no audio at all, though that check
is across all roots combined: pass one good root and one empty one and it proceeds, with
the empty one simply contributing nothing. Check the "audio files found" count in the
output against what the user expects before believing the orphan list.

---

## How writes are made safe

Every `--write` path does the same thing:

1. Back up `master.db` and its WAL, timestamped
2. Refuse if Rekordbox is running. Two tests: an exact-name process check for
   `rekordbox` / `rekordboxAgent` (`tasklist` on Windows, `pgrep -x` elsewhere), and, when
   the WAL is non-empty, a checkpoint that must return `busy = 0`. The process check is
   blind when crate_doctor runs inside a sandbox or VM that cannot see the host's processes —
   which is exactly the situation when Claude drives it through a linked computer. **So
   still ask the user whether Rekordbox is closed.** (A blocked write leaves the backup
   from step 1 behind; harmless, but it explains stray backup files.)
3. Apply changes to a **private copy**
4. Run `PRAGMA quick_check` and `PRAGMA foreign_key_check` on the copy
5. Only if both pass, replace the live file

If step 4 fails, the live database is untouched and the working copy is left for
inspection. `rename` additionally journals its disk renames and reverses them if the
database write fails.

Backups are made self-contained: the WAL is folded into the copied `.db` so one file
restores everything. **To restore:** quit Rekordbox, copy the backup over `master.db`,
delete the `-wal` and `-shm` files next to it.

---

## Config

`crate_doctor init` writes `crate_doctor.ini`. Everything that is a matter of taste lives there:
`[paths]`, `[cues]`, `[rename]`, `[tags]`, `[tags.audio]`, `[playlists]`. Command-line
flags override the file.

`[tags.audio]` is the part worth showing the DJ — they can invent their own waveform tags:

```ini
[tags.audio]
Basement = kick_mean      >= 0.90
Airy     = brightness     >= 0.90
Patient  = breakdown_frac >= 0.70
```

Fields: `brightness`, `kick_mean`, `breakdown_frac`, `loud_frac`. The number is a
percentile **of their library**, so "Dark" means dark next to what they actually play, not
dark on some absolute scale.

---

## Bugs that were already found — don't reintroduce them

If you extend this tool, these are the traps:

- **`if not anlz_file:` raises `RecursionError`** inside pyrekordbox's `AnlzFile.__len__`.
  Always test `is None`. With a broad `except`, this presents as "every track skipped: no
  analysis data" — the tool looks like it ran fine and did nothing.
- **A cue-delete pass and a cue-top-up pass will fight** if the outro window overlaps the
  floor. Clamp one against the other.
- **Validate before backing up.** A 130MB backup taken just before an argument error is
  pure waste.
- **`pip install .` on older setuptools** silently installs a package named `UNKNOWN 0.0.0`
  with no working command. `setup.cfg` is in this folder as the fallback — keep it in sync
  with `pyproject.toml`.
- **With `--tag`, count and spacing must see every cue on the track, but only tagged cues
  may be deleted.** An earlier version filtered to tagged cues first, so on a track with 6
  hand cues and 4 tagged ones, "top up to 10" would add 6 more (16 total) and could place
  them next to the hand cues. `fix_cues` now keeps `allc` and `ours` separate.
- **Waveform Vibe tags come only from the waveform.** A label mapped to "Dark" in
  `knowledge.json` is a guess about the label; the low-band energy is a measurement of the
  track. Metadata sources for any tag named in `[tags.audio]` are ignored on purpose.
- **A path you can see is not always a path Rekordbox can open.** Anything that writes
  `FolderPath` (`relocate`, `rename`) must store the path as the machine running Rekordbox
  sees it. Through a mount or sandbox those differ, and getting it wrong breaks entries
  without any error.
- **`SafeWrite` cannot assume it may delete its working copy.** Sandboxes and some network
  shares refuse; it now uses a unique per-run name and empties the file if it cannot
  remove it.
- **`shutil.copy2` preserves the source mtime.** Every backup therefore reports the same
  age, so anything that prunes or picks "the newest" must read the timestamp out of the
  filename instead. Sorting backups by mtime deleted the wrong ones.
- **Never swallow exceptions silently in a per-track loop.** Count them, keep the first
  three messages, and print them when many tracks were skipped.
- **Test writes against a copy**, never the live database. Copy `master.db` to a scratch
  folder, symlink the real `share/` folder next to it, and run there.

---

## What this cannot do

It cannot hear the records. Everything it knows about sound is three frequency bands at 150
samples a second — plenty for energy shape, useless for whether a track is any good.

It cannot tell hand-placed cues from script-placed ones except by the comment tag.

It cannot fix metadata that was wrong when the track was bought. A track filed under the
wrong label stays wrong no matter how good `knowledge.json` gets.

---

## Tone

The DJ cares about their library and does not care about SQLite. Say "your cues", not
"`djmdCue` rows". Give them the number and what it means. When you are unsure, say so and
go check rather than producing a confident wrong figure — they will catch it, and they
should be able to trust the ones you don't flag.
