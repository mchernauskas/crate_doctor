#!/usr/bin/env python3
"""
crate_doctor.py — audit and repair a Rekordbox library from the command line.

Works against any Rekordbox 6 or 7 install on macOS or Windows. Nothing here is
specific to one person's setup: the database is located by pyrekordbox, and every
threshold that could be a matter of taste is measured from YOUR OWN library rather
than hardcoded.

WHAT IT DOES
  backup     Timestamped copy of the database (+ its WAL). Do this first.
  cues       Measure your own cueing habit -- how much runway you actually leave
             after your last cue -- and list the tracks that break it. Add --fix to
             repair them: delete the dead-air cues and, optionally, top each track
             back up to N, placing the final cue on the outro's energy drop.
  sound      Turn the colour waveform Rekordbox already computed into per-track
             numbers: bass weight, brightness, breakdown share, loud share. No audio
             decoding. About 10 tracks a second. Other commands read this file.
  tags       Audit whether your My Tags actually narrow your library, and propose
             new ones from the shipped label/artist/genre knowledge base.
  playlists  Build playlists from things you already have: ratings, play counts,
             play history, and the waveform.
  relocate   Repoint entries whose file moved or got renamed -- Rekordbox's Relocate,
             in bulk, matching on exact byte size.
  dupes      Find the same track in your library twice.
  disk       Compare your drive against your library: audio files nothing
             references, and library entries whose file has gone missing.

INSTALL
    pip install pyrekordbox numpy
    python -m pyrekordbox download-key      # one time; fetches the DB decryption key

USAGE
    python crate_doctor.py backup
    python crate_doctor.py cues
    python crate_doctor.py cues --fix --tag "CUE(script)" --target 10 --write
    python crate_doctor.py sound -o sound.jsonl
    python crate_doctor.py tags --propose --sound sound.jsonl
    python crate_doctor.py playlists --sound sound.jsonl
    python crate_doctor.py disk --music-root /Volumes/T7/Music

SAFETY — read this bit
  * Every command is read-only unless you pass --write.
  * QUIT REKORDBOX FIRST. Its background helper (rekordboxAgent on macOS) keeps
    writing to the database for a while after the app window closes. Writing while
    it holds the file can corrupt master.db. This tool checks, but check yourself.
  * --write always takes a backup first, edits a private copy, verifies integrity
    with PRAGMA quick_check, and only then replaces the live file.
  * Deletions are SOFT (rb_local_deleted=1). Rekordbox hides those rows; the data is
    still there and the backup is a full copy. Nothing is destroyed.

A note on the WAL, because it trips people up: a non-empty master.db-wal after you
quit Rekordbox is normal — Rekordbox does not checkpoint on exit. So "-wal is big"
does NOT mean Rekordbox is running. The reliable test is whether a checkpoint
returns busy=0, which is what this tool does.

MIT licence. No warranty. Back up your library.
"""

import argparse, collections, configparser, datetime, json, logging, os, re, shutil, statistics, sys, unicodedata, uuid

# Dependencies are imported lazily so that `crate_doctor setup` can run on a bare Python and
# install them for you. Every other command calls require_deps() first.
np = Rekordbox6Database = tables = AnlzFile = None
MISSING = []
try:
    import numpy as np
except ImportError:
    MISSING.append('numpy')
try:
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.db6 import tables
    from pyrekordbox.anlz import AnlzFile
    logging.getLogger('pyrekordbox').setLevel(logging.ERROR)
except ImportError:
    MISSING.append('pyrekordbox')


def py_cmd():
    """What to type to get Python on this OS. Short form for instructions; the real
    interpreter path is used when we actually spawn things."""
    return 'py' if sys.platform == 'win32' else 'python3'


def require_deps():
    if MISSING:
        sys.exit(f"missing: {', '.join(MISSING)}\n"
                 f"  Easiest:  {py_cmd()} crate_doctor.py setup      (installs them for you)\n"
                 f"  By hand:  {py_cmd()} -m pip install {' '.join(MISSING)}")

AUDIO_EXT = ('.aiff', '.aif', '.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg')


# ---------------------------------------------------------------- db plumbing

def candidate_db_paths():
    """Every place a Rekordbox 6/7 master.db is known to live, most likely first."""
    home = os.path.expanduser('~')
    out = []
    if sys.platform == 'win32':
        for base in (os.environ.get('APPDATA'), os.environ.get('LOCALAPPDATA'),
                     os.path.join(home, 'AppData', 'Roaming')):
            if base:
                out.append(os.path.join(base, 'Pioneer', 'rekordbox', 'master.db'))
    elif sys.platform == 'darwin':
        out.append(os.path.join(home, 'Library', 'Pioneer', 'rekordbox', 'master.db'))
    else:
        # Rekordbox does not run on Linux, but people copy libraries around.
        out += [os.path.join(home, 'Library', 'Pioneer', 'rekordbox', 'master.db'),
                os.path.join(home, '.Pioneer', 'rekordbox', 'master.db')]
    out.append(os.path.join(os.getcwd(), 'master.db'))
    return out


def find_db():
    """Return the path to master.db or None. Never exits."""
    if Rekordbox6Database is not None:
        try:
            from pyrekordbox.config import get_config
            for app in ('rekordbox7', 'rekordbox6'):
                try:
                    p = get_config(app, 'db_path')
                    if p and os.path.exists(p):
                        return str(p)
                except Exception:
                    pass
        except Exception:
            pass
    for p in candidate_db_paths():
        if os.path.exists(p):
            return p
    return None


def db_paths(explicit=None):
    """Locate master.db, or explain exactly where we looked."""
    require_deps()
    if explicit:
        p = os.path.abspath(os.path.expanduser(explicit))
        if not os.path.exists(p):
            sys.exit(f"no database at {p}")
        return p
    p = find_db()
    if p:
        return p
    sys.exit(
        "could not find master.db. Looked in:\n  " + "\n  ".join(candidate_db_paths()) +
        "\n\nIf Rekordbox is installed somewhere unusual, pass --db /path/to/master.db\n"
        "or run `crate_doctor setup` and it will ask you.\n"
        "If Rekordbox has never been opened on this machine, there is no library yet.")


def open_ro(path):
    require_deps()
    return Rekordbox6Database(path=path, db_dir=os.path.dirname(path))


def checkpoint_live(path):
    """Merge the WAL into master.db. Returns True if nothing else holds the file.

    A busy value of 0 means no other process has a lock, which is the honest test
    for 'is Rekordbox really closed', far better than looking at the WAL's size.
    """
    wal = path + '-wal'
    if not os.path.exists(wal) or os.path.getsize(wal) == 0:
        return True
    # Use pyrekordbox's own connection so the cipher settings match.
    db = Rekordbox6Database(path=path, db_dir=os.path.dirname(path))
    try:
        res = db.engine.raw_connection().driver_connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        busy = res[0][0] if res and res[0] else 1
        return busy == 0
    finally:
        try:
            db.close()
        except Exception:
            pass


def rekordbox_processes():
    """Names of Rekordbox processes currently running, or [] if none.

    This is the check the WAL test cannot make: Rekordbox can be open with an empty
    WAL, and then nothing about the database files says so. Matched by exact process
    name, never by path -- a music folder called 'rekordbox' would match itself.
    """
    import subprocess
    names = ['rekordbox', 'rekordboxAgent', 'rekordbox.exe', 'rekordboxAgent.exe']
    found = []
    try:
        if sys.platform == 'win32':
            out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                                 text=True, timeout=10).stdout.lower()
            found = [n for n in names if n.endswith('.exe') and f'"{n.lower()}"' in out]
        else:
            for n in names[:2]:
                r = subprocess.run(['pgrep', '-x', n], capture_output=True, text=True, timeout=10)
                if r.returncode == 0 and r.stdout.strip():
                    found.append(n)
    except Exception:
        pass            # no pgrep/tasklist: fall through to the WAL test alone
    return found


BACKUP_DIR = None       # set from config / --backup-dir; None = beside the database


def backup(path, tag='backup', dest=None):
    """Timestamped copy. Goes beside the database unless you point it elsewhere --
    worth doing, because master.db lives on your system drive and these are ~130 MB
    each. An external drive is the better home for them."""
    want = dest or BACKUP_DIR
    fallback = os.path.join(os.path.dirname(path), 'crate_doctor_backups')
    d = os.path.abspath(os.path.expanduser(want or fallback))
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        if not want:
            sys.exit(f"cannot create a backup folder at {d}: {e}")
        # the configured drive is unplugged or unwritable -- say so and use the default
        # rather than dying, because the caller is usually about to write to the library
        print(f"  !  cannot write backups to {d} ({e.strerror}).\n"
              f"     Is that drive plugged in? Falling back to {fallback}")
        d = os.path.abspath(fallback)
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(d, f'master_{tag}_{stamp}.db')
    shutil.copy2(path, dst)
    n = 1
    for ext in ('-wal', '-shm'):
        if os.path.exists(path + ext) and os.path.getsize(path + ext) > 0:
            shutil.copy2(path + ext, dst + ext)
            n += 1
    _prune_backups(d)
    if n > 1:
        # Fold the copied WAL into the copied .db so the backup is ONE self-contained
        # file. Restoring just the .db would otherwise silently lose whatever Rekordbox
        # wrote in its last session. This touches only the copy, never the live database.
        try:
            b = Rekordbox6Database(path=dst, db_dir=os.path.dirname(path))
            b.engine.raw_connection().driver_connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)")
            b.close()
            for ext in ('-wal', '-shm'):
                if os.path.exists(dst + ext):
                    os.remove(dst + ext)
            n = 1
        except Exception:
            pass        # leave both files; they restore together
    return dst, n


KEEP_BACKUPS = 5


def _prune_backups(d):
    """Keep the newest KEEP_BACKUPS. These are ~130 MB each; ten of them is a gigabyte
    of copies of a file that mostly did not change."""
    if not KEEP_BACKUPS:
        return
    # Sort on the timestamp in the NAME, not the file's mtime: shutil.copy2 preserves
    # the source's mtime, so every backup would claim the same age and the prune would
    # delete the wrong ones.
    def stamp(f):
        mm = re.search(r'(\d{8}_\d{6})\.db$', f)
        return mm.group(1) if mm else ''
    try:
        files = sorted((f for f in os.listdir(d)
                        if f.startswith('master_') and f.endswith('.db') and stamp(f)),
                       key=stamp, reverse=True)
    except OSError:
        return
    for f in files[KEEP_BACKUPS:]:
        for ext in ('', '-wal', '-shm'):
            p = os.path.join(d, f + ext)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


class SafeWrite:
    """Edit a private copy, verify it, then replace the live database."""

    def __init__(self, live):
        self.live = live
        # Unique per run. A fixed name breaks if a stale copy is left behind on a
        # filesystem that will not let us delete it (sandboxes, some network shares).
        self.work = os.path.join(os.path.dirname(live),
                                 f'.crate_doctor_work_{os.getpid()}_{int(datetime.datetime.now().timestamp())}.db')

    def __enter__(self):
        procs = rekordbox_processes()
        if procs:
            sys.exit(f"REFUSING to write: {', '.join(procs)} is running. "
                     "Quit Rekordbox fully -- the agent too -- and try again.")
        wal = self.live + '-wal'
        if os.path.exists(wal) and os.path.getsize(wal) > 0:
            if not checkpoint_live(self.live):
                sys.exit("REFUSING to write: another process holds master.db. "
                         "Quit Rekordbox (and rekordboxAgent) and try again.")
        for ext in ('', '-wal', '-shm'):
            p = self.work + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    open(p, 'wb').close()      # cannot delete: empty it instead
        shutil.copy2(self.live, self.work)
        self.db = Rekordbox6Database(path=self.work, db_dir=os.path.dirname(self.live))
        return self.db

    def __exit__(self, et, ev, tb):
        if et is not None:
            try:
                self.db.close()
            except Exception:
                pass
            return False
        self.db.commit()
        try:
            self.db.close()
        except Exception:
            pass
        chk = Rekordbox6Database(path=self.work, db_dir=os.path.dirname(self.live))
        raw = chk.engine.raw_connection().driver_connection
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        ok = raw.execute("PRAGMA quick_check").fetchall()
        fk = raw.execute("PRAGMA foreign_key_check").fetchall()
        chk.close()
        if ok != [('ok',)] or fk:
            sys.exit(f"integrity check FAILED on the edited copy ({ok}, {len(fk)} fk problems). "
                     f"Live database untouched. Working copy left at {self.work}")
        wal = self.live + '-wal'
        if os.path.exists(wal) and os.path.getsize(wal) > 0:
            sys.exit("live -wal grew while working; aborting. Nothing was written.")
        shutil.copy2(self.work, self.live)
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.live + ext):
                open(self.live + ext, 'wb').close()
        for ext in ('', '-wal', '-shm'):
            p = self.work + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    open(p, 'wb').close()
                    print(f"  note: could not delete the working copy {p} -- emptied it "
                          f"instead. Safe to remove by hand.")
        print("  integrity ok, changes written")
        return False


# ---------------------------------------------------------------- helpers

def nfc(s):
    """macOS stores filenames decomposed, the database stores them composed.
    Compare them without this and every track with an accent looks like an orphan."""
    return unicodedata.normalize('NFC', s or '').lower().strip()


def real_tracks(db, skip_streaming=True):
    for r in db.query(tables.DjmdContent).all():
        if r.rb_local_deleted:
            continue
        fp = r.FolderPath or ''
        if not fp:
            continue
        if skip_streaming and ('soundcloud' in fp or 'tidal' in fp or 'beatport:' in fp):
            continue
        # Rekordbox's bundled Sampler one-shots are not tracks. They are short, they
        # come in hundreds, and they skew every count and percentage in this tool.
        if '/rekordbox/Sampler/' in fp or '\\rekordbox\\Sampler\\' in fp:
            continue
        yield r


def memory_cues(db):
    out = collections.defaultdict(list)
    for c in db.query(tables.DjmdCue).all():
        if c.rb_local_deleted or c.Kind != 0:
            continue
        out[str(c.ContentID)].append(c)
    return out


def beat_bars(dat):
    """Bar-start beat indices and their times in ms, from the stored beat grid."""
    ents = dat.get_tag('PQTZ').content.entries
    t = np.array([e.time for e in ents], float)
    bars = [i for i, e in enumerate(ents) if e.beat == 1]
    return t, bars


def energy_per_bar(ext, ex2, t, bars, length_s):
    """Kick-energy per bar from Rekordbox's own colour waveform. No audio decoding.

    The waveform is 150 samples/second. PWV7 (in the .2EX file) stores one BYTE per
    frequency band — lows, mids, highs — so 0..255 per band. PWV5 (.EXT) packs the
    same three bands into 3 bits each plus a 5-bit height, which clips badly: on a
    bass-heavy track the low band sits pinned at its maximum around half the time.
    Prefer PWV7 when it is there.
    """
    lows = None
    if ex2 is not None:
        try:
            raw = ex2.get_tag('PWV7').content.entries
            a = np.frombuffer(bytes(raw), dtype=np.uint8)
            a = a[:len(a) // 3 * 3].reshape(-1, 3).astype(float)
            lows, amp = a[:, 0], a.sum(1)
        except Exception:
            lows = None
    if lows is None:
        w = np.array(ext.get_tag('PWV5').content.entries, dtype=np.uint16)
        lows = ((w >> 13) & 7).astype(float)
        amp = ((w >> 2) & 31).astype(float)
    kick = lows * amp
    rate = len(kick) / (length_s * 1000.0)
    per = []
    for k in range(len(bars)):
        a = int(t[bars[k]] * rate)
        b = int((t[bars[k + 1]] if k + 1 < len(bars) else length_s * 1000) * rate)
        per.append(float(kick[a:b].mean()) if b > a else 0.0)
    per = np.array(per)
    return per / (np.percentile(per, 90) + 1e-9), amp


def anlz(db, r):
    p = db.get_anlz_paths(r)
    dat = AnlzFile.parse_file(p['DAT']) if p.get('DAT') and os.path.exists(p['DAT']) else None
    ext = AnlzFile.parse_file(p['EXT']) if p.get('EXT') and os.path.exists(p['EXT']) else None
    ex2 = None
    if p.get('2EX') and os.path.exists(p['2EX']):
        try:
            ex2 = AnlzFile.parse_file(p['2EX'])
        except Exception:
            ex2 = None
    return dat, ext, ex2


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else 0


# ---------------------------------------------------------------- cues

def cmd_cues(a):
    if a.fix and not a.tag and not a.all:
        sys.exit(
            "refusing to --fix without a scope.\n"
            "  Cues you placed by hand are your work and this tool cannot tell them apart\n"
            "  from ones a script placed. Pick one:\n"
            "    --tag 'CUE(script)'   only cues whose Comment is exactly this\n"
            "    --all                 every cue in the library, hand-set ones included\n"
            "  Or drop --fix to just look at the numbers.")
    path = db_paths(a.db)
    db = open_ro(path)
    cues = memory_cues(db)
    mine, theirs = [], []
    for r in real_tracks(db):
        cid = str(r.ID)
        cs = cues.get(cid)
        if not cs or not r.Length or not r.BPM:
            continue
        bpm = r.BPM / 100.0
        last = max((c.InMsec or 0) / 1000.0 for c in cs)
        tail_bars = (r.Length - last) * bpm / 240.0
        if tail_bars < 0:
            continue
        row = (tail_bars, r.Length - last, len(cs), cid, r)
        (mine if a.tag and any((c.Comment or '') == a.tag for c in cs) else theirs).append(row)

    ref = theirs if len(theirs) >= 30 else (theirs + mine)
    if not ref:
        sys.exit("no cued tracks found")
    tb = [x[0] for x in ref]
    counts = [x[2] for x in ref]
    floor = pct(tb, 0.05)
    print(f"\nYour cueing habit, measured from {len(ref)} tracks"
          + (f" you cued by hand (excluding {a.tag})" if a.tag and theirs else "") + ":")
    print(f"  bars of music after the LAST cue: p5 {pct(tb,.05):.1f}   median {pct(tb,.5):.1f}   p90 {pct(tb,.9):.1f}")
    print(f"  cues per track                  : p10 {pct(counts,.1)}   median {pct(counts,.5)}   p90 {pct(counts,.9)}")
    print(f"\n  => your effective floor is about {floor:.0f} bars "
          f"(5th percentile — 95% of your tracks leave at least this much)")
    if not a.floor_bars:
        print("     Note: since the floor IS your 5th percentile, roughly 5% of tracks will\n"
              "     always sit below it. Read the list as outliers to eyeball, not a to-do list.\n"
              "     Pass --floor-bars N to test a number you actually believe in.")

    thresh = a.floor_bars if a.floor_bars else max(8.0, round(floor))
    pool = mine if (a.tag and mine) else ref
    bad = sorted([x for x in pool if x[0] < thresh])
    label = f"tagged {a.tag}" if (a.tag and mine) else "all"
    print(f"\nTracks ({label}) whose last cue leaves less than {thresh:.0f} bars: "
          f"{len(bad)} of {len(pool)} ({100*len(bad)/max(1,len(pool)):.0f}%)")
    for tb_, secs, n, cid, r in bad[:a.limit]:
        print(f"   {secs:5.0f}s left ({tb_:4.1f} bars)  {n} cues   {(r.Title or '?')[:52]}")
    if len(bad) > a.limit:
        print(f"   ... and {len(bad)-a.limit} more")
    if not a.fix:
        if bad:
            print(f"\nTo fix them:  python {os.path.basename(sys.argv[0])} cues --fix "
                  f"--tag YOUR_TAG --target {pct(counts,.5)}"
                  + ("  --write" if a.write else ""))
        return

    # --fix: repair, using the floor measured above unless you named your own
    if not a.floor_bars:
        a.floor_bars = float(thresh)
        print(f"\nUsing your measured floor of {a.floor_bars:.0f} bars.")
    fix_cues(a)


def outro_cue(kb, bars, endbar, have, lo, hi, phrase_bars, minspace):
    """Best final-cue position: the biggest fall in kick energy inside the window
    lo..hi bars from the end. That is where the outro actually starts, which is
    where you want to be cued to mix out — not in the dead air after it."""
    best = None
    for k in range(max(1, endbar - hi), endbar - lo + 1):
        if any(abs(k - h) < minspace for h in have):
            continue
        before = kb[max(0, k - 4):k].mean() if k > 0 else 0
        after = kb[k:k + 4].mean() if k < len(kb) else 0
        score = (before - after) + (0.5 if k in phrase_bars else 0.0)
        if best is None or score > best[1]:
            best = (k, score, before - after)
    return best if (best and best[1] > 0.05) else None


def fix_cues(a):
    if not a.tag and not a.all:
        sys.exit(
            "refusing to run without a scope.\n"
            "  Cues you placed by hand are your work and this tool cannot tell them apart\n"
            "  from ones a script placed. Pick one:\n"
            "    --tag 'CUE(script)'   only cues whose Comment is exactly this\n"
            "    --all                 every cue in the library, hand-set ones included\n")
    path = db_paths(a.db)
    # The outro window must sit outside the floor, or the top-up puts a cue back
    # exactly where the delete pass just took one out and the two fight forever.
    if a.outro_lo < a.floor_bars:
        lo = int(a.floor_bars)
        hi = max(a.outro_hi, lo + 16)
        print(f"note: --outro-lo {a.outro_lo} is inside your {a.floor_bars:.0f}-bar floor, so the "
              f"final cue would land in space you just cleared.\n"
              f"      Moving the outro window to {lo}-{hi} bars from the end.")
        a.outro_lo, a.outro_hi = lo, hi
    dbro = open_ro(path)
    cues = memory_cues(dbro)
    floor, target, minspace = a.floor_bars, a.target, a.min_space
    plan_del, plan_add = collections.defaultdict(list), collections.defaultdict(list)
    skipped = 0

    errors = []
    for r in real_tracks(dbro):
        cid = str(r.ID)
        allc = cues.get(cid, [])
        # 'ours' is what this run may delete. 'allc' is what it must respect: with --tag,
        # a track can also carry hand-set cues, and those count toward the target and
        # toward spacing even though they are never touched.
        ours = [c for c in allc if not a.tag or (c.Comment or '') == a.tag]
        if not ours or not r.Length or not r.BPM:
            continue
        bpm = r.BPM / 100.0
        # 1. cues with too little runway
        for c in ours:
            t = (c.InMsec or 0) / 1000.0
            if (r.Length - t) * bpm / 240.0 < floor:
                plan_del[cid].append(c.InMsec or 0)
        if not target:
            continue
        # 2. top back up, outro cue first
        try:
            dat, ext, ex2 = anlz(dbro, r)
            if dat is None or ext is None:
                skipped += 1
                continue
            t, bars = beat_bars(dat)
            if len(bars) < 24:
                skipped += 1
                continue
            kb, _ = energy_per_bar(ext, ex2, t, bars, r.Length)
            endbar = len(bars) - 1
            barof = lambda ms: max([k for k in range(len(bars)) if t[bars[k]] <= ms] or [0])
            # everything that will still be on the track after the delete pass
            keep_all = [(c.InMsec or 0) for c in allc if (c.InMsec or 0) not in plan_del[cid]]
            # the subset of those this run is allowed to remove if we are over target
            keep_ours = [(c.InMsec or 0) for c in ours if (c.InMsec or 0) not in plan_del[cid]]
            keep = keep_all
            have = sorted(barof(m) for m in keep)
            ph = set()
            try:
                for e in ext.get_tag('PSSI').content.entries:
                    ph.add(barof(t[max(0, min(len(t) - 1, e.beat - 1))]))
            except Exception:
                pass
            picks = []
            if not any(endbar - a.outro_hi <= h <= endbar - a.outro_lo for h in have):
                b = outro_cue(kb, bars, endbar, have, a.outro_lo, a.outro_hi, ph, minspace)
                if b:
                    picks.append(b[0])
            cand = {}
            for k in range(2, endbar - int(floor)):
                d = kb[k:k + 4].mean() - kb[max(0, k - 4):k].mean()
                if abs(d) > 0.20:
                    cand[k] = 1.5 + min(abs(d), 1)
            for k in ph:
                if 1 <= k < endbar - floor:
                    cand[k] = max(cand.get(k, 0), 1.6)
            for k in range(16, max(17, endbar - int(floor)), 16):
                cand.setdefault(k, 0.6)
            for k, _sc in sorted(cand.items(), key=lambda x: -x[1]):
                if len(keep) + len(picks) >= target:
                    break
                if any(abs(k - h) < minspace for h in have):
                    continue
                if any(abs(k - k2) < minspace for k2 in picks):
                    continue
                picks.append(k)
            # never exceed the target: drop tail-most cues OF OURS to make room.
            # Hand-set cues are never candidates, so with --tag on a track that already
            # has more hand cues than the target, nothing is removed and nothing added.
            over = len(keep) + len(picks) - target
            if over > 0 and have:
                for m in sorted(keep_ours, reverse=True):
                    if over <= 0:
                        break
                    if barof(m) == min(have):
                        continue          # keep the opening downbeat
                    plan_del[cid].append(m)
                    over -= 1
            for k in picks:
                plan_add[cid].append(int(round(t[bars[k]])))
        except Exception as e:
            skipped += 1
            if len(errors) < 3:
                errors.append(f"{(r.Title or '?')[:40]}: {e.__class__.__name__}: {e}")

    plan_del = {k: v for k, v in plan_del.items() if v}
    plan_add = {k: v for k, v in plan_add.items() if v}
    ndel = sum(len(v) for v in plan_del.values())
    nadd = sum(len(v) for v in plan_add.values())
    touched = set(plan_del) | set(plan_add)
    print(f"\nfloor {floor} bars, target {target or 'n/a'} cues, min spacing {minspace} bars")
    print(f"  cues to remove : {ndel} across {len(plan_del)} tracks")
    print(f"  cues to add    : {nadd} across {len(plan_add)} tracks")
    print(f"  tracks touched : {len(touched)}"
          + (f"   ({skipped} skipped: no usable analysis data)" if skipped else ""))
    if errors:
        print("  first errors from skipped tracks (if these are ALL tracks, that is a bug, "
              "not your library):")
        for e in errors:
            print(f"     {e}")
    if not a.write:
        print("\n  dry run — nothing written. add --write to apply.")
        return
    if not touched:
        return
    dst, n = backup(path, 'before_cuefix')
    print(f"\n  backup: {dst}" + (f" (+{n-1} wal/shm)" if n > 1 else ""))
    now = datetime.datetime.now()
    with SafeWrite(path) as db:
        live = memory_cues(db)
        for cid, msl in plan_del.items():
            for ms in msl:
                for c in live.get(cid, []):
                    if a.tag and (c.Comment or '') != a.tag:
                        continue          # never delete a cue that is not ours
                    if (c.InMsec or 0) == ms and not c.rb_local_deleted:
                        c.rb_local_deleted = 1
                        c.rb_local_synced = 0
                        c.updated_at = now
                        break
        for cid, msl in plan_add.items():
            for ms in msl:
                db.add(tables.DjmdCue.create(
                    ID=str(uuid.uuid4()), ContentID=str(cid), InMsec=int(ms),
                    InFrame=0, InMpegFrame=0, InMpegAbs=0,
                    OutMsec=-1, OutFrame=0, OutMpegFrame=0, OutMpegAbs=0,
                    Kind=0, Color=-1, ColorTableIndex=0, ActiveLoop=0,
                    Comment=a.tag or '', BeatLoopSize=0, CueMicrosec=0,
                    InPointSeekInfo=None, OutPointSeekInfo=None, ContentUUID=None,
                    UUID=str(uuid.uuid4()), rb_data_status=0, rb_local_data_status=0,
                    rb_local_deleted=0, rb_local_synced=0,
                    created_at=now, updated_at=now))
    print(f"  removed {ndel}, added {nadd}")


# ---------------------------------------------------------------- features

def cmd_sound(a):
    path = db_paths(a.db)
    db = open_ro(path)
    out = open(a.out, 'w') if a.out else sys.stdout
    n = err = 0
    errors = []
    rows = list(real_tracks(db))
    if a.limit:
        rows = rows[:a.limit]
    total = len(rows)
    for i, r in enumerate(rows):
        if a.out and i % 100 == 0:
            print(f"  {i}/{total}", file=sys.stderr, flush=True)
        try:
            dat, ext, ex2 = anlz(db, r)
            if dat is None or ext is None or not r.Length:
                err += 1
                continue
            t, bars = beat_bars(dat)
            if len(bars) < 8:
                err += 1
                continue
            kb, amp = energy_per_bar(ext, ex2, t, bars, r.Length)
            hi, wave = None, 'pwv7'
            if ex2 is not None:
                try:
                    raw = np.frombuffer(bytes(ex2.get_tag('PWV7').content.entries), dtype=np.uint8)
                    a3 = raw[:len(raw) // 3 * 3].reshape(-1, 3).astype(float)
                    hi = float(a3[:, 2].mean())
                except Exception:
                    hi = None
            if hi is None:
                # PWV5's 3-bit bands are on a DIFFERENT scale from PWV7's bytes, so a
                # library that mixes the two cannot have its brightness values compared
                # across tracks. 'wave' records which one this row came from so the tag
                # and playlist commands can refuse to mix them.
                w = np.array(ext.get_tag('PWV5').content.entries, dtype=np.uint16)
                hi, wave = float(((w >> 7) & 7).mean()), 'pwv5'
            rec = {
                'id': str(r.ID), 'title': r.Title, 'bpm': (r.BPM or 0) / 100.0,
                'length_s': r.Length, 'rating': r.Rating or 0, 'wave': wave,
                'brightness': round(hi, 3),
                'kick_mean': round(float(kb.mean()), 4),
                'breakdown_frac': round(float((kb < 0.4 * np.percentile(kb, 90)).mean()), 3),
                'loud_frac': round(float((amp > 0.8 * np.percentile(amp, 90)).mean()), 3),
                'bars': len(bars),
            }
            out.write(json.dumps(rec) + '\n')
            n += 1
            if a.out and n % 250 == 0:
                print(f"  {n} tracks...", file=sys.stderr)
        except Exception as e:
            err += 1
            if len(errors) < 3:
                errors.append(f"{(r.Title or '?')[:40]}: {e.__class__.__name__}: {e}")
    if a.out:
        out.close()
        print(f"wrote {n} tracks to {a.out}" + (f" ({err} skipped)" if err else ""))
    if errors and (err > n or err > 20):
        print("  many tracks skipped. first errors (if this is every track, it is a bug, "
              "not your library):")
        for e in errors:
            print(f"     {e}")


# ---------------------------------------------------------------- files

def cmd_disk(a):
    path = db_paths(a.db)
    db = open_ro(path)
    roots = [os.path.abspath(os.path.expanduser(p)) for p in a.music_root]
    bad = [p for p in roots if not os.path.isdir(p)]
    if bad:
        sys.exit("not a folder: " + ", ".join(bad) +
                 "\n  If that drive is unplugged, plug it in — a folder that is not there\n"
                 "  looks exactly like a folder where every file has gone missing.")
    disk = {}
    for root in roots:
        for dp, dn, fn in os.walk(root):
            for f in fn:
                # macOS writes a "._name" sidecar for every file on exFAT/FAT volumes.
                # They match audio extensions. Count them and your numbers double.
                if f.startswith('._') or not f.lower().endswith(AUDIO_EXT):
                    continue
                p = os.path.join(dp, f)
                try:
                    disk[p] = (nfc(f), os.path.getsize(p))
                except OSError:
                    pass
    if roots and not disk:
        sys.exit("found no audio files under " + ", ".join(roots) +
                 "\n  Refusing to guess. Point --music-root at the folder your tracks are\n"
                 "  actually in, or every entry in your library will look 'missing'.")
    disk_names = {nm for nm, _sz in disk.values()}
    names, sizes, rows = set(), set(), 0
    missing = []
    for r in real_tracks(db):
        rows += 1
        # FileNameL holds the real filename. basename(FolderPath) can be TRUNCATED
        # on cloud-synced entries, so matching on it produces phantom orphans.
        fn = nfc(r.FileNameL or os.path.basename(r.FolderPath or ''))
        if fn:
            names.add(fn)
        if r.FileSize:
            sizes.add(int(r.FileSize))
        if not roots or not fn:
            continue
        # An entry counts as present if the file is at the path the database claims,
        # OR if a file of that name turned up anywhere under the folders you scanned.
        # Both halves matter: cloud-synced entries store a stub path that will never
        # exist on disk, while a file you moved by hand still exists under its name.
        if os.path.exists(r.FolderPath or ''):
            continue
        if fn not in disk_names:
            missing.append((r.FileNameL, r.FolderPath))
    orphans = [p for p, (nm, sz) in disk.items() if nm not in names and sz not in sizes]
    print(f"\nlibrary entries: {rows}")
    print(f"audio files found under {', '.join(roots) if roots else '(no --music-root given)'}: {len(disk)}")
    print(f"\nfiles on disk your library does NOT reference: {len(orphans)}"
          f"   ({sum(disk[p][1] for p in orphans)/2**30:.1f} GB)")
    byfolder = collections.Counter(os.path.dirname(p) for p in orphans)
    for k, v in byfolder.most_common(a.limit):
        print(f"   {v:5d}  {k}")
    print(f"\nlibrary entries whose file is missing from the folders you scanned: {len(missing)}")
    for fn, fp in missing[:a.limit]:
        print(f"   {(fn or '?')[:58]}")
    if len(missing) > a.limit:
        print(f"   ... and {len(missing)-a.limit} more")
    if missing:
        print("  (an entry counts as found if the file is at the path the database says,\n"
              "   or if a file of that name exists anywhere you scanned. So these are\n"
              "   entries whose file is genuinely not in the folders you gave — check you\n"
              "   passed every drive before you believe the number.)")
    print("\n  Nothing was moved or deleted. Review the lists, then move anything you")
    print("  don't want into a _to_delete folder yourself — matching files to a database")
    print("  is fiddlier than it looks, and a false positive here costs you a track.")


# ---------------------------------------------------------------- config

CONFIG_NAME = 'crate_doctor.ini'

DEFAULT_CONFIG = """\
# crate_doctor configuration. Every setting here is a matter of taste, which is why it
# lives in a file you own rather than in the code. Delete any line to fall back to
# the built-in default. A command-line flag always beats this file.

[paths]
# Leave db blank to let crate_doctor find Rekordbox itself.
#   macOS    ~/Library/Pioneer/rekordbox/master.db
#   Windows  %APPDATA%\\Pioneer\\rekordbox\\master.db
db =
# Folders holding your audio. One per line, indented. Used by `disk`.
music_roots =
# Where `sound` writes, and where `tags` and `playlists` look for it.
sound = sound.jsonl
# Where backups go. Leave blank and they land next to master.db -- which is on your
# system drive, and they are about 130 MB each. Point this at an external drive.
#   backups = /Volumes/MyDrive/rekordbox_backups
backups =
# How many to keep. Older ones are pruned automatically. 0 keeps everything.
keep_backups = 5

[cues]
# floor_bars = auto  measures YOUR habit and uses your own 5th percentile.
# Set a number if you would rather state it outright.
floor_bars = auto
# How many cues a track should end up with. 0 = only delete, never add.
target = 10
# Never put two cues closer together than this.
min_space = 8
# Where the last cue belongs: bars from the end of the track. The tool looks in
# this window for the biggest drop in kick energy, which is where the outro starts.
outro = 20-36
# Only touch cues whose comment is exactly this. Strongly recommended -- it is what
# keeps a script away from the cues you placed by hand.
tag =

[rename]
# Fields: {artist} {title} {remixer} {mix}
# Rekordbox usually already carries the mix inside the title, so {title} alone
# normally gives you "Glue (Original Mix)".
pattern = {artist} - {title}

[tags]
# A tag earns its place only if it narrows the library to somewhere in this band.
# Wider than max_pct and searching it barely filters anything.
min_pct = 3
max_pct = 25
# How sure the knowledge base has to be before a tag is proposed: 1, 2 or 3.
min_confidence = 2

[tags.audio]
# Tags derived from the waveform instead of from metadata.
#   name = field comparison percentile
# Fields: brightness, kick_mean, breakdown_frac, loud_frac
# The percentile is of YOUR library, so "Dark" means dark next to what you play,
# not dark on some absolute scale. Add, remove or rename these freely.
Dark        = brightness     <= 0.22
Vibrant     = brightness     >= 0.80
Thumping    = kick_mean      >= 0.80
Atmospheric = breakdown_frac >= 0.80
Chill       = loud_frac      <= 0.18

[playlists]
# Playlist folder to build into.
folder = crate_doctor
# "Dusty" means rated highly, played before, but not in this many days.
stale_days = 365
# Plays that make a track a workhorse.
workhorse_plays = 10
# BPM range the Sunrise list draws from.
sunrise_bpm = 119-127
"""


def find_config(explicit=None):
    if explicit:
        return explicit if os.path.exists(explicit) else sys.exit(f"no config at {explicit}")
    here = os.path.join(os.getcwd(), CONFIG_NAME)
    if os.path.exists(here):
        return here
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_NAME)
    if os.path.exists(beside):
        return beside
    # the usual per-user spot on each OS
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'),
                                                            'AppData', 'Roaming')
        home = os.path.join(appdata, 'crate_doctor')
    elif sys.platform == 'darwin':
        home = os.path.expanduser('~/Library/Application Support/crate_doctor')
    else:
        home = os.path.join(os.environ.get('XDG_CONFIG_HOME',
                                           os.path.expanduser('~/.config')), 'crate_doctor')
    p = os.path.join(home, CONFIG_NAME)
    return p if os.path.exists(p) else None


def read_config(explicit=None):
    """Returns {section: {key: value}}, or {} when there is no config file."""
    p = find_config(explicit)
    if not p:
        return {}, None
    cp = configparser.ConfigParser(interpolation=None, delimiters=('=',))
    cp.optionxform = str                       # keep tag names cased as written
    try:
        cp.read(p, encoding='utf-8')
    except configparser.Error as e:
        sys.exit(f"could not read {p}:\n  {e}")
    return {s: dict(cp.items(s)) for s in cp.sections()}, p


def _num(v, cast, field, default=None):
    v = (v or '').strip()
    if not v:
        return default
    try:
        return cast(v)
    except ValueError:
        sys.exit(f"config: {field} should be a number, got {v!r}")


def _range(v, field):
    v = (v or '').strip()
    if not v:
        return None
    m = re.match(r'^\s*([\d.]+)\s*-\s*([\d.]+)\s*$', v)
    if not m:
        sys.exit(f"config: {field} should look like 20-36, got {v!r}")
    return float(m.group(1)), float(m.group(2))


def config_defaults(cfg):
    """Turn the config file into argparse defaults, per subcommand."""
    d = collections.defaultdict(dict)
    paths, cues = cfg.get('paths', {}), cfg.get('cues', {})
    tags, pls = cfg.get('tags', {}), cfg.get('playlists', {})
    ren = cfg.get('rename', {})

    db = (paths.get('db') or '').strip()
    snd = (paths.get('sound') or '').strip()
    roots = [x.strip() for x in (paths.get('music_roots') or '').splitlines() if x.strip()]

    fb = (cues.get('floor_bars') or '').strip()
    if fb and fb.lower() != 'auto':
        d['cues']['floor_bars'] = _num(fb, float, 'cues.floor_bars')
    for k, cast in (('target', int), ('min_space', int)):
        if cues.get(k):
            d['cues'][k] = _num(cues[k], cast, 'cues.' + k)
    r = _range(cues.get('outro'), 'cues.outro')
    if r:
        d['cues']['outro_lo'], d['cues']['outro_hi'] = int(r[0]), int(r[1])
    if (cues.get('tag') or '').strip():
        d['cues']['tag'] = cues['tag'].strip()

    if ren.get('pattern'):
        d['rename']['pattern'] = ren['pattern'].strip()

    for k, cast in (('min_pct', float), ('max_pct', float), ('min_confidence', int)):
        if tags.get(k):
            d['tags'][k] = _num(tags[k], cast, 'tags.' + k)
    if pls.get('folder'):
        d['playlists']['folder'] = pls['folder'].strip()
    for k, cast in (('stale_days', int), ('workhorse_plays', int)):
        if pls.get(k):
            d['playlists'][k] = _num(pls[k], cast, 'playlists.' + k)
    r = _range(pls.get('sunrise_bpm'), 'playlists.sunrise_bpm')
    if r:
        d['playlists']['sunrise_lo'], d['playlists']['sunrise_hi'] = r

    if snd:
        d['sound']['out'] = snd
        d['tags']['sound'] = snd
        d['playlists']['sound'] = snd
    if roots:
        d['disk']['music_root'] = roots
        d['relocate']['music_root'] = roots
    if db:
        d['_global']['db'] = os.path.expanduser(db)
    bd = (paths.get('backups') or '').strip()
    if bd:
        d['_global']['backup_dir'] = os.path.expanduser(bd)
    kb = (paths.get('keep_backups') or '').strip()
    if kb:
        d['_global']['keep_backups'] = _num(kb, int, 'paths.keep_backups')
    return d


def audio_vibes(cfg):
    """[tags.audio] -> [(name, field, op, percentile)], falling back to the defaults."""
    sec = cfg.get('tags.audio')
    if not sec:
        return list(AUDIO_VIBES)
    fields = {'brightness', 'kick_mean', 'breakdown_frac', 'loud_frac'}
    out = []
    for name, spec in sec.items():
        m = re.match(r'^\s*(\w+)\s*(<=|>=)\s*([\d.]+)\s*$', spec or '')
        if not m:
            sys.exit(f"config: [tags.audio] {name} should look like "
                     f"'brightness <= 0.22', got {spec!r}")
        field, op, q = m.group(1), m.group(2), float(m.group(3))
        if field not in fields:
            sys.exit(f"config: [tags.audio] {name} uses unknown field {field!r}. "
                     f"Pick one of: {', '.join(sorted(fields))}")
        if not 0 < q < 1:
            sys.exit(f"config: [tags.audio] {name} percentile should be between 0 and 1, got {q}")
        out.append((name, field, op, q))
    return out


def cmd_init(a):
    dst = a.path or os.path.join(os.getcwd(), CONFIG_NAME)
    if os.path.exists(dst) and not a.force:
        sys.exit(f"{dst} already exists. Pass --force to overwrite it.")
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(DEFAULT_CONFIG)
    print(f"wrote {dst}\n\n"
          "  Open it and set what you care about -- the cue floor, the filename pattern,\n"
          "  your own waveform tags. Every command reads it from the folder you run in.\n"
          "  Command-line flags still win over the file.")

def load_sound(path, tracks_ids=None):
    """Read sound.jsonl. Returns {id: row} using only the dominant waveform format.

    PWV5 and PWV7 brightness values live on different scales. Percentile-based tags
    are rank-based, so mixing the two distributions produces nonsense: every PWV5 track
    would read as 'Dark' simply because its scale tops out lower. Keep the majority
    format and say plainly what was dropped.
    """
    rows = {}
    if not os.path.exists(path):
        print(f"  (no {path} yet -- run `sound -o {path}` first to unlock the waveform-based\n"
              f"   results. Continuing with ratings and play history only.)\n")
        return rows
    for ln in open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        d = json.loads(ln)
        if tracks_ids is not None and str(d.get('id')) not in tracks_ids:
            continue
        rows[str(d['id'])] = d
    by = collections.Counter(d.get('wave', 'pwv7') for d in rows.values())
    if len(by) > 1:
        keep = by.most_common(1)[0][0]
        dropped = sum(v for k, v in by.items() if k != keep)
        print(f"  note: {path} mixes waveform formats ({dict(by)}). Brightness is not\n"
              f"        comparable across them, so the {dropped} {'/'.join(k for k in by if k != keep)} "
              f"tracks are left out of the waveform-based results.\n"
              f"        Re-analyze those tracks in Rekordbox 7 to bring them in.")
        rows = {k: v for k, v in rows.items() if v.get('wave', 'pwv7') == keep}
    return rows




# ---------------------------------------------------------------- relocate

def loose(s):
    """Name key for matching: accents folded, case dropped, runs of spaces and
    punctuation collapsed. 'Internova  (Original Mix)' == 'Internova (Original Mix)',
    and "TWIINS (GR) - Don't Waste My Time" survives the apostrophe."""
    s = unicodedata.normalize('NFKD', (s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _warn_mount(explicit):
    """Shout if the database is being reached through a path that looks like a mount
    or a sandbox, because paths written from here may not be paths Rekordbox can open."""
    p = os.path.abspath(os.path.expanduser(explicit or ''))
    for marker in ('/sessions/', '/mnt/', '/media/', '/run/'):
        if marker in p:
            print(f"  !! WARNING: the database is at {p}\n"
                  f"     That looks like a mount or sandbox rather than the machine Rekordbox\n"
                  f"     runs on. Any path written from here must be a path REKORDBOX can\n"
                  f"     open, not the path you see. Check the dry run carefully.\n")
            return


def cmd_relocate(a):
    """Point library entries at files that moved or were renamed.

    This is Rekordbox's Relocate, done in bulk. It changes the DATABASE, never the
    files: your cues, tags, ratings, playlists and play history all key on the track,
    not the filename, so repointing an entry keeps every bit of that.
    """
    import difflib
    path = db_paths(a.db)
    db = open_ro(path)
    # If you are reaching the drive through a mount that is not where Rekordbox sees it,
    # the path we FIND a file at is not the path that belongs in the database. Writing
    # the wrong one silently breaks every entry it touches, so it must be stated.
    remap = None
    if a.path_as:
        if '=' not in a.path_as:
            sys.exit("--path-as needs the form SEARCHED=STORED, "
                     "e.g. --path-as /mnt/T7=/Volumes/T7")
        src, dst = a.path_as.split('=', 1)
        remap = (os.path.abspath(os.path.expanduser(src.strip())).rstrip(os.sep),
                 dst.strip().rstrip('/\\'))
    roots = [os.path.abspath(os.path.expanduser(p)) for p in a.music_root]
    if not remap:
        _warn_mount(a.db)
    bad = [p for p in roots if not os.path.isdir(p)]
    if bad:
        sys.exit("not a folder: " + ", ".join(bad) +
                 "\n  Plug the drive in first. A folder that is not there looks exactly\n"
                 "  like a folder where every file has gone missing.")
    if not roots:
        sys.exit("relocate needs to know where to look:\n"
                 "  crate_doctor relocate --music-root /path/to/your/music\n"
                 "  (repeat it for each drive, or set music_roots in crate_doctor.ini)")

    # index the drive
    disk = []
    for root in roots:
        for dp, dn, fn in os.walk(root):
            for f in fn:
                if f.startswith('._') or not f.lower().endswith(AUDIO_EXT):
                    continue
                p = os.path.join(dp, f)
                try:
                    disk.append((p, f, os.path.getsize(p)))
                except OSError:
                    pass
    if not disk:
        sys.exit("found no audio under " + ", ".join(roots))
    by_size, by_name = collections.defaultdict(list), collections.defaultdict(list)
    for p, f, sz in disk:
        by_size[sz].append(p)
        by_name[loose(os.path.splitext(f)[0])].append((p, sz))

    # An entry is only MISSING if the file is neither at the path the database claims
    # nor findable by its own filename on the drive. Cloud-synced entries store a stub
    # path that never exists, so testing the path alone marks the whole library missing.
    on_disk_nfc = {nfc(f) for _p, f, _sz in disk}
    claimed, missing, healthy_names = set(), [], set()
    for r in real_tracks(db):
        fp = r.FolderPath or ''
        fn = r.FileNameL or os.path.basename(fp)
        stem = loose(os.path.splitext(fn)[0])
        if os.path.exists(fp):
            claimed.add(os.path.abspath(fp))
            healthy_names.add(stem)
            continue
        if nfc(fn) in on_disk_nfc:
            # found under its own name somewhere on the drive; Rekordbox resolves these
            for p, f, _sz in disk:
                if nfc(f) == nfc(fn):
                    claimed.add(os.path.abspath(p))
            healthy_names.add(stem)
            continue
        missing.append(r)

    plan, unmatched, ambiguous = [], [], []
    used = set()
    for r in missing:
        fn = r.FileNameL or os.path.basename(r.FolderPath or '')
        stem = loose(os.path.splitext(fn)[0])
        size = int(r.FileSize or 0)
        cands = []      # (confidence, why, path, disksize)

        # 1. exact byte size, and only one file has it -> conclusive
        same = [p for p in by_size.get(size, []) if p not in used and os.path.abspath(p) not in claimed]
        if size and len(same) == 1:
            cands.append(('high', 'exact byte size', same[0], size))
        # 2. same name once punctuation and case are ignored, size close
        for p, sz in by_name.get(stem, []):
            if p in used or os.path.abspath(p) in claimed:
                continue
            near = (not size) or abs(sz - size) <= max(1, size * 0.02)
            cands.append(('high' if near else 'low', 'same name' +
                          ('' if near else f', size differs {abs(sz-size)/max(1,size)*100:.1f}%'), p, sz))
        # 3. close name, close size -> worth showing, not worth trusting alone
        if not any(c[0] == 'high' for c in cands):
            pool = [k for k in by_name if k not in healthy_names]
            for k in difflib.get_close_matches(stem, pool, n=3, cutoff=0.90):
                for p, sz in by_name[k]:
                    if p in used or os.path.abspath(p) in claimed:
                        continue
                    if size and abs(sz - size) <= max(1, size * 0.02):
                        cands.append(('medium', f'name {difflib.SequenceMatcher(None, stem, k).ratio()*100:.0f}% similar, size within 2%', p, sz))

        order = {'high': 0, 'medium': 1, 'low': 2}
        cands.sort(key=lambda c: order[c[0]])
        best = [c for c in cands if c[0] == 'high'] or [c for c in cands if c[0] == 'medium']
        if not best:
            unmatched.append((r, cands))
            continue
        if len(best) > 1 and len({c[2] for c in best}) > 1:
            ambiguous.append((r, best))
            continue
        conf, why, p, sz = best[0]
        if conf == 'medium' and not a.fuzzy:
            ambiguous.append((r, best))
            continue
        used.add(p)
        plan.append((r, p, sz, conf, why))

    print(f"\nlibrary entries whose file is not where the database says: {len(missing)}")
    print(f"  can be repointed : {len(plan)}")
    print(f"  need your eyes   : {len(ambiguous)}")
    print(f"  no candidate     : {len(unmatched)}\n")

    def stored(p):
        """The path that will actually go into the database."""
        if remap and (p == remap[0] or p.startswith(remap[0] + os.sep)):
            return remap[1] + p[len(remap[0]):].replace(os.sep, '/')
        return p

    if remap:
        print(f"  paths will be stored as {remap[1]}/... "
              f"(found under {remap[0]}/...)\n")
    for r, p, sz, conf, why in plan:
        print(f"  [{conf}] {(r.FileNameL or '?')[:56]}")
        print(f"         -> {stored(p)}")
        if remap:
            print(f"            (found at {p})")
        extra = f"   (database had {r.FileSize} bytes, file is {sz})" if int(r.FileSize or 0) != sz else ""
        print(f"         {why}{extra}\n")
    for r, cands in ambiguous:
        print(f"  [?] {(r.FileNameL or '?')[:56]}   -- more than one possibility:")
        for conf, why, p, sz in cands[:3]:
            print(f"         {conf}: {p}  ({why})")
        print("      Pass --fuzzy to accept a single close match, or relocate this one in Rekordbox.\n")
    for r, cands in unmatched:
        print(f"  [x] {(r.FileNameL or '?')[:56]}   -- nothing on the drive matches.")
        print(f"      Database path: {(r.FolderPath or '?')[:70]}")
        if cands:
            for conf, why, p, sz in cands[:2]:
                print(f"      rejected: {p}  ({why})")
        print()

    if not plan:
        return
    if not a.write:
        print("  dry run — nothing written. Read the list above, then add --write.\n"
              "  This changes only the database. Your files are not touched, moved or renamed,\n"
              "  and cues, tags, ratings, playlists and play counts all survive.")
        return

    dst, _n = backup(path, 'before_relocate')
    print(f"  backup: {dst}")
    now = datetime.datetime.now()
    with SafeWrite(path) as wdb:
        byid = {str(x.ID): x for x in wdb.query(tables.DjmdContent).all()}
        n = 0
        for r, p, sz, conf, why in plan:
            row = byid.get(str(r.ID))
            if row is None:
                continue
            row.FolderPath = stored(p)
            row.FileNameL = os.path.basename(p)
            row.FileSize = sz
            row.rb_local_synced = 0
            row.updated_at = now
            n += 1
    print(f"  repointed {n} entries. Files untouched.")

# ---------------------------------------------------------------- setup

def _ask(prompt, default='y'):
    """y/n question that works when stdin is not a terminal (assumes the default)."""
    if not sys.stdin.isatty():
        return default == 'y'
    try:
        ans = input(f"{prompt} [{'Y/n' if default == 'y' else 'y/N'}] ").strip().lower()
    except EOFError:
        return default == 'y'
    return (ans or default).startswith('y')


def _run(cmd, **kw):
    import subprocess
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, **kw).returncode


def cmd_setup(a):
    """First-run: check Python, install what is missing, find the library, fetch the
    key, write a config with the right paths in it, take a backup. Then say what to
    try. Nothing here touches the library except the backup."""
    import platform, subprocess
    osname = {'darwin': 'macOS', 'win32': 'Windows'}.get(sys.platform, platform.system())
    py = py_cmd()
    print(f"\ncrate_doctor setup on {osname}, Python {platform.python_version()} at {sys.executable}\n")

    ok = True
    # 1. Python version
    if sys.version_info < (3, 9):
        print(f"  x  Python {platform.python_version()} is too old. Need 3.9 or newer.")
        if sys.platform == 'darwin':
            print("     Install from https://www.python.org/downloads/ or:  brew install python")
        elif sys.platform == 'win32':
            print("     Install from https://www.python.org/downloads/ -- tick 'Add python.exe to PATH'")
        else:
            print("     Use your package manager, e.g.  sudo apt install python3 python3-pip")
        sys.exit(1)
    print(f"  ok Python {platform.python_version()}")

    # 2. dependencies
    global MISSING
    if MISSING:
        print(f"  -  missing Python packages: {', '.join(MISSING)}")
        if a.yes or _ask("     Install them now with pip?"):
            in_venv = sys.prefix != getattr(sys, 'base_prefix', sys.prefix)
            base = [sys.executable, '-m', 'pip', 'install'] + MISSING
            # inside a venv, plain install. Outside, --user keeps the system Python clean.
            attempts = [base] if in_venv else [base + ['--user']]
            # PEP 668 "externally managed environment" -- Homebrew and modern Debian
            attempts.append(base + ['--user', '--break-system-packages'])
            rc = 1
            for cmd in attempts:
                rc = _run(cmd)
                if rc == 0:
                    break
                print("     pip refused; trying another way...")
            if rc != 0:
                print("  x  pip could not install them. Try by hand:\n"
                      f"       {py} -m pip install --user {' '.join(MISSING)}\n"
                      "     and run setup again.")
                sys.exit(1)
            print("  ok installed. Continuing...\n")
            # a fresh interpreter so the new packages import cleanly
            sys.exit(subprocess.call([sys.executable] + sys.argv))
        else:
            print(f"     Install them yourself:  {py} -m pip install --user {' '.join(MISSING)}")
            sys.exit(1)
    print("  ok numpy and pyrekordbox present")

    # 3. the database
    db = a.db or find_db()
    if not db:
        print("  -  could not find master.db in any of the usual places:")
        for p in candidate_db_paths():
            print(f"       {p}")
        if sys.stdin.isatty():
            typed = input("     Paste the full path to master.db (or press Enter to skip): ").strip()
            typed = os.path.expanduser(typed.strip('"').strip("'"))
            if typed and os.path.exists(typed):
                db = typed
        if not db:
            print("     Skipping. Open Rekordbox once if you never have, then run setup again,\n"
                  "     or pass --db /path/to/master.db.")
            ok = False
    if db:
        print(f"  ok library: {db}")

    # 4. the decryption key
    key_ok = False
    if db:
        try:
            test = Rekordbox6Database(path=db, db_dir=os.path.dirname(db))
            n = test.query(tables.DjmdContent).count()
            test.close()
            key_ok = True
            print(f"  ok database opens ({n} entries)")
        except Exception as e:
            msg = str(e).splitlines()[0][:80]
            print(f"  -  database will not open yet ({msg})")
            print("     This usually means the decryption key has not been fetched on this machine.")
            if a.yes or _ask("     Fetch it now? (one time, needs internet)"):
                rc = _run([sys.executable, '-m', 'pyrekordbox', 'download-key'])
                if rc == 0:
                    try:
                        test = Rekordbox6Database(path=db, db_dir=os.path.dirname(db))
                        test.close()
                        key_ok = True
                        print("  ok key fetched, database opens")
                    except Exception as e2:
                        print(f"  x  still will not open: {str(e2).splitlines()[0][:80]}")
                else:
                    print("  x  key download failed. Are you online? Try again later with:\n"
                          f"       {py} -m pyrekordbox download-key")
            if not key_ok:
                ok = False

    # 5. Rekordbox running?
    procs = rekordbox_processes()
    if procs:
        print(f"  !  {', '.join(procs)} is running. Fine for looking; quit it fully before any --write.")

    # 6. config
    cfg_path = os.path.join(os.getcwd(), CONFIG_NAME)
    if not os.path.exists(cfg_path) or a.force:
        text = DEFAULT_CONFIG
        if db:
            text = text.replace("db =\n", f"db = {db}\n", 1)
        with open(cfg_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"  ok wrote {cfg_path}" + (" (with your library path filled in)" if db else ""))
    else:
        print(f"  ok config already exists: {cfg_path}")

    # 7. backup
    if db and key_ok:
        try:
            if not BACKUP_DIR:
                print("  !  backups will go next to master.db on your system drive "
                      "(~130 MB each).\n"
                      "     Set  backups = /Volumes/YourDrive/rekordbox_backups  in "
                      f"{CONFIG_NAME} to keep them off it.")
            dst, _n = backup(db, 'setup')
            print(f"  ok backup: {dst}")
        except Exception as e:
            print(f"  -  backup failed: {e}")
            ok = False

    print()
    if ok and key_ok:
        exe = 'crate_doctor' if os.path.basename(sys.argv[0]) == 'crate_doctor' else f"{py} {os.path.basename(sys.argv[0])}"
        print("Ready. Everything below only looks -- nothing changes without --write.\n")
        print(f"  {exe} cues            what is my cueing habit, which tracks break it")
        print(f"  {exe} tags            are my My Tags doing anything")
        print(f"  {exe} dupes           same track twice")
        print(f"  {exe} sound -o sound.jsonl        (a few minutes; unlocks the two below)")
        print(f"  {exe} playlists --sound sound.jsonl")
        print(f"  {exe} tags --propose --sound sound.jsonl")
        print(f"\nSettings live in {CONFIG_NAME}. Or open this folder in Claude and just ask.")
    else:
        print("Not finished -- fix the items marked x or - above and run setup again.")

# ---------------------------------------------------------------- knowledge base

def kb_paths():
    """Everywhere knowledge.json might reasonably live, in order of preference."""
    here = os.path.dirname(os.path.abspath(__file__))
    out = [os.path.join(here, 'knowledge.json'),
           os.path.join(os.getcwd(), 'knowledge.json'),
           os.path.join(sys.prefix, 'share', 'crate_doctor', 'knowledge.json'),
           os.path.join(os.path.dirname(here), 'share', 'crate_doctor', 'knowledge.json')]
    if sys.platform == 'win32':
        # guard the env var: an unset APPDATA would otherwise build a RELATIVE path and
        # make us search whatever folder the user happens to be standing in
        appdata = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'),
                                                            'AppData', 'Roaming')
        out.append(os.path.join(appdata, 'crate_doctor', 'knowledge.json'))
    elif sys.platform == 'darwin':
        out.append(os.path.expanduser('~/Library/Application Support/crate_doctor/knowledge.json'))
    else:
        out.append(os.path.join(os.environ.get('XDG_CONFIG_HOME',
                   os.path.expanduser('~/.config')), 'crate_doctor', 'knowledge.json'))
    return out


def load_kb(explicit=None):
    """The shipped label/artist/genre map. Edit knowledge.json to add your own."""
    cand = [explicit] if explicit else kb_paths()
    for p in cand:
        if p and os.path.exists(p):
            try:
                return json.load(open(p, encoding='utf-8'))
            except json.JSONDecodeError as e:
                sys.exit(f"knowledge.json at {p} is not valid JSON:\n  {e}\n"
                         "  If you have been editing it, check for a missing comma "
                         "or a trailing one before a closing brace.")
    sys.exit("could not find knowledge.json. Looked in:\n  " + "\n  ".join(cand) +
             "\n  It ships alongside crate_doctor. Keep them together, or pass --kb PATH.")


def kbnorm(s):
    """Fold accents and case so 'Bicep' matches 'BICEP' and 'Björk' matches 'Bjork'."""
    s = unicodedata.normalize('NFKD', (s or '').strip())
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


MYTAG_GROUPS = {'1': 'Style', '2': 'Components', '3': 'Situation', '4': 'Vibe'}

# The five Vibe tags that come from the waveform rather than from metadata, and the
# percentile of YOUR library each one cuts at. Thresholds are computed per library,
# so "Dark" means dark relative to your own collection, not to some absolute scale.
AUDIO_VIBES = [
    ('Dark',        'brightness',      '<=', 0.22),
    ('Vibrant',     'brightness',      '>=', 0.80),
    ('Thumping',    'kick_mean',       '>=', 0.80),
    ('Atmospheric', 'breakdown_frac',  '>=', 0.80),
    ('Chill',       'loud_frac',       '<=', 0.18),
]


def live_tags(db):
    """{(group, name): id} for every tag that currently exists, plus reverse map."""
    out, grp = {}, {}
    for t in db.query(tables.DjmdMyTag).all():
        if t.rb_local_deleted or t.ParentID == 'root':
            continue
        g = MYTAG_GROUPS.get(str(t.ParentID))
        if not g:
            continue
        out[(g, t.Name)] = str(t.ID)
        grp[t.Name] = g
    return out, grp


def tag_rows(db):
    rows = collections.defaultdict(set)      # tag id -> {content id}
    for s in db.query(tables.DjmdSongMyTag).all():
        if s.rb_local_deleted:
            continue
        rows[str(s.MyTagID)].add(str(s.ContentID))
    return rows


# ---------------------------------------------------------------- tags

def cmd_tags(a):
    path = db_paths(a.db)
    db = open_ro(path)
    tracks = list(real_tracks(db))
    N = len(tracks)
    tagid, grp = live_tags(db)
    rows = tag_rows(db)

    print(f"\n{N} tracks, {len(tagid)} tags defined, "
          f"{sum(len(v) for v in rows.values())} tag rows.\n")

    # ---- audit: does each tag actually NARROW the library?
    # A tag is only worth having if it splits the collection. One that lands on 60%
    # of your tracks tells you nothing when you search it; one on 0 tracks is clutter.
    LO, HI = a.min_pct, a.max_pct
    buckets = collections.defaultdict(list)
    for (g, name), tid in sorted(tagid.items()):
        n = len(rows.get(tid, ()))
        p = 100.0 * n / max(1, N)
        verdict = 'ok' if LO <= p <= HI else ('dead' if n == 0 else
                                              ('thin' if p < LO else 'too broad'))
        buckets[verdict].append((p, n, g, name))

    print(f"A tag earns its place by narrowing the library to {LO:g}-{HI:g}%.")
    for verdict, blurb in (('too broad', f'over {HI:g}% — searching it barely filters anything'),
                           ('dead', 'on no tracks at all'),
                           ('thin', f'under {LO:g}% — fine for something genuinely rare'),
                           ('ok', 'earning their place')):
        v = sorted(buckets[verdict], reverse=(verdict == 'too broad'))
        if not v:
            continue
        print(f"\n  {verdict.upper()}  ({len(v)}) — {blurb}")
        for p, n, g, name in v[:a.limit]:
            print(f"     {p:5.1f}%  {n:5d}  [{g:<10}] {name}")
        if len(v) > a.limit:
            print(f"     ... and {len(v)-a.limit} more")

    tagged = set()
    for v in rows.values():
        tagged |= v
    print(f"\nCoverage: {len(tagged & {str(r.ID) for r in tracks})} of {N} tracks carry at least one tag.")

    if not a.propose:
        print("\nAdd --propose to see what tags this would suggest, from the shipped\n"
              "label/artist/genre knowledge base plus your own waveforms.")
        return

    # ---- propose
    kb = load_kb(a.kb)
    LAB = {kbnorm(k): v for k, v in kb['labels'].items()}
    ART = {kbnorm(k): v for k, v in kb['artists'].items()}
    GEN = kb['genres']
    SUB = kb['subgenre_patterns']
    NOSIG = {kbnorm(x) for x in kb['no_signal_labels']}

    # audio features, for the five Vibe tags that metadata cannot know
    feats = load_sound(a.sound, {str(r.ID) for r in tracks}) if a.sound else {}

    genres = {str(g.ID): g.Name for g in db.query(tables.DjmdGenre).all()}
    labels = {str(l.ID): l.Name for l in db.query(tables.DjmdLabel).all()}
    artists = {str(x.ID): x.Name for x in db.query(tables.DjmdArtist).all()}

    plan = collections.defaultdict(dict)     # cid -> {tag: source}
    why = collections.Counter()
    audio_owned = {name for name, _f, _op, _q in AUDIO_VIBES}
    unknown_labels = collections.Counter()
    for r in tracks:
        cid = str(r.ID)
        g = genres.get(str(r.GenreID), '')
        lab = labels.get(str(r.LabelID), '')
        who = kbnorm(artists.get(str(r.ArtistID), '')) + ' , ' + kbnorm(artists.get(str(r.RemixerID), ''))
        hits = collections.defaultdict(list)

        if g in GEN:
            for t in GEN[g][0] + GEN[g][1]:
                hits[t].append(('genre', 3))
        elif g:
            why['genre not in knowledge base: ' + g] += 1

        ln_ = kbnorm(lab)
        if ln_ and ln_ not in NOSIG:
            if ln_ in LAB:
                s, v, conf = LAB[ln_]
                for t in s + v:
                    hits[t].append(('label', conf))
            else:
                unknown_labels[lab] += 1

        for pat, tags in SUB:
            if pat and pat in kbnorm(r.Title or ''):
                for t in tags:
                    hits[t].append(('subgenre', 3))
        for aname, tags in ART.items():
            if aname and aname in who:
                for t in tags:
                    hits[t].append(('artist', 3))

        for t, srcs in hits.items():
            # The waveform owns these. A label that "sounds dark" is a guess; the
            # track's own low-band energy is a measurement. This matches how the
            # original library was tagged: audio Vibe tags come ONLY from audio.
            if t in audio_owned:
                why['waveform-owned tag, metadata source ignored: ' + t] += 1
                continue
            conf = max(c for _s, c in srcs)
            if conf < a.min_confidence:
                why[f'below confidence {a.min_confidence}: ' + t] += 1
                continue
            plan[cid][t] = srcs[0][0]

    # audio-derived Vibe tags, thresholds taken from this library
    audio_plan = collections.defaultdict(dict)
    if feats:
        have = [i for i in feats if i in {str(r.ID) for r in tracks}]
        for name, field, op, q in AUDIO_VIBES:
            vals = [feats[i].get(field) or 0 for i in have]
            thr = pct(vals, q)
            for i in have:
                v = feats[i].get(field) or 0
                if v and ((op == '<=' and v <= thr) or (op == '>=' and v >= thr)):
                    audio_plan[i][name] = 'waveform'
            print(f"  audio vibe  {name:<12} {field:<15} {op} {thr:8.3f}  -> "
                  f"{sum(1 for i in have if name in audio_plan[i])} tracks")

    # only tags that already exist in their library get written, unless --create-tags
    want = collections.Counter()
    for d in list(plan.values()) + list(audio_plan.values()):
        for t in d:
            want[t] += 1
    missing = [t for t in want if t not in grp]

    print(f"\nProposal: {sum(len(v) for v in plan.values())} tag rows from metadata"
          + (f", {sum(len(v) for v in audio_plan.values())} from your waveforms" if feats else
             "\n  (no --sound file given, so the 5 waveform Vibe tags were skipped)"))
    print(f"  across {len(set(plan) | set(audio_plan))} tracks, {len(want)} distinct tags")
    if unknown_labels:
        print(f"\n  {len(unknown_labels)} labels are not in the knowledge base. Top ones:")
        for k, v in unknown_labels.most_common(10):
            print(f"     {v:4d}  {k}")
        print("     Add them to knowledge.json and re-run — that is where the accuracy lives.")
    if missing:
        print(f"\n  {len(missing)} proposed tags do not exist in your library yet:")
        print("     " + ', '.join(sorted(missing)[:20]) + ('...' if len(missing) > 20 else ''))
        print("     They will be SKIPPED unless you add --create-tags.")

    # how much would each proposed tag narrow?
    print(f"\n  Would each proposed tag earn its place ({LO:g}-{HI:g}% of {N})?")
    merged = collections.Counter()
    for cid in set(plan) | set(audio_plan):
        for t in set(plan.get(cid, {})) | set(audio_plan.get(cid, {})):
            merged[t] += 1
    for t, n in merged.most_common():
        p = 100.0 * n / N
        flag = '' if LO <= p <= HI else ('  <- too broad' if p > HI else '  <- thin')
        print(f"     {p:5.1f}%  {n:5d}  {t}{flag}")

    if a.out:
        json.dump({'metadata': {k: v for k, v in plan.items()},
                   'audio': {k: v for k, v in audio_plan.items()}},
                  open(a.out, 'w'))
        print(f"\n  plan written to {a.out}")

    if not a.write:
        print("\n  dry run — nothing written. Read the list above, then add --write.")
        return

    # validate BEFORE taking a 100MB backup
    if a.create_tags:
        broad = [t for t in missing if 100.0 * merged.get(t, 0) / N > HI]
        if broad and not a.force:
            sys.exit("refusing to create tags that would land on more than "
                     f"{HI:g}% of your library: {', '.join(sorted(broad))}\n"
                     "  A tag that broad does not narrow anything when you search it.\n"
                     "  Drop them, raise --max-pct, or pass --force if you really mean it.")
    dst, n = backup(path, 'before_tags')
    print(f"\n  backup: {dst}")
    now = datetime.datetime.now()
    added = created = 0
    with SafeWrite(path) as wdb:
        tid2, grp2 = live_tags(wdb)
        if a.create_tags:
            for t in sorted(missing):
                nid = str(uuid.uuid4())
                wdb.add(tables.DjmdMyTag.create(
                    ID=nid, Seq=0, Name=t, Attribute=0, ParentID='1',
                    UUID=str(uuid.uuid4()), rb_data_status=0, rb_local_data_status=0,
                    rb_local_deleted=0, rb_local_synced=0, created_at=now, updated_at=now))
                tid2[('Style', t)] = nid
                grp2[t] = 'Style'
                created += 1
            wdb.flush()
        existing = tag_rows(wdb)
        byname = {name: i for (g, name), i in tid2.items()}
        for cid in set(plan) | set(audio_plan):
            for t in set(plan.get(cid, {})) | set(audio_plan.get(cid, {})):
                i = byname.get(t)
                if not i or cid in existing.get(i, ()):
                    continue
                wdb.add(tables.DjmdSongMyTag.create(
                    ID=str(uuid.uuid4()), MyTagID=str(i), ContentID=str(cid), TrackNo=None,
                    UUID=str(uuid.uuid4()), rb_data_status=0, rb_local_data_status=0,
                    rb_local_deleted=0, rb_local_synced=0, created_at=now, updated_at=now))
                existing[i].add(cid)
                added += 1
    print(f"  created {created} tags, added {added} tag rows")


# ---------------------------------------------------------------- playlists

def cmd_playlists(a):
    """Playlists built from things every Rekordbox library already has: ratings,
    play counts, the play history, and the waveform. No taste required."""
    path = db_paths(a.db)
    db = open_ro(path)
    tracks = list(real_tracks(db))
    byid = {str(r.ID): r for r in tracks}

    # play history: DateCreated on a history session is a STRING, not a date
    hist_date = {}
    for h in db.query(tables.DjmdHistory).all():
        if not h.rb_local_deleted:
            hist_date[str(h.ID)] = (h.DateCreated or '')[:10]
    plays, last = collections.Counter(), {}
    for s in db.query(tables.DjmdSongHistory).all():
        if s.rb_local_deleted:
            continue
        cid = str(s.ContentID)
        plays[cid] += 1
        d = hist_date.get(str(s.HistoryID))
        if d and (cid not in last or d > last[cid]):
            last[cid] = d

    feats = load_sound(a.sound, set(byid)) if a.sound else {}

    def rating(i):    return (byid[i].Rating or 0)
    def bpm(i):       return (byid[i].BPM or 0) / 100.0
    def played(i):    return (byid[i].DJPlayCount or 0) + plays[i]
    def f(i, k):      return (feats.get(i) or {}).get(k) or 0
    def nm(i):
        r = byid[i]
        return f"{(r.Title or '?')[:44]}"

    ids = list(byid)
    stale = (datetime.date.today() - datetime.timedelta(days=a.stale_days)).isoformat()

    out = {}
    out['Never Played 4+'] = [i for i in ids if rating(i) >= 4 and played(i) == 0]
    out['Dusty Bangers'] = [i for i in ids if rating(i) >= 4 and played(i) > 0
                            and (i not in last or last[i] < stale)]
    out['Workhorses'] = sorted([i for i in ids if played(i) >= a.workhorse_plays],
                               key=lambda i: -played(i))
    out['Underrated Workhorses'] = [i for i in out['Workhorses'] if rating(i) <= 3]

    if feats:
        pool = [i for i in ids if i in feats and rating(i) >= 3]
        # The Turn: biggest swing between the quiet part and the loud part of a track.
        turn = sorted(((f(i, 'loud_frac') - f(i, 'breakdown_frac'), i) for i in pool),
                      reverse=True)
        cut = max(1, int(len(turn) * 0.15))
        out['The Turn'] = [i for _v, i in turn[:cut]]
        # Sunrise: bright, breakdown-heavy, mid tempo.
        sun = [i for i in pool if a.sunrise_lo <= bpm(i) <= a.sunrise_hi]
        if sun:
            b = pct([f(i, 'brightness') for i in sun], 0.55)
            d = pct([f(i, 'breakdown_frac') for i in sun], 0.50)
            out['Sunrise'] = [i for i in sun if f(i, 'brightness') >= b
                              and f(i, 'breakdown_frac') >= d]
        # Cold Opens: long run-up before the kick really lands.
        out['Cold Opens'] = [i for i in pool if f(i, 'breakdown_frac') >= 0.30]
        # Peak Time: heavy kick, loud, few breakdowns.
        k = pct([f(i, 'kick_mean') for i in pool], 0.75)
        out['Peak Time'] = [i for i in pool if f(i, 'kick_mean') >= k
                            and f(i, 'breakdown_frac') <= 0.15]
    for k in list(out):
        if not out[k]:
            del out[k]
        else:
            out[k] = sorted(out[k], key=lambda i: bpm(i))

    print(f"\n{len(byid)} tracks. Proposed playlists:\n")
    if not feats:
        print("  (no --sound file, so the waveform-based playlists were skipped —\n"
              "   run `sound -o sound.jsonl` first to get all of them)\n")
    for name, v in out.items():
        print(f"  {name:<24} {len(v):5d}")
        for i in v[:a.limit]:
            print(f"      {bpm(i):5.1f}  {rating(i)}*  {played(i):3d} plays   {nm(i)}")
        if a.limit and len(v) > a.limit:
            print(f"      ... and {len(v)-a.limit} more")
        if a.limit:
            print()

    if not a.write:
        print("  dry run — nothing written. add --write to create them.")
        return
    dst, _n = backup(path, 'before_playlists')
    print(f"  backup: {dst}")
    now = datetime.datetime.now()
    made = added = 0
    with SafeWrite(path) as wdb:
        existing = {p.Name: p for p in wdb.query(tables.DjmdPlaylist).all()
                    if not p.rb_local_deleted}
        folder = existing.get(a.folder)
        if folder is None:
            folder = wdb.create_playlist_folder(a.folder)
            wdb.flush()
        for name, v in out.items():
            if name in existing:
                print(f"  skipping '{name}' — a playlist with that name already exists")
                continue
            pl = wdb.create_playlist(name, parent=folder)
            wdb.flush()
            for k, i in enumerate(v, 1):
                wdb.add(tables.DjmdSongPlaylist.create(
                    ID=str(uuid.uuid4()), PlaylistID=str(pl.ID), ContentID=str(i), TrackNo=k,
                    UUID=str(uuid.uuid4()), rb_data_status=0, rb_local_data_status=0,
                    rb_local_deleted=0, rb_local_synced=0, created_at=now, updated_at=now))
                added += 1
            made += 1
    print(f"  created {made} playlists in '{a.folder}', {added} tracks placed")


# ---------------------------------------------------------------- dupes

def cmd_dupes(a):
    path = db_paths(a.db)
    db = open_ro(path)
    tracks = list(real_tracks(db))
    by_ts, by_file = collections.defaultdict(list), collections.defaultdict(list)
    for r in tracks:
        t = kbnorm(r.Title or '')
        if t:
            by_ts[(t, int((r.BPM or 0) / 100), int(r.Length or 0))].append(r)
        if r.FileNameL and r.FileSize:
            by_file[(nfc(r.FileNameL), int(r.FileSize))].append(r)

    exact = [v for v in by_file.values() if len(v) > 1]
    seen = {frozenset(str(r.ID) for r in v) for v in exact}
    near = [v for v in by_ts.values()
            if len(v) > 1 and frozenset(str(r.ID) for r in v) not in seen]

    print(f"\n{len(tracks)} tracks.")
    print(f"\nSame filename AND same byte size ({len(exact)} groups) — almost certainly "
          f"the same file twice:")
    for v in exact[:a.limit]:
        print(f"   {(v[0].Title or '?')[:50]}")
        for r in v:
            print(f"      {(r.FolderPath or '?')[-70:]}")
    if len(exact) > a.limit:
        print(f"   ... and {len(exact)-a.limit} more")

    print(f"\nSame title, BPM and length but different files ({len(near)} groups) — "
          f"could be a real duplicate, could be a remix pack:")
    for v in near[:a.limit]:
        print(f"   {(v[0].Title or '?')[:50]}")
        for r in v:
            print(f"      {(r.FileNameL or '?')[:66]}")
    if len(near) > a.limit:
        print(f"   ... and {len(near)-a.limit} more")

    print("\n  Nothing was changed. Before removing either copy, check which one your\n"
          "  playlists and history point at — the newer file is often the one with no\n"
          "  cues and no play count on it.")



# ---------------------------------------------------------------- rename

# Characters no filesystem should be asked to carry. Windows is the strict one, so
# these are stripped everywhere — a library that only works on a Mac is half a library.
BAD_CHARS = '<>:"/\\|?*'


def safe_component(s, maxlen=180):
    """Clean one field. Note what this does NOT do: strip trailing periods.
    'Lello B.', 'Nikk.', 'Rodriguez Jr.' are real names and the dot belongs to them.
    Only the finished filename gets its tail trimmed, in safe_filename below."""
    s = unicodedata.normalize('NFC', (s or '').strip())
    # Rekordbox itself substitutes '_' for characters a filesystem won't take, so
    # match it -- 'How Will I Know?' becomes 'How Will I Know_' either way, and your
    # existing library stays put instead of churning through a pointless rename.
    s = ''.join('_' if c in BAD_CHARS else c for c in s if ord(c) >= 32)
    s = re.sub(r'_{2,}', '_', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:maxlen]


def safe_filename(name, maxlen=200):
    """Trim the finished name. Windows refuses a trailing dot or space on a FILE,
    which is why this runs once at the end and not on every field."""
    name = re.sub(r'\s+', ' ', name).strip(' -').strip()
    return name[:maxlen].rstrip(' .')


def target_name(r, artists, pattern):
    """Build the filename Rekordbox's own metadata says this track should have."""
    artist = artists.get(str(r.ArtistID), '') or ''
    remixer = artists.get(str(r.RemixerID), '') or ''
    title = r.Title or ''
    # Rekordbox usually already carries the mix inside the title -- "Glue (Original Mix)".
    # Only add the remixer when the title does not mention one, or you get it twice.
    mix = ''
    if '(' not in title and remixer and kbnorm(remixer) != kbnorm(artist):
        mix = f'{remixer} Remix'
    fields = {'artist': safe_component(artist), 'title': safe_component(title),
              'remixer': safe_component(remixer), 'mix': safe_component(mix)}
    try:
        name = pattern.format(**fields)
    except KeyError as e:
        sys.exit(f"unknown field {e} in --pattern. Available: "
                 "{artist} {title} {remixer} {mix}")
    name = re.sub(r'\(\s*\)', '', name)          # empty (mix) when there is no mix
    return safe_filename(name)


def cmd_rename(a):
    path = db_paths(a.db)
    # rename writes FolderPath too, so the same mount hazard applies. It renames files
    # in place rather than moving them between roots, so a warning is enough here.
    _warn_mount(a.db)
    db = open_ro(path)
    artists = {str(x.ID): x.Name for x in db.query(tables.DjmdArtist).all()}

    plan, skipped = [], collections.Counter()
    taken = collections.Counter()
    for r in real_tracks(db):
        fp = r.FolderPath or ''
        if not fp or not os.path.exists(fp):
            skipped['file not found on disk'] += 1
            continue
        if not (r.Title and str(r.ArtistID) in artists):
            skipped['no artist or title in the database'] += 1
            continue
        base = target_name(r, artists, a.pattern)
        if not base:
            skipped['metadata produced an empty name'] += 1
            continue
        ext = os.path.splitext(fp)[1]
        d = os.path.dirname(fp)
        new = os.path.join(d, base + ext)
        if unicodedata.normalize('NFC', new) == unicodedata.normalize('NFC', fp):
            skipped['already correct'] += 1
            continue
        # never overwrite: a name already on disk, or one another track wants too.
        # A change of case only ('foo.aiff' -> 'Foo.aiff') is not a collision even
        # though os.path.exists says yes on a case-insensitive drive.
        key = (d, kbnorm(base + ext))
        case_only = os.path.basename(fp).lower() == (base + ext).lower()
        if (os.path.exists(new) and not case_only) or taken[key]:
            skipped['target name already taken'] += 1
            continue
        taken[key] += 1
        plan.append((str(r.ID), fp, new))

    print(f"\npattern: {a.pattern}")
    print(f"  files to rename : {len(plan)}")
    for k, v in skipped.most_common():
        print(f"  skipped ({v}): {k}")
    print()
    for _cid, old, new in plan[:a.limit]:
        print(f"   {os.path.basename(old)}")
        print(f"-> {os.path.basename(new)}\n")
    if len(plan) > a.limit:
        print(f"   ... and {len(plan)-a.limit} more\n")

    if not plan:
        return
    if not a.write:
        print("  dry run — nothing renamed. Read the list, then add --write.")
        return

    dst, _n = backup(path, 'before_rename')
    print(f"  backup: {dst}")

    # Rename on disk first, remembering every move. If the database write fails we
    # put every file back, so the library is never left pointing at names that moved.
    done = []
    try:
        for cid, old, new in plan:
            os.rename(old, new)
            done.append((old, new))
            # macOS leaves a '._name' sidecar next to every file on exFAT drives. Move
            # it too, or the drive fills with orphaned sidecars named for old files.
            side = os.path.join(os.path.dirname(old), '._' + os.path.basename(old))
            if os.path.exists(side):
                nside = os.path.join(os.path.dirname(new), '._' + os.path.basename(new))
                try:
                    os.rename(side, nside)
                    done.append((side, nside))
                except OSError:
                    pass
    except OSError as e:
        for old, new in reversed(done):
            try:
                os.rename(new, old)
            except OSError:
                pass
        sys.exit(f"rename failed on disk ({e}). Every file was put back. "
                 f"Nothing was written to the database.")
    print(f"  renamed {len(done)} files on disk")

    try:
        with SafeWrite(path) as wdb:
            byid = {str(r.ID): r for r in wdb.query(tables.DjmdContent).all()}
            now = datetime.datetime.now()
            for cid, old, new in plan:
                row = byid.get(cid)
                if row is None:
                    continue
                row.FolderPath = new
                row.FileNameL = os.path.basename(new)
                row.rb_local_synced = 0
                row.updated_at = now
    except BaseException as e:
        for old, new in reversed(done):
            try:
                os.rename(new, old)
            except OSError:
                pass
        msg = str(e).strip().splitlines()[0] if str(e).strip() else e.__class__.__name__
        sys.exit(f"\n  DATABASE WRITE FAILED: {msg}\n"
                 f"  All {len(done)} files were renamed back to their original names.\n"
                 f"  Your library is unchanged. The backup above is still there if you want it.")
    print(f"  database updated for {len(plan)} tracks")
    print("\n  Open Rekordbox and check a few. Playlists, cues, tags and play counts are\n"
          "  keyed to the track, not the filename, so all of that follows automatically.")


def cmd_backup(a):
    path = db_paths(a.db)
    dst, n = backup(path, 'manual')
    print(f"backed up {path}\n       -> {dst}" + (f" (+{n-1} wal/shm)" if n > 1 else ""))


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(
        description="Audit and repair a Rekordbox library.",
        epilog="Quit Rekordbox before using --write.")
    ap.add_argument('--db', help='path to master.db (default: auto-detect)')
    ap.add_argument('--config', help=f'path to {CONFIG_NAME} (default: look in this folder, '
                                     f'then next to crate_doctor, then your user config folder)')
    ap.add_argument('--backup-dir', help='where backups go (default: beside master.db). '
                                         'Put this on an external drive.')
    ap.add_argument('--keep-backups', type=int, help='how many to keep (default 5, 0 = all)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('cues', help="audit your cueing; add --fix to repair it")
    c.add_argument('--tag', default='', help='only look at cues with this Comment; new cues get it too')
    c.add_argument('--floor-bars', type=float, default=0,
                   help='minimum bars of music after a cue (default: whatever your own library says)')
    c.add_argument('--limit', type=int, default=25)
    c.add_argument('--fix', action='store_true', help='repair the cues, not just report on them')
    c.add_argument('--all', action='store_true', help='with --fix: every cue, including ones you set by hand')
    c.add_argument('--target', type=int, default=0, help='with --fix: top each track back up to this many cues')
    c.add_argument('--min-space', type=int, default=8, help='minimum bars between cues (default 8)')
    c.add_argument('--outro-lo', type=int, default=20, help='window for the final cue, nearest bar to the end (default 20)')
    c.add_argument('--outro-hi', type=int, default=36, help='window for the final cue, furthest bar (default 36)')
    c.add_argument('--write', action='store_true', help='with --fix: actually apply the changes')
    c.set_defaults(func=cmd_cues)

    c = sub.add_parser('sound', help="per-track sound numbers, decoded from the stored waveform")
    c.add_argument('-o', '--out', help='write JSONL here instead of stdout')
    c.add_argument('--limit', type=int, default=0, help='stop after N tracks (for a quick look)')
    c.set_defaults(func=cmd_sound)

    c = sub.add_parser('disk', help="compare your drive against your library")
    c.add_argument('--music-root', action='append', default=[], help='folder to scan (repeatable)')
    c.add_argument('--limit', type=int, default=20)
    c.set_defaults(func=cmd_disk)

    c = sub.add_parser('tags', help="audit My Tags, and propose new ones from the knowledge base")
    c.add_argument('--propose', action='store_true', help='suggest tags, not just audit existing ones')
    c.add_argument('--sound', help='sound.jsonl from the sound command; unlocks the 5 waveform Vibe tags')
    c.add_argument('--kb', help='path to knowledge.json (default: next to this script)')
    c.add_argument('--min-pct', type=float, default=3.0, help='a tag should cover at least this %% of the library (default 3)')
    c.add_argument('--max-pct', type=float, default=25.0, help='...and at most this %% (default 25)')
    c.add_argument('--min-confidence', type=int, default=2, help='1-3, how sure the knowledge base must be (default 2)')
    c.add_argument('--create-tags', action='store_true', help='also create proposed tags that do not exist yet')
    c.add_argument('--force', action='store_true', help='create tags even if they are too broad to be useful')
    c.add_argument('-o', '--out', help='write the proposal to this JSON file')
    c.add_argument('--limit', type=int, default=20)
    c.add_argument('--write', action='store_true', help='actually apply the proposal')
    c.set_defaults(func=cmd_tags)

    c = sub.add_parser('playlists', help="build playlists from ratings, play history and waveforms")
    c.add_argument('--sound', help='sound.jsonl; unlocks the waveform-based playlists')
    c.add_argument('--folder', default='crate_doctor', help='playlist folder to create them in')
    c.add_argument('--stale-days', type=int, default=365, help='"dusty" means not played in this many days (default 365)')
    c.add_argument('--workhorse-plays', type=int, default=10, help='plays that make a track a workhorse (default 10)')
    c.add_argument('--sunrise-lo', type=float, default=119.0)
    c.add_argument('--sunrise-hi', type=float, default=127.0)
    c.add_argument('--limit', type=int, default=5, help='example tracks to print per crate')
    c.add_argument('--write', action='store_true', help='actually create the playlists')
    c.set_defaults(func=cmd_playlists)

    c = sub.add_parser('relocate', help="repoint entries at files that moved or were renamed")
    c.add_argument('--music-root', action='append', default=[], help='folder to search (repeatable)')
    c.add_argument('--fuzzy', action='store_true', help='also accept a single close-name match')
    c.add_argument('--path-as', metavar='SEARCHED=STORED',
                   help='write paths as the machine running Rekordbox sees them, when that '
                        'differs from where you are searching. e.g. '
                        '--path-as /mnt/T7=/Volumes/T7 . Only needed if you are working '
                        'through a mount, a VM, or a network share.')
    c.add_argument('--write', action='store_true', help='actually repoint them')
    c.set_defaults(func=cmd_relocate)

    c = sub.add_parser('dupes', help="find the same track in your library twice")
    c.add_argument('--limit', type=int, default=15)
    c.set_defaults(func=cmd_dupes)

    c = sub.add_parser('rename', help="rename files on disk to match your library metadata")
    c.add_argument('--pattern', default='{artist} - {title}',
                   help='fields: {artist} {title} {remixer} {mix}  (default: "{artist} - {title}")')
    c.add_argument('--limit', type=int, default=15, help='examples to print')
    c.add_argument('--write', action='store_true', help='actually rename')
    c.set_defaults(func=cmd_rename)

    c = sub.add_parser('backup', help="timestamped copy of master.db")
    c.set_defaults(func=cmd_backup)

    c = sub.add_parser('setup', help="first run: install what is missing, find your library, fetch the key")
    c.add_argument('--yes', '-y', action='store_true', help='do not ask, just do it')
    c.add_argument('--force', action='store_true', help=f'rewrite {CONFIG_NAME} even if it exists')
    c.set_defaults(func=cmd_setup)

    c = sub.add_parser('init', help=f"write a starter {CONFIG_NAME} you can edit")
    c.add_argument('path', nargs='?', help=f'where to write it (default: ./{CONFIG_NAME})')
    c.add_argument('--force', action='store_true', help='overwrite an existing one')
    c.set_defaults(func=cmd_init)

    # These four work before OR after the command name. Nobody remembers which, and
    # `crate_doctor backup --backup-dir ...` is how people actually type it.
    for name, p in sub.choices.items():
        if name != 'init':
            p.add_argument('--db', dest='db', default=argparse.SUPPRESS, help=argparse.SUPPRESS)
            p.add_argument('--config', dest='config', default=argparse.SUPPRESS, help=argparse.SUPPRESS)
            p.add_argument('--backup-dir', dest='backup_dir', default=argparse.SUPPRESS,
                           help=argparse.SUPPRESS)
            p.add_argument('--keep-backups', dest='keep_backups', type=int,
                           default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    # Config supplies the defaults; anything you type on the command line still wins,
    # because argparse only applies a default when the flag is absent.
    pre, _rest = ap.parse_known_args()
    cfg, cfg_path = ({}, None) if pre.cmd in ('init', 'setup') else read_config(pre.config)
    if cfg:
        d = config_defaults(cfg)
        for name, p in sub.choices.items():
            if d.get(name):
                p.set_defaults(**d[name])
        if d.get('_global'):
            ap.set_defaults(**d['_global'])
        global AUDIO_VIBES
        AUDIO_VIBES = audio_vibes(cfg)

    a = ap.parse_args()
    global BACKUP_DIR, KEEP_BACKUPS
    if getattr(a, 'backup_dir', None):
        BACKUP_DIR = a.backup_dir
    if getattr(a, 'keep_backups', None) is not None:
        KEEP_BACKUPS = a.keep_backups
    if cfg_path and a.cmd != 'init':
        print(f"(config: {cfg_path})", file=sys.stderr)
    a.func(a)


if __name__ == '__main__':
    main()
