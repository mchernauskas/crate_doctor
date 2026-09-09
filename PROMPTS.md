# Working on your library with Claude

> **Start here instead if your computer is linked to Claude:** just open this folder in the
> Claude desktop app and talk. `CLAUDE.md` gives Claude the full picture and it will drive
> crate_doctor for you. This file is for when you're pasting output into a normal Claude chat,
> or want to do the label research yourself.

`crate_doctor` handles the parts that are the same for every DJ: database safety, waveform
decoding, cue geometry, file matching. This file is for the other half — the parts that
depend on *your* collection and *your* taste, which no script can decide for you.

The short version: **crate_doctor gives Claude eyes on your library.** Run a command, paste
the output into Claude, and ask a real question about it.

---

## The setup

Give Claude the tool and one export to look at:

```bash
python crate_doctor.py sound -o sound.jsonl
python crate_doctor.py tags > tags.txt
```

Then, in Claude, attach `crate_doctor.py`, `knowledge.json`, `sound.jsonl` and `tags.txt`
and say roughly:

> This is my Rekordbox library exported with crate_doctor. `sound.jsonl` has per-track
> waveform features, `tags.txt` is my current My Tag setup. I want to [what you want].
> Look at the actual data before suggesting anything.

That last sentence matters more than it looks. Without it you get generic advice about
DJing. With it you get advice about *your* records.

---

## The four things worth doing

### 1. Fix the label knowledge base for your scene

This is where the accuracy actually lives, and it's the highest-value hour you can spend.

```bash
python crate_doctor.py tags --propose | grep -A20 "not in the knowledge base"
```

That gives you the labels in your collection that `knowledge.json` doesn't recognise,
ranked by how many tracks each covers. Paste that list into Claude:

> These are record labels in my collection that my knowledge base doesn't know. For each
> one, look it up and tell me what sub-genres and moods it actually releases. If a label
> is genuinely just generic melodic/tech house with no identity, say so rather than
> inventing a category — I'd rather have an honest blank than a wrong tag.

Push back if it gives you a hand-wavy answer for a label you know. Ask it to actually
search. Then add the results to `knowledge.json` in the same shape as the existing
entries: `"Label Name": [["Style","Tags"], ["Vibe","Tags"], confidence_1_to_3]`.

### 2. Design a tag vocabulary that actually narrows

Most people's My Tags are useless, and the reason is always the same: the tags don't
split the collection. `python crate_doctor.py tags` shows you which of yours do.

The rule that makes tags work: **a tag is worth having only if it covers roughly 3-25% of
your library.** Above 25% and searching it barely filters anything. At 0 it's clutter.

Paste your `tags.txt` into Claude and ask:

> Here's my current tag setup with coverage percentages. Which of these are earning their
> place and which should go? I care about being able to find a specific kind of record
> fast, not about describing every track completely.

Be prepared for the answer to be "delete most of them". That's usually correct. Combinations
do the work: `Raw` + `Hypnotic` finds a much more specific record than any single tag with
a fancier name, and two tags at 15% each beat ten tags at 2%.

### 3. Get playlists out of data you already have

`playlists` builds the obvious ones. For anything else, describe the *situation* rather than
the sound, and let Claude find the tracks:

> From sound.jsonl, find me tracks that would work for the first 30 minutes of a warm-up
> — something has to be moving but it can't peak. Show me BPM, rating and play count so I
> can sanity-check the picks.

Useful fields in `sound.jsonl`:

- `kick_mean` — how much low-end weight the track carries overall
- `breakdown_frac` — how much of the track sits well below its own kick level (intros, breakdowns)
- `loud_frac` — how much of it sits near its own ceiling
- `brightness` — high-frequency content, which tracks fairly well with "airy" vs "dark"
- `bars`, `bpm`, `length_s`, `rating`

Note that these are all **relative to the track itself**, not to an absolute scale. A
`breakdown_frac` of 0.4 means 40% of *that* track is quiet compared to *that* track's own
peaks — which is what you actually care about.

### 4. Audit the cue work

```bash
python crate_doctor.py cues > cues.txt
```

> Here's my cueing habit measured across my whole library. Is my instinct consistent, or
> am I doing something different on some tracks without realising it?

---

## Things worth telling Claude up front

These come from actually doing this, and each one prevents a specific mistake:

- **"Never delete anything without asking me first."** Say it explicitly. Then have it move
  files to a `_to_delete` folder you can inspect rather than removing them.
- **"Quit Rekordbox and rekordboxAgent before any write."** The agent keeps writing after
  the app window closes and will corrupt the database out from under you.
- **"Show me the plan before you apply it."** Every write should be a proposal you approve
  first. `crate_doctor` is built this way; ask Claude to work the same way.
- **"Don't touch the grids or cues on my old tracks."** If you've hand-gridded years of
  records, fence them off by name at the start.
- **"Everything is case by case."** Any threshold — 16 bars, 25%, 10 cues — is a starting
  point, not a law. Say so, or you'll get rigid answers.
- **Challenge numbers that smell wrong.** "114 tracks have no genre" turned out to be 36
  sample one-shots and 78 streaming entries — zero real songs. If a count surprises you,
  make it prove the count. It is often wrong in a way that's easy to check and easy to miss.

---

## Extending crate_doctor itself

It's one file plus one JSON. To add a command, attach `crate_doctor.py` and ask:

> Add a command that does [X]. Follow the existing pattern: read-only unless --write, use
> the SafeWrite context manager for any database change, and print a dry-run plan first.

Two things to insist on:

- **Test it against a copy of the database before you trust it.** Copy `master.db` to a
  scratch folder, symlink the `share` folder next to it, and run the write there first.
- **Watch for silent failures.** The most dangerous bug found while building this was a
  `RecursionError` swallowed by a broad `except`, which made every track report "no
  analysis data" — the tool looked like it ran fine and did nothing at all. If a command
  reports skipping everything, that's a bug, not a fact about your library.

---

## What this can't do

- It can't hear the records. Everything it knows about the sound comes from Rekordbox's
  waveform, which is three frequency bands at 150 samples a second. That's plenty for
  energy shape and useless for whether a track is any good.
- It can't tell your hand-placed cues from a script's. Tag anything a script writes.
- Label metadata in your library may just be wrong, and no amount of research fixes a
  track filed under the wrong label to begin with.
