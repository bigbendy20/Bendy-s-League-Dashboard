"""
The storage layer, both backends.

Most of these are round-trip tests, because that's where the risk is. Going
from a parsed row to JSON and back loses type information silently: tuples
become lists, Timestamps become strings, numpy scalars become whatever
`json` decides. None of that raises on save. It raises much later, in a
groupby, on a page nobody opened during testing.

The SQL backend is exercised against stdlib `sqlite3` so the suite needs no
database and no extra dependency. The statements are deliberately restricted
to what both SQLite and Postgres accept.
"""
import datetime
import json
import sqlite3
import tempfile

import pandas as pd

import stats
import store
from conftest import make_match


def _row(match_id="NA1_1", **overrides):
    """A realistic parsed row — built by the real parser, not hand-written,
    so these tests break if `parse_match` starts emitting something the
    encoder can't handle."""
    match = make_match(match_id=match_id)
    row = stats.parse_match(match, "me-puuid")
    row.update(overrides)
    return row


class TestTupleColumns:
    def test_the_declared_list_matches_reality(self):
        """The guard that keeps the round-trip honest.

        If someone adds a tuple column to `parse_match` and doesn't add it
        here, it comes back from storage as a list — and pandas raises
        "unhashable type: 'list'" the first time something groups by it.
        This test fails at the source instead.
        """
        row = _row()
        actual = {k for k, v in row.items() if isinstance(v, tuple)}
        assert actual == set(store.TUPLE_COLUMNS), (
            f"parse_match tuple columns changed: {actual ^ set(store.TUPLE_COLUMNS)}"
        )

    def test_tuples_survive_a_round_trip(self):
        row = _row()
        restored = store._decode(store._encode(row))
        for column in store.TUPLE_COLUMNS:
            assert isinstance(restored[column], tuple), column

    def test_a_restored_frame_can_still_group_by_build(self):
        """The failure this prevents, end to end. `build` is a tuple of item
        ids used directly as a groupby key."""
        frame = store.rows_to_frame([store._decode(store._encode(_row()))])
        assert not stats.win_rate_by(frame, "build").empty


class TestEncoding:
    def test_timestamps_become_strings_and_come_back_as_datetimes(self):
        row = _row()
        assert isinstance(row["game_creation"], pd.Timestamp)
        encoded = json.loads(store._encode(row))
        assert isinstance(encoded["game_creation"], str)
        frame = store.rows_to_frame([store._decode(store._encode(row))])
        assert pd.api.types.is_datetime64_any_dtype(frame["game_creation"])

    def test_numpy_scalars_are_unwrapped(self):
        """`summoner1_id` arrives as a numpy int. `json` can't serialise one,
        so it has to be converted rather than left to `default=str`, which
        would turn a number into a string."""
        row = _row()
        encoded = json.loads(store._encode(row))
        assert isinstance(encoded["summoner1_id"], int)

    def test_none_survives(self):
        row = _row(opponent_champion=None)
        assert store._decode(store._encode(row))["opponent_champion"] is None

    def test_every_column_survives(self):
        """No silent column loss — the encoder iterates the row, so a type it
        can't handle would drop a field rather than fail."""
        row = _row()
        assert set(store._decode(store._encode(row))) == set(row)


class TestRowsToFrame:
    def test_empty_rows_give_an_empty_frame(self):
        assert store.rows_to_frame([]).empty

    def test_mixed_microsecond_precision_parses(self):
        """The bug real data found and every synthetic fixture missed.

        `Timestamp.isoformat()` omits microseconds when they're zero, so a
        genuine batch mixes "2026-03-17T18:57:30" with "...:30.123456".
        pandas infers a format from the first row unless told otherwise, then
        raises on the first row that doesn't match. In the 631-game history
        that was row 198 — well past anything a hand-written fixture covers.
        """
        rows = [
            {"match_id": "a", "game_creation": "2026-03-17T18:57:30"},
            {"match_id": "b", "game_creation": "2026-03-17T19:57:30.123456"},
            {"match_id": "c", "game_creation": "2026-03-17T20:57:30"},
        ]
        frame = store.rows_to_frame(rows)
        assert pd.api.types.is_datetime64_any_dtype(frame["game_creation"])
        assert list(frame["match_id"]) == ["a", "b", "c"]

    def test_rows_come_back_in_chronological_order(self):
        base = datetime.datetime(2026, 1, 5, 12)
        rows = [
            {"match_id": "b", "game_creation": (base + datetime.timedelta(hours=2)).isoformat()},
            {"match_id": "a", "game_creation": base.isoformat()},
        ]
        assert list(store.rows_to_frame(rows)["match_id"]) == ["a", "b"]


class _StoreContract:
    """Shared behaviour both backends must satisfy.

    Written once and run against each, because the whole point of the
    interface is that the app can't tell them apart. A backend that quietly
    diverges — say, one that re-inserts duplicates — would otherwise only
    show up in production.
    """

    def make(self):
        raise NotImplementedError

    def test_saves_and_loads(self):
        s = self.make()
        assert s.save_matches("p1", [_row("NA1_1"), _row("NA1_2")]) == 2
        assert len(s.load_matches("p1")) == 2

    def test_saving_never_removes_what_is_already_there(self):
        """A later batch adds to the record rather than replacing it. An
        implementation that wrote the batch as the new contents would pass
        any test that saves only once."""
        s = self.make()
        s.save_matches("p1", [_row(f"NA1_{i}") for i in range(5)])
        s.save_matches("p1", [_row(f"NA1_{i}") for i in range(5, 9)])
        assert len(s.load_matches("p1")) == 9

    def test_every_queue_is_kept_not_just_the_ranked_ones(self):
        """ARAM, Arena and the rotating modes are stored too. The site scopes
        them out of the main stats and gives them their own tab, but that is a
        *display* decision — filtering at the storage layer would discard
        games that can never be recovered, because Riot's match list only
        reaches back about six months.

        In the contract rather than on one backend: a mutant that filtered
        queues in `FileStore.save_matches` survived a version of this test
        that built a `SqlStore`. Both backends have to promise the same thing
        or the promise depends on the deployment.
        """
        s = self.make()
        s.save_matches("p1", [
            _row("NA1_1", queue_id=420), _row("NA1_2", queue_id=450),
            _row("NA1_3", queue_id=1700), _row("NA1_4", queue_id=490),
        ])
        stored = s.load_matches("p1")
        assert len(stored) == 4
        assert set(stored["queue_id"]) == {420, 450, 1700, 490}

    def test_unknown_profile_is_empty_not_an_error(self):
        assert self.make().load_matches("nobody").empty

    def test_duplicate_matches_are_not_stored_twice(self):
        """Every refresh re-sends overlapping ids. Without this the table
        grows without bound and win rates double-count."""
        s = self.make()
        s.save_matches("p1", [_row("NA1_1")])
        assert s.save_matches("p1", [_row("NA1_1")]) == 0
        assert len(s.load_matches("p1")) == 1

    def test_profiles_are_isolated(self):
        """The multi-user property. One friend's games must never appear in
        another's stats."""
        s = self.make()
        s.save_matches("p1", [_row("NA1_1")])
        s.save_matches("p2", [_row("NA1_2")])
        assert list(s.load_matches("p1")["match_id"]) == ["NA1_1"]
        assert list(s.load_matches("p2")["match_id"]) == ["NA1_2"]

    def test_known_ids_are_scoped_to_the_profile(self):
        """The multi-user failure mode with the nastiest symptom.

        `known_match_ids` is what the five-minute poll diffs against. If it
        leaks across profiles, the poll sees another friend's match ids as
        already-loaded and concludes there's nothing new — so that person's
        games are never fetched, and their profile silently stops updating
        while the app reports everything is fine.

        The isolation test above only covers `load_matches`, which is why
        this needed saying separately.
        """
        s = self.make()
        s.save_matches("p1", [_row("NA1_1")])
        s.save_matches("p2", [_row("NA1_2")])
        assert s.known_match_ids("p1") == {"NA1_1"}
        assert s.known_match_ids("p2") == {"NA1_2"}
        # ...and the consequence, spelled out: p2's game must read as new to p1.
        assert stats.unseen_match_ids(["NA1_2"], s.known_match_ids("p1")) == ["NA1_2"]

    def test_known_ids_support_the_cheap_poll(self):
        s = self.make()
        s.save_matches("p1", [_row("NA1_1"), _row("NA1_2")])
        assert s.known_match_ids("p1") == {"NA1_1", "NA1_2"}
        assert stats.unseen_match_ids(["NA1_3", "NA1_1"], s.known_match_ids("p1")) == ["NA1_3"]

    def test_rows_survive_storage_intact(self):
        s = self.make()
        s.save_matches("p1", [_row("NA1_1")])
        loaded = s.load_matches("p1").iloc[0]
        assert isinstance(loaded["build"], tuple)
        assert isinstance(loaded["game_creation"], pd.Timestamp)

    def test_stored_data_still_computes_stats(self):
        """The real acceptance test: does the analytics layer work on data
        that has been through storage?"""
        s = self.make()
        s.save_matches("p1", [_row(f"NA1_{i}") for i in range(5)])
        frame = s.load_matches("p1")
        assert stats.overall_win_rate(frame)[0] == 5
        assert not stats.win_rate_by(frame, "champion").empty


class TestFileStore(_StoreContract):
    def make(self):
        return store.FileStore(tempfile.mkdtemp(prefix="store-"))

    def test_a_corrupt_file_reads_as_empty(self):
        """A half-written file shouldn't brick a profile permanently."""
        s = self.make()
        s._path("p1").write_text("{not json")
        assert s.load_matches("p1").empty
        assert s.save_matches("p1", [_row("NA1_1")]) == 1

    def test_puuid_cannot_escape_the_directory(self):
        """puuids come from Riot, but a path separator in one would write
        outside the store's root."""
        s = self.make()
        assert s._path("a/../../b").parent == s.root


class TestSqlStore(_StoreContract):
    def make(self):
        return store.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")

    def test_game_counts_reflect_stored_matches(self):
        """Renamed from `profiles()` when real profile records arrived — this
        counts *data*, which is not the same as who is registered."""
        s = self.make()
        s.save_matches("p1", [_row("NA1_1"), _row("NA1_2")])
        s.save_matches("p2", [_row("NA1_3")])
        assert s.game_counts() == [
            {"puuid": "p1", "games": 2},
            {"puuid": "p2", "games": 1},
        ]

    def test_schema_creation_is_idempotent(self):
        """The app opens a store on every run; a second call must not fail
        or wipe anything."""
        conn = sqlite3.connect(":memory:")
        first = store.SqlStore(conn)
        first.save_matches("p1", [_row("NA1_1")])
        second = store.SqlStore(conn)
        assert len(second.load_matches("p1")) == 1

    def test_uses_only_portable_sql(self):
        """Guards the deployment story: these statements have to run on
        Postgres, which is not what they're tested against. SQLite-only
        syntax would pass every test here and fail on deploy."""
        import inspect

        source = inspect.getsource(store.SqlStore)
        for sqlite_only in ("AUTOINCREMENT", "INSERT OR IGNORE", "INSERT OR REPLACE",
                            "PRAGMA", "WITHOUT ROWID"):
            assert sqlite_only not in source, f"{sqlite_only} is SQLite-specific"


class _ProfileContract:
    """Profiles and rank history, required of both backends."""

    def make(self):
        raise NotImplementedError

    BENDY = {
        "puuid": "p1", "game_name": "Bendy", "tag_line": "NA1",
        "platform_region": "na1", "continental_region": "americas",
        "display_name": "Bendy", "email": "bendy@example.com",
    }

    def test_upsert_then_read_back(self):
        s = self.make()
        s.upsert_profile(self.BENDY)
        assert s.get_profile("p1")["game_name"] == "Bendy"

    def test_upsert_is_idempotent_and_updates(self):
        """The refresher calls this every cycle, so a second write must not
        duplicate the profile — and must apply changes."""
        s = self.make()
        s.upsert_profile(self.BENDY)
        s.upsert_profile({**self.BENDY, "display_name": "Bendy (jg)"})
        assert len(s.list_profiles()) == 1
        assert s.get_profile("p1")["display_name"] == "Bendy (jg)"

    def test_unknown_profile_is_none(self):
        assert self.make().get_profile("nobody") is None

    def test_profiles_sort_by_display_name_case_insensitively(self):
        """Two properties in one, and the fixture is picked so each can fail.

        The puuids are assigned so that sorting by puuid gives the *wrong*
        answer — an earlier version happened to order correctly under both,
        so breaking the sort changed nothing. And "ahri"/"Zed" separates a
        case-insensitive sort from a raw ASCII one, which would put the
        capital first.
        """
        s = self.make()
        s.upsert_profile({**self.BENDY, "puuid": "p2", "display_name": "ahri"})
        s.upsert_profile({**self.BENDY, "puuid": "p3", "display_name": "Zed"})
        assert [p["display_name"] for p in s.list_profiles()] == ["ahri", "Zed"]

    # ---- rank history ----
    ENTRY = {"queueType": "RANKED_SOLO_5x5", "tier": "EMERALD", "rank": "IV",
             "leaguePoints": 55, "wins": 204, "losses": 183}

    def test_first_snapshot_is_stored(self):
        s = self.make()
        assert s.save_rank_snapshot("p1", [self.ENTRY]) == 1
        assert len(s.load_rank_snapshots("p1")) == 1

    def test_unchanged_standing_is_not_stored_again(self):
        """The reason deduping exists. A five-minute poller would otherwise
        write 288 identical rows per person per day; the local app already
        had 25 snapshots covering only 6 distinct states."""
        s = self.make()
        s.save_rank_snapshot("p1", [self.ENTRY])
        assert s.save_rank_snapshot("p1", [self.ENTRY]) == 0
        assert len(s.load_rank_snapshots("p1")) == 1

    def test_an_lp_change_is_stored(self):
        s = self.make()
        s.save_rank_snapshot("p1", [self.ENTRY])
        assert s.save_rank_snapshot("p1", [{**self.ENTRY, "leaguePoints": 78}]) == 1
        assert len(s.load_rank_snapshots("p1")) == 2

    def test_a_win_is_a_change_even_at_the_same_lp(self):
        """Promotion series and decay can leave LP unchanged while the record
        moves. Comparing only LP would drop those rows."""
        s = self.make()
        s.save_rank_snapshot("p1", [self.ENTRY])
        assert s.save_rank_snapshot("p1", [{**self.ENTRY, "wins": 205}]) == 1

    def test_queues_are_tracked_separately(self):
        s = self.make()
        s.save_rank_snapshot("p1", [self.ENTRY])
        flex = {**self.ENTRY, "queueType": "RANKED_FLEX_SR"}
        assert s.save_rank_snapshot("p1", [flex]) == 1

    def test_rank_history_is_scoped_to_the_profile(self):
        s = self.make()
        s.save_rank_snapshot("p1", [self.ENTRY])
        assert s.load_rank_snapshots("p2") == []

    def test_no_entries_writes_nothing(self):
        assert self.make().save_rank_snapshot("p1", []) == 0


class TestFileStoreProfiles(_ProfileContract):
    def make(self):
        return store.FileStore(tempfile.mkdtemp(prefix="prof-"))


class TestSqlStoreProfiles(_ProfileContract):
    def make(self):
        return store.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")


class TestNothingIsEverDeleted:
    """The site's stated contract: data goes in and never comes out.

    This matters more here than it would elsewhere. Riot's match list is
    truncated to a rolling window — measured at 620 ids, about six months —
    so anything this database drops is not re-fetchable from anywhere. The
    1,858 games recovered from the local cache exist in exactly one place
    now. A pruning step added later "to keep the database tidy" would be
    silent, irreversible, and indistinguishable from normal operation.
    """

    def test_the_data_layer_contains_no_destructive_sql(self):
        """Read as source rather than exercised, because the failure being
        guarded against is a statement that doesn't exist yet."""
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        banned = ("delete from", "drop table", "truncate table")
        for name in ("store.py", "refresher.py", "refresh_job.py",
                     "import_cache.py", "upload_store.py", "app.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))

            # Docstrings are excluded by identity, not by indentation. The
            # first attempt skipped `col_offset == 0`, which only covers
            # module-level ones — and a nested docstring using the word
            # "truncated" to *describe* this very policy failed the test.
            # Prose about deletion is not deletion.
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    body = getattr(node, "body", [])
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstrings.add(id(body[0].value))

            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)):
                    continue
                if id(node) in docstrings:
                    continue
                lowered = " ".join(node.value.lower().split())
                for phrase in banned:
                    assert phrase not in lowered, f"{name}: {phrase!r}"



class TestProfileGoalRoundTrip:
    def _store(self):
        return store.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")

    def test_a_goal_survives_save_and_reload(self):
        import profiles

        s = self._store()
        s.upsert_profile(profiles.make_profile(
            "p1", "Bendy", "NA1", goal_tier="DIAMOND", goal_rank="IV"))
        assert s.get_profile("p1")["goal_tier"] == "DIAMOND"
        assert [p["goal_rank"] for p in s.list_profiles()] == ["IV"]

    def test_setting_one_profiles_goal_leaves_others_alone(self):
        import profiles

        s = self._store()
        s.upsert_profile(profiles.make_profile("p1", "Bendy", "NA1"))
        s.upsert_profile(profiles.make_profile("p2", "Friend", "NA1"))
        s.upsert_profile(profiles.make_profile(
            "p1", "Bendy", "NA1", goal_tier="MASTER"))
        assert s.get_profile("p1")["goal_tier"] == "MASTER"
        assert s.get_profile("p2")["goal_tier"] is None

    def test_an_existing_database_gains_the_new_columns(self):
        """The deployed Postgres was created before these columns existed, and
        `CREATE TABLE IF NOT EXISTS` does nothing to a table that's already
        there. Without a migration every SELECT naming them fails and the site
        shows nothing at all — verified against a copy of the real database
        before shipping, and pinned here."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE profiles (puuid TEXT PRIMARY KEY, game_name TEXT, "
            "tag_line TEXT, platform_region TEXT, continental_region TEXT, "
            "display_name TEXT, email TEXT)")
        conn.execute("INSERT INTO profiles (puuid, game_name) VALUES ('p1', 'Bendy')")
        conn.commit()

        s = store.SqlStore(conn, paramstyle="?")      # opening runs the migration
        columns = {r[1] for r in conn.execute("PRAGMA table_info(profiles)")}
        assert {"goal_tier", "goal_rank"} <= columns
        assert s.get_profile("p1")["game_name"] == "Bendy", "existing rows lost"

    def test_the_row_decoder_follows_the_field_list(self):
        """Schema, upsert, SELECT and decoder all read from
        `PROFILE_FIELDS`. When the decoder had its own copy, adding a column
        meant remembering four places, and missing this one drops the value
        silently on read."""
        import profiles

        assert store.SqlStore._profile_row(
            tuple(range(len(profiles.PROFILE_FIELDS)))
        ).keys() == dict.fromkeys(profiles.PROFILE_FIELDS).keys()
