"""
Where match data lives. One interface, two backends.

The local app kept raw Riot match JSON on disk — 83 KB per game, 54 MB for a
single 631-game history. That works when there's one user and a real
filesystem. It doesn't survive the move to a hosted site: Streamlit Community
Cloud has an ephemeral disk, so every restart would wipe the cache and
trigger a multi-hour re-fetch against a rate-limited key.

**The fix is to stop storing what we don't read.** Nothing in the app ever
touches the raw JSON after `parse_match` runs; it was kept only to avoid
re-fetching. Storing the *parsed row* instead is 198x smaller — 1.0 KB per
game rather than 83 — which puts seven players' full histories at roughly
7 MB, comfortably inside a free 500 MB Postgres tier.

The trade, stated plainly: parsed rows are a one-way door. If a future stat
needs a Riot field `parse_match` doesn't currently extract, it can't be
recomputed from the store — those matches have to be pulled again. That has
already happened once here (ally/enemy champions for the live tips were free
*because* the raw JSON was still on disk). Two hedges: the whole parsed row
is stored rather than just the columns in use today, and the local archive of
raw JSON stays on the developer's machine.

**Schema note.** Rows go in as a JSON blob keyed by (puuid, match_id), with
`game_creation` and `queue_id` lifted into real columns for ordering and
filtering. That means adding a column to `parse_match` needs no migration —
it simply appears in the blob. For a project that has added columns in most
rounds, that's worth more than a strict schema.

**Why SQL rather than an ORM.** The SQL here is deliberately plain so the
same statements run on both SQLite and Postgres: `CREATE TABLE IF NOT
EXISTS`, `INSERT ... ON CONFLICT DO NOTHING`. That lets the entire backend be
tested offline against stdlib `sqlite3` while deploying against Postgres,
with no dependency that has to be installed to run the test suite.
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd

import env_file
import profiles as _profiles


DEFAULT_LOCAL_DB = "data/board.db"

# Columns `parse_match` emits as tuples. They must come back as tuples, not
# lists, because JSON has no tuple type and pandas cannot group by an
# unhashable value — `win_rate_by(df, "build")` raises on lists. This is the
# round-trip's sharpest edge, and `tests/test_store.py` asserts the list here
# still matches what `parse_match` actually produces, so adding a new tuple
# column can't silently break grouping.
TUPLE_COLUMNS = (
    "ally_champions",
    "build",
    "enemy_champions",
    "summoner_combo",
    "teammate_names",
    "teammate_puuids",
)


def _encode(row: dict) -> str:
    """Parsed row -> JSON text.

    Timestamps become ISO strings and numpy scalars become Python numbers;
    `default=str` catches anything else rather than raising mid-save, since a
    single odd value shouldn't cost a whole batch.
    """
    clean = {}
    for key, value in row.items():
        if isinstance(value, pd.Timestamp):
            clean[key] = value.isoformat()
        elif value is None or isinstance(value, (str, bool, int, float)):
            clean[key] = value
        elif isinstance(value, (tuple, list)):
            clean[key] = list(value)
        elif hasattr(value, "item"):      # numpy scalar
            clean[key] = value.item()
        else:
            clean[key] = value
    return json.dumps(clean, default=str)


def _decode(blob: str) -> dict:
    row = json.loads(blob)
    for column in TUPLE_COLUMNS:
        if isinstance(row.get(column), list):
            row[column] = tuple(row[column])
    return row


def rows_to_frame(rows: list) -> pd.DataFrame:
    """Decoded rows -> the DataFrame the rest of the app expects.

    `game_creation` is re-parsed to a datetime because every time-based stat
    does `.dt` operations on it, and a column of ISO strings would fail at the
    call site rather than here.
    """
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "game_creation" in df.columns:
        # `format="ISO8601"` is load-bearing, not defensive. `isoformat()`
        # omits the microseconds when they happen to be zero, so a real batch
        # contains both "2026-03-17T18:57:30" and "...:30.123456". Without an
        # explicit format pandas infers one from the first row and then raises
        # on the first row that differs — which in the 631-game history was
        # row 198. Every synthetic fixture here used a single shape, so the
        # unit tests passed and only real data exposed it.
        df["game_creation"] = pd.to_datetime(df["game_creation"], format="ISO8601")
        df = df.sort_values("game_creation").reset_index(drop=True)
    return df


# The fields that identify a rank standing. A snapshot is only worth storing
# when one of these changes: the local app wrote one per manual refresh, which
# was already mostly redundant (25 snapshots covering 6 distinct states), and a
# server polling every five minutes would write 288 per person per day, nearly
# all identical. Deduping keeps the log meaningful *and* makes LP-per-game
# attribution tractable, since every stored row is an actual change.
STANDING_FIELDS = ("queue_type", "tier", "rank", "league_points", "wins", "losses")


def _standing(entry: dict) -> tuple:
    return tuple(entry.get(f) for f in STANDING_FIELDS)


def _snapshot_rows(league_entries: list, timestamp: str) -> list:
    """league-v4 entries -> flat snapshot rows."""
    return [{
        "timestamp": timestamp,
        "queue_type": e.get("queueType"),
        "tier": e.get("tier"),
        "rank": e.get("rank"),
        "league_points": e.get("leaguePoints"),
        "wins": e.get("wins"),
        "losses": e.get("losses"),
    } for e in league_entries]


# Folder names that indicate a file-syncing client. SQLite keeps a database
# consistent using file locks and rollback journals, and a sync client that
# copies the file mid-write — or restores an older version behind your back —
# can corrupt it. This is a documented hazard for SQLite, Access and similar,
# not a theoretical one.
#
# Detected by path rather than prevented, because the project genuinely does
# live in OneDrive here and refusing to run would be worse than saying so.
SYNC_FOLDER_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud")


def sync_folder_warning(path) -> str:
    """A warning if `path` looks like it's inside a syncing folder, else ''.

    Returned rather than printed so the caller decides where it goes, and so
    it can be tested without capturing stdout.
    """
    lowered = str(path).replace("\\", "/").lower()
    for marker in SYNC_FOLDER_MARKERS:
        if f"/{marker}" in lowered or lowered.startswith(marker):
            return (
                f"Warning (not an error — continuing). This database is "
                f"inside a {marker.title()} folder, and sync clients can "
                f"corrupt an open SQLite file. To move it, put this line in "
                f".env:  DATABASE_URL=sqlite:///C:/temp/board.db"
            )
    return ""


class FileStore:
    """Parsed rows as one JSON file per profile. The local/offline backend.

    Kept because it needs no database to run the app on your own machine, and
    because it's the fallback if the hosted store is unreachable.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, puuid: str) -> Path:
        # puuids are Riot-generated base64-ish ids; slashes would escape the
        # directory, so they're replaced rather than trusted.
        safe = str(puuid).replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.json"

    def known_match_ids(self, puuid: str) -> set:
        return {row.get("match_id") for row in self._read(puuid)}

    def _read(self, puuid: str) -> list:
        path = self._path(puuid)
        if not path.exists():
            return []
        try:
            return [_decode(json.dumps(r)) for r in json.loads(path.read_text())]
        except json.JSONDecodeError:
            # A half-written file shouldn't brick the profile — treat it as
            # empty and let the next save rewrite it.
            return []

    def save_matches(self, puuid: str, rows: list) -> int:
        """Add rows that aren't already stored. Returns how many were new."""
        existing = {r.get("match_id"): r for r in self._read(puuid)}
        added = 0
        for row in rows:
            mid = row.get("match_id")
            if mid and mid not in existing:
                existing[mid] = json.loads(_encode(row))
                added += 1
        self._path(puuid).write_text(json.dumps(list(existing.values())))
        return added

    def load_matches(self, puuid: str) -> pd.DataFrame:
        return rows_to_frame(self._read(puuid))

    # ---- profiles ----
    def _profiles_path(self) -> Path:
        return self.root / "_profiles.json"

    def _all_profiles(self) -> dict:
        path = self._profiles_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}

    def upsert_profile(self, profile: dict) -> None:
        profiles = self._all_profiles()
        puuid = profile["puuid"]
        profiles[puuid] = {**profiles.get(puuid, {}), **profile}
        self._profiles_path().write_text(json.dumps(profiles, indent=2))

    def list_profiles(self) -> list:
        return sorted(self._all_profiles().values(),
                      key=lambda p: (p.get("display_name") or "").lower())

    def get_profile(self, puuid: str):
        return self._all_profiles().get(puuid)

    # ---- rank history ----
    def _rank_path(self, puuid: str) -> Path:
        safe = str(puuid).replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}_rank.json"

    def save_rank_snapshot(self, puuid: str, league_entries: list, timestamp=None) -> int:
        stamp = timestamp or pd.Timestamp.utcnow().isoformat()
        history = self.load_rank_snapshots(puuid)
        latest = {}
        for row in history:
            latest[row.get("queue_type")] = row
        added = []
        for row in _snapshot_rows(league_entries, stamp):
            previous = latest.get(row["queue_type"])
            if previous and _standing(previous) == _standing(row):
                continue
            added.append(row)
        if added:
            self._rank_path(puuid).write_text(json.dumps(history + added, indent=2))
        return len(added)

    def load_rank_snapshots(self, puuid: str) -> list:
        path = self._rank_path(puuid)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return []


class SqlStore:
    """Parsed rows in a SQL table. The hosted backend.

    Takes an open DBAPI connection rather than a URL so the caller owns the
    lifecycle, and so tests can hand it a `sqlite3` connection. `paramstyle`
    is the one dialect difference that matters: SQLite wants `?`, Postgres
    wants `%s`.
    """

    def __init__(self, connection, paramstyle: str = "?"):
        self.conn = connection
        self.p = paramstyle
        self._create_schema()

    def _create_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
                puuid         TEXT NOT NULL,
                match_id      TEXT NOT NULL,
                game_creation TEXT,
                queue_id      INTEGER,
                data          TEXT NOT NULL,
                PRIMARY KEY (puuid, match_id)
            )
            """
        )
        # Ordering by recency per profile is the app's most common access
        # pattern by a wide margin — every page sorts by game_creation.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS matches_by_time "
            "ON matches (puuid, game_creation)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                puuid             TEXT PRIMARY KEY,
                game_name         TEXT,
                tag_line          TEXT,
                platform_region   TEXT,
                continental_region TEXT,
                display_name      TEXT,
                email             TEXT,
                goal_tier         TEXT,
                goal_rank         TEXT
            )
            """
        )
        # `email` is how a signed-in Google account maps to a League profile.
        # Nullable, because a profile can exist before its owner has ever
        # logged in — the backfill shouldn't wait on that.
        # `CREATE TABLE IF NOT EXISTS` does nothing to a table that already
        # exists, so a new column has to be added explicitly or the deployed
        # database keeps the old shape and every SELECT naming it fails. Both
        # backends support `ADD COLUMN IF NOT EXISTS`; wrapped anyway, because
        # an older SQLite would raise and a failed migration must not stop the
        # app from opening a database that is otherwise fine.
        for column in ("goal_tier", "goal_rank"):
            try:
                cur.execute(f"ALTER TABLE profiles ADD COLUMN IF NOT EXISTS {column} TEXT")
            except Exception:
                try:
                    cur.execute(f"ALTER TABLE profiles ADD COLUMN {column} TEXT")
                except Exception:
                    pass          # already present

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS rank_snapshots (
                puuid         TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                queue_type    TEXT,
                tier          TEXT,
                rank          TEXT,
                league_points INTEGER,
                wins          INTEGER,
                losses        INTEGER
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS snapshots_by_time "
            "ON rank_snapshots (puuid, timestamp)"
        )
        self.conn.commit()

    def known_match_ids(self, puuid: str) -> set:
        cur = self.conn.cursor()
        cur.execute(f"SELECT match_id FROM matches WHERE puuid = {self.p}", (puuid,))
        return {row[0] for row in cur.fetchall()}

    def save_matches(self, puuid: str, rows: list) -> int:
        """Insert rows, ignoring ones already present.

        `ON CONFLICT DO NOTHING` rather than a read-then-write: it's one round
        trip, and it's correct even if two refreshers run at once — which they
        will, once the site has several people on it.
        """
        payload = []
        for row in rows:
            mid = row.get("match_id")
            if not mid:
                continue
            created = row.get("game_creation")
            payload.append((
                puuid,
                mid,
                created.isoformat() if isinstance(created, pd.Timestamp) else created,
                row.get("queue_id"),
                _encode(row),
            ))
        if not payload:
            return 0
        before = len(self.known_match_ids(puuid))
        cur = self.conn.cursor()
        placeholders = ", ".join([self.p] * 5)
        cur.executemany(
            f"INSERT INTO matches (puuid, match_id, game_creation, queue_id, data) "
            f"VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            payload,
        )
        self.conn.commit()
        return len(self.known_match_ids(puuid)) - before

    def load_matches(self, puuid: str) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT data FROM matches WHERE puuid = {self.p} ORDER BY game_creation",
            (puuid,),
        )
        return rows_to_frame([_decode(row[0]) for row in cur.fetchall()])

    def game_counts(self) -> list:
        """Every puuid with match data, and how many games each has.

        Distinct from `list_profiles()`: this reflects what's actually stored,
        which can differ from who's registered — a newly added friend has a
        profile and no games until the first backfill finishes.
        """
        cur = self.conn.cursor()
        cur.execute(
            "SELECT puuid, COUNT(*) FROM matches GROUP BY puuid ORDER BY COUNT(*) DESC"
        )
        return [{"puuid": p, "games": n} for p, n in cur.fetchall()]

    # ---- profiles ----
    def upsert_profile(self, profile: dict) -> None:
        """Add or update a profile. Idempotent, so a refresher can call it
        every cycle without checking first."""
        cur = self.conn.cursor()
        columns = _profiles.PROFILE_FIELDS
        values = tuple(profile.get(c) for c in columns)
        placeholders = ", ".join([self.p] * len(columns))
        updates = ", ".join(f"{c} = excluded.{c}" for c in columns[1:])
        cur.execute(
            f"INSERT INTO profiles ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT (puuid) DO UPDATE SET {updates}",
            values,
        )
        self.conn.commit()

    def list_profiles(self) -> list:
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT {', '.join(_profiles.PROFILE_FIELDS)} FROM profiles "
            "ORDER BY LOWER(COALESCE(display_name, game_name))"
        )
        return [self._profile_row(r) for r in cur.fetchall()]

    def get_profile(self, puuid: str):
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT {', '.join(_profiles.PROFILE_FIELDS)} "
            f"FROM profiles WHERE puuid = {self.p}",
            (puuid,),
        )
        row = cur.fetchone()
        return self._profile_row(row) if row else None

    @staticmethod
    def _profile_row(row) -> dict:
        """Row tuple -> profile dict.

        Keyed off `profiles.PROFILE_FIELDS` rather than a second hand-written
        list. The two had to agree and nothing checked that they did: adding a
        column meant editing the schema, the upsert, two SELECTs and this
        tuple, and forgetting the last one silently drops the value on the way
        out — the record saves, and reads back without it.
        """
        return dict(zip(_profiles.PROFILE_FIELDS, row))

    # ---- rank history ----
    def save_rank_snapshot(self, puuid: str, league_entries: list, timestamp=None) -> int:
        """Store a standing, but only if it changed. Returns rows written."""
        stamp = timestamp or pd.Timestamp.utcnow().isoformat()
        latest = {}
        for row in self.load_rank_snapshots(puuid):
            latest[row.get("queue_type")] = row
        cur = self.conn.cursor()
        written = 0
        for row in _snapshot_rows(league_entries, stamp):
            previous = latest.get(row["queue_type"])
            if previous and _standing(previous) == _standing(row):
                continue
            cur.execute(
                f"INSERT INTO rank_snapshots (puuid, timestamp, queue_type, tier, "
                f"rank, league_points, wins, losses) "
                f"VALUES ({', '.join([self.p] * 8)})",
                (puuid, row["timestamp"], row["queue_type"], row["tier"],
                 row["rank"], row["league_points"], row["wins"], row["losses"]),
            )
            written += 1
        self.conn.commit()
        return written

    def load_rank_snapshots(self, puuid: str) -> list:
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT timestamp, queue_type, tier, rank, league_points, wins, losses "
            f"FROM rank_snapshots WHERE puuid = {self.p} ORDER BY timestamp",
            (puuid,),
        )
        keys = ("timestamp", "queue_type", "tier", "rank",
                "league_points", "wins", "losses")
        return [dict(zip(keys, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------- factory ---
# Lives here rather than in `refresh_job` because *both* the site and the jobs
# need it, and for a while only the jobs had it. `app.py` hardcoded
# `FileStore(<app dir>/data/profiles)` and never looked at `DATABASE_URL` —
# so the deployed site would have read an empty local directory on an
# ephemeral disk and shown eight profiles with no games, while the database it
# was supposed to be reading sat there full. One factory, one answer to "which
# store are we using", used by everything.


def sqlite_path(url, base_dir=None) -> str:
    """A `sqlite:` URL (or an empty one) -> the file to open.

    Separated so it can be tested without touching a disk. The one thing that
    can go wrong is landing on the *wrong file*, and a test that only checks
    "a store came back" cannot see that. The Windows form in particular —
    `sqlite:///C:/temp/board.db`, the string DEPLOY.md suggests for keeping
    the database out of OneDrive — has to survive the leading-slash strip with
    its drive letter intact, or it silently resolves inside the project and
    the advice does nothing.

    Forms:
      ``""``                      -> `data/board.db` under `base_dir`
      ``sqlite:///rel/path.db``   -> relative to `base_dir`
      ``sqlite:////abs/path.db``  -> absolute (POSIX)
      ``sqlite:///C:/path.db``    -> absolute (Windows drive letter)

    `base_dir` defaults to this file's directory — the code, never the working
    directory, so `python lol-dashboard/refresh_job.py` from the parent folder
    opens the same database as a double-clicked `.bat` that cd's first.
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    path = DEFAULT_LOCAL_DB
    if url.startswith("sqlite"):
        path = url.split("://", 1)[1].lstrip("/") or DEFAULT_LOCAL_DB
        if url.startswith("sqlite:////"):
            path = "/" + path
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    return path


def open_store(url=None, base_dir=None):
    """Open whichever store `DATABASE_URL` points at.

      * unset            -> a local SQLite file (`data/board.db`)
      * `sqlite:///path` -> that SQLite file
      * `postgres://…`   -> the hosted database

    `base_dir` is where a relative SQLite path resolves. It defaults to this
    file's directory, and callers that live somewhere else — or that are being
    run against a sandbox, as the startup tests are — pass their own.
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    if url is None:
        # `.env` may name a database as well as the API key, and writing it
        # there is how someone makes the setting stick across terminals.
        # A real environment variable still wins.
        env_file.load(os.path.join(base_dir, ".env"))
        url = os.getenv("DATABASE_URL", "")
    url = (url or "").strip()

    if not url or url.startswith("sqlite"):
        import sqlite3

        path = sqlite_path(url, base_dir)
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        warning = sync_folder_warning(path)
        if warning:
            print(f"  ! {warning}", file=sys.stderr)
        try:
            return SqlStore(sqlite3.connect(path), paramstyle="?")
        except sqlite3.Error as exc:
            # SQLite's own message here is "disk I/O error" or "unable to open
            # database file" — true, and useless. The cause that actually
            # produces it is a cloud-synced folder: OneDrive's Files
            # On-Demand leaves a placeholder where the file should be.
            raise SystemExit(
                f"Could not open the database at:\n    {path}\n"
                f"SQLite said: {exc}\n\n"
                f"This is usually a cloud-synced folder (OneDrive, Dropbox). "
                f"Move the database somewhere local by adding this line to "
                f".env, then run again:\n"
                f"    DATABASE_URL=sqlite:///C:/temp/board.db"
            ) from exc

    # Imported lazily so the test suite and local use need no driver.
    import psycopg

    return SqlStore(psycopg.connect(url), paramstyle="%s")
