# crate_doctor

One Python file that audits and repairs a **Rekordbox 6 or 7** library from the command
line. macOS, Windows or Linux. It finds your database itself, and every number that's a matter of
taste is **measured from your own library** rather than hardcoded by someone else.

Read-only unless you type `--write`.

**https://github.com/mchernauskas/crate_doctor**

> **Status: alpha.** Tested end to end against a real 3,214-track library, but the code has
> only ever *executed* on Linux — the test library lived on a Mac and was reached over a
> mount. The macOS and Windows code paths are written and path-verified but unrun. Back up
> before you use `--write`, and expect names and flags to move. See CHANGELOG.md.

```
backup     do this first
cues       your cueing habit, which tracks break it, and --fix to repair them
sound      per-track energy numbers from the waveform Rekordbox already made
tags       do my My Tags narrow anything? plus suggestions from 316 researched labels
playlists  built from ratings, play history and waveforms
rename     put every file on disk into one naming standard
relocate   repoint entries whose file moved or got renamed
dupes      the same track in your library twice
disk       junk on your drive nothing uses, and entries whose file is gone
```

---

## How it works

Rekordbox keeps everything in a SQLite database called `master.db` — tracks, cues,
playlists, tags, ratings, play counts. It's encrypted, but `pyrekordbox` opens it. That's
the whole trick: crate_doctor reads and edits that database directly, which is how it does
things the Rekordbox UI has no button for.

It also reads the **analysis files** Rekordbox wrote when you analysed each track
(`.DAT` / `.EXT` / `.2EX` under `share/PIONEER/USBANLZ`). Those hold the beat grid, the
phrase markers, and the colour waveform. The waveform is the interesting one: a
per-frequency-band energy reading at 150 samples per second, already computed, sitting on
your drive doing nothing. Decode it and you get the shape of every track — where the kick
drops out, where the breakdown is, how bright it is — **without opening a single audio
file**. It's about 10 tracks a second, so a 3,000-track library takes ~5 minutes. Actually
decoding the audio would take hours.

So: the beat grid says where the bars are, the waveform says where the energy is, the
database says where your cues and tags are. Everything else follows from those three.

**Nothing is hardcoded to one person's taste.** `cues` doesn't tell you what your cueing
should look like — it measures what it already *is* and hands you back your own habit as a
number. `tags` doesn't tell you which tags are good — it tells you which of yours actually
narrow your library.

---

## Rather just talk to Claude about it?

You don't have to learn any of this. Put this folder somewhere on your computer, open it in
the Claude desktop app with your computer linked, and talk normally:

> *"audit my rekordbox library"*
> *"which of my tags are actually useless?"*
> *"find tracks I rated 4 stars and never played out"*
> *"fix the cues that sit too close to the end"*

`CLAUDE.md` in this folder tells Claude everything it needs — what the tool does, how the
Rekordbox database works, the safety rules, and the mistakes not to repeat. Claude runs the
commands, reads the output, explains it in plain terms, and asks before changing anything.

The rest of this README is for driving it yourself.

---

## Install

**Double-click the setup file for your computer:**

| | |
|---|---|
| Mac | `setup-mac.command` |
| Windows | `setup-windows.bat` |
| Linux | `setup-linux.sh` |

It installs what's missing, finds your library, fetches the decryption key, writes your
settings file with the paths filled in, and takes a backup. The only thing it might send
you off to install yourself is Python, and it tells you where.

Details, and the by-hand version, in **INSTALL.md**.

Once installed, `crate-doctor` and `crate_doctor` both work as the command. Running the
file directly is `python3 crate_doctor.py` — Python filenames can't have hyphens.

---

## Configure it once

```bash
crate-doctor init
```

Writes `crate_doctor.ini` next to you. Everything that's a matter of taste lives there, so
you're not retyping flags or editing code:

```ini
[cues]
floor_bars = auto          ; measure my own habit and use that
target = 10
outro = 20-36
tag = CUE(script)

[rename]
pattern = {artist} - {title}

[tags]
min_pct = 3
max_pct = 25

[tags.audio]
; your own tags, from your own waveforms, at your own cut points
Basement = kick_mean      >= 0.90
Airy     = brightness     >= 0.90
Patient  = breakdown_frac >= 0.70
```

That `[tags.audio]` section is the one to play with — invent whatever tags you want,
pick the field and the percentile, and crate_doctor computes the threshold against your
library. The five shipped ones are just a starting point.

Every command reads the config from the folder you run in, then next to crate_doctor, then
your user config folder. **Command-line flags always win over the file.**

---

## Before you touch anything

**Quit Rekordbox.** Not just the window — its background helper (`rekordboxAgent` on
macOS) keeps writing for a while after the app closes, and writing to `master.db` while it
holds the file can corrupt your library.

One thing that trips everyone up: **a large `master.db-wal` file after you quit Rekordbox
is normal.** Rekordbox doesn't flush it on exit, so a big `-wal` does *not* mean Rekordbox
is still running. The honest test is whether a checkpoint returns `busy=0` — which is what
crate_doctor does instead of guessing from file sizes.

Every `--write` takes a timestamped backup, makes its changes on a private copy, runs
`PRAGMA quick_check` and a foreign-key check on that copy, and only then replaces your live
database. Deletions are **soft** — the row is flagged hidden, not destroyed.

Rekordbox's bundled Sampler one-shots are excluded from every count. They're not tracks and
they wreck every percentage.

---

## Suggested first run

```bash
python crate_doctor.py backup
python crate_doctor.py cues
python crate_doctor.py tags
python crate_doctor.py sound -o sound.jsonl              # ~5 min for 3000 tracks
python crate_doctor.py playlists --sound sound.jsonl
python crate_doctor.py dupes
```

All read-only. None of it can change anything. Add `--write` only once a dry run looks
right to you.

---

## The commands

### `cues` — what is my actual cueing habit, and fix it

```bash
python crate_doctor.py cues                                              # just look
python crate_doctor.py cues --fix --tag "CUE(script)" --target 10        # dry run
python crate_doctor.py cues --fix --tag "CUE(script)" --target 10 --write
```

A cue 15 seconds from the end of a track is worthless — there's nothing left to mix into.

**Without `--fix`** it measures how much music you leave after your **last** cue, across
every track you've cued, and shows the distribution:

```
  bars of music after the LAST cue: p5 16.0   median 29.1   p90 40.0
  cues per track                  : p10 8   median 10   p90 10

  => your effective floor is about 16 bars
```

That floor is yours, computed from your own work. Since it's your 5th percentile, ~5% of
tracks always sit below it by construction — read the list as outliers to eyeball, not a
to-do list.

**With `--fix`** it repairs them, using the floor it just measured unless you name your
own. Two passes: first it removes any cue with less than the floor of music after it. Then,
with `--target N`, it tops each track back up to N cues — placing the **final** cue on the
biggest drop in kick energy in a window near the end, because that's where the outro
actually begins and where you want to be cued to mix out. Not in the silence after it.

The rest go on energy changes and Rekordbox's own phrase boundaries, never closer than
`--min-space` bars. It never exceeds your target and never removes the opening downbeat.

**`--fix` refuses to run without a scope**, deliberately. No tool can tell your hand-placed
cues from a script's, so it makes you say which you mean.

| flag | |
|---|---|
| `--fix` | repair, don't just report |
| `--tag NAME` | only cues with this comment; new cues get it too |
| `--all` | every cue, *including ones you set by hand* |
| `--floor-bars N` | override your measured floor |
| `--target N` | top back up to N cues. **Omit and it only deletes, never adds** |
| `--min-space N` | min bars between cues (default 8) |
| `--outro-lo N` / `--outro-hi N` | window for the final cue, bars from the end (default 20–36) |
| `--write` | apply it |

If `--outro-lo` ends up inside your floor it moves the window out and says so, otherwise
the two passes fight each other forever.

### `sound` — numbers for every track

```bash
python crate_doctor.py sound -o sound.jsonl
```

Per track: `brightness`, `kick_mean`, `breakdown_frac` (share of the track sitting well
below its own kick level — intros and breakdowns), `loud_frac`, `bpm`, `bars`, `rating`.
JSONL, one object per line. Everything is **relative to the track itself**, which is what
you actually care about.

`tags` and `playlists` take `--sound sound.jsonl` and do more with it.

If you go digging yourself: it prefers the **PWV7** tag in the `.2EX` file over **PWV5** in
the `.EXT`. Same 150 samples/second timeline, but PWV7 gives one *byte* per frequency band
where PWV5 packs each into 3 bits — and PWV5 clips hard, with the low band pinned at
maximum roughly half the time on a bass-heavy track. PWV7 never hits its ceiling. Sixteen
times the resolution, same files you already have.

### `tags` — do my My Tags actually do anything?

```bash
python crate_doctor.py tags
python crate_doctor.py tags --propose --sound sound.jsonl
```

Most people's tag setups are useless, and it's always the same reason: the tags don't split
the collection. A tag on 60% of your library tells you nothing when you search it; a tag on
zero tracks is clutter.

The rule: **a tag earns its place by covering 3–25% of your library.** `tags` sorts yours
into *too broad*, *dead*, *thin* and *ok*, so you can see which are pulling weight.

`--propose` then suggests tags from `knowledge.json` — 316 record labels mapped to
sub-genres and moods, plus artists, genres and title patterns — and, if you give it
`--sound`, five more from your waveforms:

| tag | from | cut at |
|---|---|---|
| Dark | brightness | bottom 22% of your library |
| Vibrant | brightness | top 20% |
| Thumping | kick weight | top 20% |
| Atmospheric | breakdown share | top 20% |
| Chill | loudness share | bottom 18% |

Those thresholds are percentiles of *your* collection, so "Dark" means dark relative to
what you actually play.

Every proposed tag is shown with the share of your library it would land on, so you can see
before writing anything whether it would narrow or not. It won't create a tag flagged too
broad unless you pass `--force`.

| flag | |
|---|---|
| `--propose` | suggest tags, not just audit |
| `--sound F` | unlocks the five waveform Vibe tags |
| `--min-pct` / `--max-pct` | the band a tag must land in (default 3 / 25) |
| `--min-confidence 1..3` | how sure the knowledge base must be (default 2) |
| `--create-tags` | also create proposed tags you don't have yet |
| `-o plan.json` | save the proposal |
| `--write` | apply it |

**The knowledge base is the part worth editing.** `--propose` prints the labels in your
collection it doesn't recognise, ranked by track count. Add those to `knowledge.json` in
the same shape and re-run — that's where the accuracy lives. `PROMPTS.md` covers how.

### `playlists` — from data you already have

```bash
python crate_doctor.py playlists --sound sound.jsonl
python crate_doctor.py playlists --sound sound.jsonl --write
```

- **Never Played 4+** — rated 4★ or better, never once played out
- **Dusty Bangers** — 4★+, played before, nothing in a year
- **Workhorses** / **Underrated Workhorses** — your most-played, and the ones among them you've rated 3★ or lower
- **The Turn** — biggest swing between the quiet part and the loud part
- **Sunrise** — bright, breakdown-heavy, mid tempo
- **Cold Opens** — long run-up before the kick lands
- **Peak Time** — heavy kick, loud, few breakdowns

It skips any name you already have, and puts new ones in a folder (`--folder`).

| flag | |
|---|---|
| `--sound F` | unlocks the waveform playlists |
| `--folder NAME` | where to create them |
| `--stale-days N` | what "dusty" means (default 365) |
| `--workhorse-plays N` | plays that make a workhorse (default 10) |
| `--write` | create them |

### `rename` — one naming standard for every file

```bash
python crate_doctor.py rename                  # dry run, shows every old -> new
python crate_doctor.py rename --write
```

Renames the audio files on disk to match the metadata already in your library, and
updates the database in the same operation so nothing gets orphaned. Default pattern is
`{artist} - {title}` — Rekordbox usually already carries the mix inside the title, so
`Bicep - Glue (Original Mix).aiff` comes out right without asking for it.

It strips the characters Windows refuses (`< > : " / \ | ? *`) plus trailing dots and
spaces, so the result works on a USB stick and not just on your Mac.

**It will not overwrite anything.** If two tracks want the same filename, or the name is
already taken on disk, it skips and tells you. Fix the metadata and run it again.

**If the database write fails, every file is renamed back** before the tool exits. You
never end up with files renamed on disk and a library pointing at the old names — which
is the one way a rename tool can genuinely ruin your afternoon.

Playlists, cues, tags, ratings and play counts are keyed to the track, not the filename,
so all of that survives untouched.

| flag | |
|---|---|
| `--pattern` | fields are `{artist}` `{title}` `{remixer}` `{mix}` |
| `--limit N` | examples to print (default 15) |
| `--write` | actually rename |

### `relocate` — files moved or got renamed

```bash
python crate_doctor.py relocate --music-root /Volumes/T7/Music          # dry run
python crate_doctor.py relocate --music-root /Volumes/T7/Music --write
```

Rekordbox's Relocate, in bulk, for every broken entry at once. Matches by **exact byte
size** first — that's conclusive, not a guess — then by name with case and punctuation
ignored, so `Internova␣␣(Original Mix)` finds `Internova (Original Mix)`. Add `--fuzzy` to
also accept a single close-name match whose size is within 2%.

It changes only the database. Your files are never touched, and cues, tags, ratings,
playlists and play counts all survive because they key on the track, not the filename.

| flag | |
|---|---|
| `--music-root PATH` | where to search (repeatable) |
| `--fuzzy` | also accept a single close-name match |
| `--path-as A=B` | store paths as `B` when you're searching under `A` |
| `--write` | apply it |

`--path-as` only matters if you're reaching the drive through a mount, a VM, or a remote
session — in that case the path you find a file at isn't the path Rekordbox can open, and
storing the wrong one breaks the entry. The dry run's `->` line always shows the path that
will actually be stored. Check it.

### `dupes`

```bash
python crate_doctor.py dupes
```

Two groups: same filename **and** byte size (almost certainly the same file twice), and
same title/BPM/length but different files (could be a duplicate, could be a remix pack).
Changes nothing. Before removing either copy, check which one your playlists and history
point at — the newer file is often the one with no cues and no play count.

### `disk` — what's on disk that my library doesn't know about

```bash
python crate_doctor.py disk --music-root /Volumes/T7/Music --music-root ~/Music
```

Orphans (grouped by folder with sizes) and entries whose file has gone missing. This one is
fussier than it looks, and the fussiness is the point:

- matches on `FileNameL`, not the folder path's basename — cloud-synced entries store a **truncated** path
- normalises Unicode both ways — macOS stores filenames decomposed, the database stores them composed, and skipping this makes every track with an accent look orphaned
- ignores the `._name` sidecars macOS writes on exFAT drives, which otherwise double your file count
- matches on name **and** size
- refuses to report anything if a root you passed doesn't exist or has no audio in it, because an unplugged drive looks exactly like a library where every file vanished

**It never moves or deletes anything.** It prints lists. Move things yourself into a
`_to_delete` folder you can inspect first. A false positive here costs you a track.

### `backup`

Timestamped, self-contained copy of `master.db` (the WAL is folded in, so one file
restores everything).

**Put these on your external drive.** They default to sitting next to `master.db` on your
system drive and each one is about 130 MB. In `crate_doctor.ini`:

```ini
[paths]
backups = /Volumes/T7/rekordbox_backups
keep_backups = 5
```

Or per-run: `--backup-dir PATH` and `--keep-backups N` (`0` keeps everything). Older ones
are pruned automatically.

**To restore:** quit Rekordbox, copy the backup over `master.db`, delete the `-wal` and
`-shm` files next to it.

---

## Files

| | |
|---|---|
| `crate_doctor.py` | the whole tool |
| `knowledge.json` | 316 labels, 154 artists, 38 genres. Edit this |
| `crate_doctor.ini` | your settings — `setup` writes it with your paths filled in |
| `setup-mac.command` / `setup-windows.bat` / `setup-linux.sh` | double-click installers |
| `CLAUDE.md` | context for Claude or any AI working on your library |
| `INSTALL.md` | installing, on each OS |
| `PROMPTS.md` | doing the bespoke half with Claude on your own library |

---

## What it can't do

It can't hear the records. Everything it knows about the sound comes from Rekordbox's
waveform — three frequency bands at 150 samples a second. That's plenty for energy shape
and useless for whether a track is any good.

---

MIT licence. No warranty. It edits your library — back it up.
