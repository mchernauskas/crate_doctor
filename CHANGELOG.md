# Changelog

## 0.9.0a9 — alpha

### The phrase-grid detector is out — it did not survive its first unseen batch

Third review batch. Thirty tracks and three hundred vetted cues now, with the
ground truth keyed by artist *and* title (an earlier fit had let a second "Yeah" by
a different artist into the set).

The a8 detector was fit on batch two and gained six cues there. On batch three,
the first data it had not seen, it gained nothing overall and turned one track into
a one-in-ten: *That Boy* (Abe Duque remix) has PSSI boundaries at +5 off the
8-grid, the detector snapped all ten cues onto them, and the DJ moved all ten back
onto the 8-grid. So the DJ follows a shifted phrase grid on some tracks (Heart in
Hand +2, Talk Box +4, the LUNR remix +1) and ignores it on others, and there is no
rule in six examples that tells the two apart. Aggregate across 30 tracks: 8-grid
snap 202/300, detector 207/300, and the whole five-cue difference is the batch it was
trained on. A rule that only works on its training data is not a rule. Removed;
worth another look at 60+ tracks.

Two more ideas were tested on the full set and rejected before they shipped:

- **Snap anchor 32 to a nearby phrase boundary.** Suggested by Serenata (32 → 24),
  Calcio (32 → 36) and the LUNR remix (32 dropped for 24). Over 30 tracks it is
  *worse* — 197 vs 202. The anchor stays where it is.
- **Fall back to a phrase boundary when no energy drop qualifies for the outro cue.**
  Suggested by Life Itself, where the tool placed no outro cue and the DJ added one
  by hand. Harmless but changed nothing measurable, so not added.

### What held

The 28–40 outro window is still the best over all 30 tracks — 17 of 30 last cues
exactly right, against 12–15 for every wider or earlier window tried. The DJ's last
cue sits a median of 32 bars from the end; the spread is wider on tracks over 230
bars (27–61) and on the one track under 120 bars (20), but no window that caters to
those beats 28–40 overall. Phrase weight 2.2 unchanged. Overall agreement over three
batches: 67% exact, 74% within two bars.

The lesson for the loop: every change is now tested against *all* vetted tracks
before it ships, and a gain that lives only in the batch that suggested it is
discarded.

## 0.9.0a8 — alpha

### The 8-grid is not always the grid: snap to the phrase boundaries instead

Second review batch, and the first one re-examined by position rather than by tag
(the tag reasoning in a7 was wrong — see the correction there). Two batches now,
22 tracks, 219 vetted cues. 84% of the DJ's cues sit exactly on a Rekordbox PSSI
phrase boundary, 90% within two bars. That held across both batches.

What batch two added: on a handful of tracks the phrase grid is **shifted off the
8-grid by a constant**. Heart in Hand's phrases are a clean 16-grid at +2 bars;
Talk Box's and one *That Boy* remix at +4 and +5. On those the DJ cues the phrase
every time, and the old snap-to-8-grid was pulling every cue one to four bars off
the phrase it belonged on — the single biggest source of disagreement in the batch.

The fix decides the grid per track. When a track's phrase boundaries share a
dominant nonzero offset mod 8 (`≥60%` at the same offset, a regular shifted grid),
snap candidates to the phrase boundaries; otherwise snap to the 8-grid as before.
The guard matters: a naive "always snap to phrase" *regressed* the majority of
tracks from 70% to 65%, because most tracks are phrased on the 8-grid and forcing
the odd energy event onto a distant phrase boundary is wrong. It also deliberately
does not fire on tracks whose phrasing is merely irregular rather than shifted
(Polly's Acid Kiss), where the DJ cues the 8-grid. Net across both batches: exact
agreement 70% → 72%, within-two-bars unchanged. A small, targeted win — honestly
worth much less than the outro-window fix in a7 — but it is the right mechanism and
it is measured, not guessed.

### What did NOT change

The phrase weight stays at 2.2 and the outro window at 28–40; batch two confirmed
both (the DJ's last cues landed 28–41 bars from the end, and 9 of 10 of the tool's
outro placements survived his review). No change was made on the strength of a
single track.

## 0.9.0a7 — alpha

### The final cue was landing too close to the end — measured, not guessed

First batch of the review loop: ten tracks, a hundred cues. The DJ confirmed 85
positions and moved 15 (see the note on hand-set rows below for why it is not "71
kept, 29 replaced"). The single clearest pattern was the last cue on the track. They moved
it on five of the seven tracks they touched, every time in the same direction —
earlier — and every time onto a phrase boundary:

| track | last cue was | they moved it to |
|---|---|---|
| Polly's Acid Kiss | 20 bars from end | 36 |
| Peninsula | 17 | 57 |
| Vibin Check | 17 | 29 |
| Rock You | 19 | 31 |
| Hewy Go | 22 | 34 |
| Talk Box | 20 | 28 |

Their hundred vetted cues put the last one at 28, 29, 29, 30, 31, 33, 33, 34, 36
and 40 bars from the end. **Nothing below 28.** The outro window was 20–36, so the
tool was free to cue inside the last 20 bars, which is past the point where there is
enough track left to mix out of. It is now **28–40**, and on the next ten tracks that
moves the last cue from a median of 19 bars out to 32.

### Phrase boundaries are worth more than the grid

86 of their 100 vetted cues sit exactly on a PSSI phrase boundary; 91 within two
bars. The phrase weight goes 1.6 → 2.2, which raises the model's own on-phrase rate
from 78% to 85% on that batch. Honest caveat: on the *next* ten tracks the same
change moved on-phrase from 68% to 69%, so this one is worth a fraction of what the
outro fix is worth, and a wider sample may revise it. The outro change reproduces
nine of their ten last-cue positions exactly.

### `cues --local --newest N`

`--fix` could only run on the whole library, which is useless for a review loop —
rebuilding a track the DJ has already vetted throws their work away. `--newest N`
takes the N most recently added tracks and `--local` skips Cloud Library Sync
entries, the same scoping `hotcues` uses. The run prints the tracks in scope before
it does anything.

**A cue the DJ sets by hand is a new row with no comment, even when it lands on
the same downbeat as the one it replaces.** On two tracks the DJ cleared the
tool's cues and re-cued from scratch. Fourteen of those landed on the same bar as
before — 1 to 2 ms off, which is Rekordbox's own quantise against the tool's
`int(round(...))` of the grid time — and the first reading of the diff mistook
them for a sync artefact. They are not. The row `created_at` times run one every
ten seconds through the review session, twenty minutes before the sync, and the
cues the DJ left alone kept their row ID and their `CUE(Claude)` tag straight
through the sync. **Cloud Library Sync does not touch cue rows.** The correction
was made after the DJ pointed out that they had made edits close to the originals.

What it means for the loop: a re-set on the same bar is the strongest possible
confirmation of that position, not a change, and it only shows up if the diff
compares positions against the backup rather than trusting the tag. Counting
that way, batch one was 85 of 100 positions confirmed and 15 moved, not 71 kept
and 29 replaced.

## 0.9.0a6 — alpha

### `hotcues`: clear hot cues from the part of the library you haven't vetted yet

Eleven commands. `hotcues` reports on hot cues (`djmdCue.Kind != 0`) and, with
`--clear`, soft-deletes them from a slice of the library. Memory cues are never
touched.

Why it exists: the review loop. The DJ works down the Date Added list a batch at a
time, checking the cues the tool placed. Rekordbox's own memory→hot conversion
refuses to write onto a full bank ("There are no Hot Cues left that can be set"),
so the tracks not yet reviewed need their hot cues cleared before the fixed memory
cues can be converted.

Scope narrows three ways, all optional:

- `--local` — only tracks whose file is a real path on this machine, not Cloud
  Library Sync entries
- `--skip-newest N` — leave the N most recently added tracks alone. The dry run
  prints the last kept and first cleared track so the boundary can be checked
  against the Date Added column before anything is written.
- `--since YYYY-MM-DD` — only hot cues created on or after that date. Without it,
  the dry run lists every track whose hot cues predate the bulk batch, because those
  are almost certainly hand work.

First real run: 458 local tracks, 21 vetted, 4,303 hot cues removed from 431
tracks, 57 hand-set hot cues on 6 tracks protected by `--since`.

**Hot cue numbering.** `Kind` 1, 2, 3, 5, 6, 7, 8, 9 are A–H — 4 is skipped — and
10, 11, … carry on as I, J, … for the extended banks. `hotcue_letter()` does the
mapping.

**Date Added order.** `DateCreated` is a date; two hundred tracks imported in one
go all share it. `created_at` carries microseconds and reproduces Rekordbox's
Date Added column exactly, including within a batch. `by_date_added()` sorts on it.

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
