"""
Copying the local store into the hosted one.

The operation runs once, by hand, against a database that already holds live
data — so the properties that matter are all about *not losing anything*:
re-running must be harmless, and rows the destination already has must
survive. Both are tested against two real stores rather than mocks, because
the whole point is that two independent implementations agree.
"""
import sqlite3

import refresh_job
import stats
import store
import upload_store
from conftest import make_match


def _store():
    return store.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")


def _row(match_id, puuid="p1"):
    return stats.parse_match(make_match(match_id=match_id, puuid=puuid), puuid)


PROFILE = {
    "puuid": "p1", "game_name": "Bendy", "tag_line": "NA1",
    "platform_region": "na1", "continental_region": "americas",
    "display_name": "Bendy", "email": "bendy@example.com",
}
ENTRY = {"queueType": "RANKED_SOLO_5x5", "tier": "EMERALD", "rank": "IV",
         "leaguePoints": 55, "wins": 200, "losses": 180}


def _seeded():
    src = _store()
    src.upsert_profile(PROFILE)
    src.save_matches("p1", [_row("NA1_1"), _row("NA1_2")])
    src.save_rank_snapshot("p1", [ENTRY])
    return src


class TestCopyStore:
    def test_everything_transfers(self):
        src, dst = _seeded(), _store()
        report = upload_store.copy_store(src, dst)
        assert report["profiles"] == 1
        assert report["matches"] == 2
        # The reported counts are asserted alongside the stored rows. Only
        # checking the rows left the summary untested: zeroing the snapshot
        # tally still wrote the snapshots, so the upload would print
        # "0 rank snapshot(s)" while having copied them — the kind of
        # discrepancy that makes you re-run a working operation.
        assert report["snapshots"] == 1
        assert len(dst.load_matches("p1")) == 2
        assert dst.get_profile("p1")["display_name"] == "Bendy"
        assert len(dst.load_rank_snapshots("p1")) == 1

    def test_matches_survive_intact(self):
        """Through two encode/decode cycles now — the tuple columns and the
        timestamps have to come out the far side still usable."""
        src, dst = _seeded(), _store()
        upload_store.copy_store(src, dst)
        loaded = dst.load_matches("p1").iloc[0]
        assert isinstance(loaded["build"], tuple)
        assert not stats.win_rate_by(dst.load_matches("p1"), "build").empty

    def test_re_running_changes_nothing(self):
        """An upload that dies halfway must be safe to simply run again."""
        src, dst = _seeded(), _store()
        upload_store.copy_store(src, dst)
        second = upload_store.copy_store(src, dst)
        assert second["matches"] == 0
        assert second["snapshots"] == 0
        assert len(dst.load_matches("p1")) == 2

    def test_rows_only_in_the_destination_are_kept(self):
        """The live refresher keeps running while the backfill does. Games it
        collected must not be wiped by a later upload — this is a push, not a
        mirror."""
        src, dst = _seeded(), _store()
        dst.upsert_profile(PROFILE)
        dst.save_matches("p1", [_row("NA1_99")])
        upload_store.copy_store(src, dst)
        assert set(dst.load_matches("p1")["match_id"]) == {"NA1_1", "NA1_2", "NA1_99"}

    def test_a_profile_without_a_puuid_is_skipped(self):
        """Bootstrap profiles have no puuid, so there's no key to copy their
        data under. Counted rather than silently dropped."""
        src, dst = _store(), _store()
        src.upsert_profile({**PROFILE, "puuid": None})
        report = upload_store.copy_store(src, dst)
        assert report["skipped"] == 1
        assert report["profiles"] == 0

    def test_an_empty_source_is_not_an_error(self):
        assert upload_store.copy_store(_store(), _store())["profiles"] == 0

    def test_dry_run_writes_nothing(self):
        src, dst = _seeded(), _store()
        report = upload_store.copy_store(src, dst, dry_run=True)
        assert report["profiles"] == 1
        assert dst.load_matches("p1").empty
        assert dst.get_profile("p1") is None

    def test_progress_is_reported_per_profile(self):
        """Eight profiles and thousands of matches shouldn't look frozen."""
        messages = []
        upload_store.copy_store(_seeded(), _store(), on_progress=messages.append)
        assert len(messages) == 1 and "Bendy" in messages[0]

    def test_rank_snapshot_dedupe_applies_at_the_destination(self):
        """Snapshots are replayed one at a time so the destination's own
        dedupe decides what to keep — an unchanged standing already there
        shouldn't be duplicated by the upload."""
        src, dst = _seeded(), _store()
        dst.save_rank_snapshot("p1", [ENTRY])
        upload_store.copy_store(src, dst)
        assert len(dst.load_rank_snapshots("p1")) == 1


class TestCli:
    def test_refuses_to_copy_a_local_store_onto_itself(self):
        """Both sides defaulting to the same SQLite file would report a
        successful copy of nothing."""
        assert upload_store.main(["--to", "sqlite:///whatever.db"]) == 2


class TestSyncFolderWarning:
    """SQLite in a syncing folder can be corrupted mid-write. The project
    genuinely lives in OneDrive here, so this warns rather than refuses."""

    def test_detects_common_sync_clients(self):
        for path in ("C:/Users/x/OneDrive/proj/data/board.db",
                     "/home/x/Dropbox/board.db",
                     "/Users/x/Google Drive/board.db"):
            assert store.sync_folder_warning(path), path

    def test_quiet_for_an_ordinary_path(self):
        assert store.sync_folder_warning("/home/x/projects/board.db") == ""
        assert store.sync_folder_warning("C:/dev/lol/data/board.db") == ""

    def test_the_warning_says_what_to_do(self):
        warning = store.sync_folder_warning("C:/Users/x/OneDrive/data/board.db")
        assert "pause" in warning.lower() or "DATABASE_URL" in warning


class TestDestinationFromEnv:
    """Where the connection string comes from.

    On the command line it ends up in shell history and in any `.bat` that
    wraps it — and `.bat` files are not gitignored, in a public repo. `.env`
    is, so that's where it lives.
    """

    def test_the_destination_falls_back_to_postgres_url(self):
        import os
        import tempfile

        import upload_store

        directory = tempfile.mkdtemp()
        with open(os.path.join(directory, ".env"), "w", encoding="utf-8") as handle:
            handle.write("POSTGRES_URL=postgresql://user:pw@host/db\n")

        seen = {}

        def spy(url=None, base_dir=None):
            seen.setdefault("urls", []).append(url)
            raise SystemExit(0)

        import env_file
        import refresh_job

        real_open, real_load = refresh_job.open_store, env_file.load
        refresh_job.open_store = spy
        env_file.load = lambda *a, **k: real_load(os.path.join(directory, ".env"))
        saved = os.environ.pop("POSTGRES_URL", None)
        try:
            try:
                upload_store.main([])
            except SystemExit:
                pass
            assert os.environ.get("POSTGRES_URL") == "postgresql://user:pw@host/db"
        finally:
            refresh_job.open_store = real_open
            env_file.load = real_load
            os.environ.pop("POSTGRES_URL", None)
            if saved is not None:
                os.environ["POSTGRES_URL"] = saved

    def test_no_destination_anywhere_is_a_clear_error(self):
        import os

        import env_file
        import upload_store

        real_load = env_file.load
        env_file.load = lambda *a, **k: []
        saved = os.environ.pop("POSTGRES_URL", None)
        try:
            assert upload_store.main([]) == 2
        finally:
            env_file.load = real_load
            if saved is not None:
                os.environ["POSTGRES_URL"] = saved

    def test_it_does_not_reuse_database_url(self):
        """DATABASE_URL is what the *local* store reads. If the upload used
        the same name, setting it would point source and destination at the
        same database — a copy onto itself that reports success and moves
        nothing."""
        import ast
        import os
        import pathlib

        path = pathlib.Path(__file__).resolve().parent.parent / "upload_store.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [
            node.args[0].value for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and node.args and isinstance(node.args[0], ast.Constant)
        ]
        assert "POSTGRES_URL" in names
        assert "DATABASE_URL" not in names


class TestHostedCheck:
    """The "did it actually land?" report.

    Its value is separating two failures that look identical from a browser:
    an empty database, and a database the site isn't reading. Both present as
    a page with no games.
    """

    def _store_with(self, profiles_and_counts):
        import sqlite3

        import stats
        import store as store_module
        from conftest import make_match

        s = store_module.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")
        for puuid, name, count in profiles_and_counts:
            s.upsert_profile({
                "puuid": puuid, "display_name": name, "game_name": name,
                "tag_line": "NA1", "platform_region": "na1",
                "continental_region": "americas", "email": None})
            rows = [stats.parse_match(
                make_match(match_id=f"{puuid}_{i}", puuid=puuid), puuid)
                for i in range(count)]
            if rows:
                s.save_matches(puuid, rows)
        return s

    def test_counts_and_ordering(self):
        import check_hosted

        s = self._store_with([("p1", "Bendy", 3), ("p2", "Friend", 7)])
        rows = check_hosted.summarise(s)
        assert [r[0] for r in rows] == ["Friend", "Bendy"], "busiest first"
        assert [r[1] for r in rows] == [7, 3]

    def test_a_profile_with_no_matches_is_shown_not_hidden(self):
        """Someone seeded but never fetched is exactly what this is meant to
        surface. Dropping them from the report would hide the problem it
        exists to find."""
        import check_hosted

        rows = check_hosted.summarise(self._store_with([("p1", "Bendy", 0)]))
        assert rows == [("Bendy", 0, None, None)]

    def test_an_empty_database_reports_nothing_rather_than_failing(self):
        import check_hosted
        import sqlite3
        import store as store_module

        empty = store_module.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")
        assert check_hosted.summarise(empty) == []
