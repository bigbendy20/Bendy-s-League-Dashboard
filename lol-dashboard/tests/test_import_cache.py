"""
Importing matches from the local JSON cache.

This exists because of a measured limitation rather than a bug: Riot's match
*list* endpoint stops at 620 ids for the primary account, ending 2026-02-07,
and an explicit older time window returns nothing. The matches themselves are
still served individually — they're unlisted, not deleted — and 1,123 of them
are already cached on disk, back to September 2024.

That makes the cache the only route to roughly two thirds of the record, and
these tests are mostly about not damaging it: import must be repeatable,
must not attribute a game to the wrong player, and must not fall over on one
unreadable file.
"""
import json
import os
import sqlite3
import tempfile

import import_cache
import store
from conftest import make_match


def _store():
    return store.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")


def _profile(puuid, name):
    return {"puuid": puuid, "display_name": name, "game_name": name,
            "tag_line": "NA1", "platform_region": "na1",
            "continental_region": "americas", "email": None}


def _cache(matches, extra_files=None):
    """Write matches to a temp directory; return a glob pattern for it."""
    directory = tempfile.mkdtemp()
    for match in matches:
        path = os.path.join(directory, f"{match['metadata']['matchId']}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(match, handle)
    for name, text in (extra_files or {}).items():
        with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
            handle.write(text)
    return os.path.join(directory, "*.json")


class TestImport:
    def test_cached_matches_are_stored(self):
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        pattern = _cache([make_match(match_id=f"NA1_{i}", puuid="p1")
                          for i in range(5)])
        report = import_cache.import_matches(s, pattern)
        assert report["imported"] == 5
        assert len(s.load_matches("p1")) == 5

    def test_matches_already_stored_are_skipped(self):
        """The property that makes re-running safe, and that makes this
        usable alongside the refresher rather than instead of it."""
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        pattern = _cache([make_match(match_id=f"NA1_{i}", puuid="p1")
                          for i in range(5)])
        import_cache.import_matches(s, pattern)
        again = import_cache.import_matches(s, pattern)
        assert again["imported"] == 0
        assert len(s.load_matches("p1")) == 5

    def test_a_match_is_attributed_to_every_tracked_participant(self):
        """One game with two roster members in it belongs to both records.

        The distinguishing fixture: if the importer stopped at the first
        match, or keyed on the match rather than the player, this passes for
        one of them and silently loses the other's copy.
        """
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        s.upsert_profile(_profile("p2", "Friend"))
        match = make_match(match_id="NA1_1", puuid="p1")
        # Second player in the same game. Set on `info.participants`, which is
        # what `parse_match` reads — `metadata.participants` is a parallel
        # list that the fixture doesn't populate, and depending on it was the
        # bug this test caught in the importer's first version.
        match["info"]["participants"][1]["puuid"] = "p2"
        report = import_cache.import_matches(s, _cache([match]))
        assert report["imported"] == 2
        assert len(s.load_matches("p1")) == 1
        assert len(s.load_matches("p2")) == 1

    def test_matches_for_untracked_players_are_ignored(self):
        """The cache holds every participant of every game — nine other
        people per match. Importing them all would fill the database with
        strangers."""
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        pattern = _cache([make_match(match_id="NA1_1", puuid="stranger")])
        report = import_cache.import_matches(s, pattern)
        assert report["imported"] == 0

    def test_a_corrupt_file_does_not_abort_the_import(self):
        """Thousands of files written over months; one truncated write must
        not cost the other 1,122."""
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        pattern = _cache(
            [make_match(match_id=f"NA1_{i}", puuid="p1") for i in range(3)],
            extra_files={"broken.json": "{not json", "empty.json": ""},
        )
        report = import_cache.import_matches(s, pattern)
        assert report["imported"] == 3

    def test_a_dry_run_writes_nothing(self):
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        pattern = _cache([make_match(match_id=f"NA1_{i}", puuid="p1")
                          for i in range(4)])
        report = import_cache.import_matches(s, pattern, dry_run=True)
        assert report["imported"] == 4
        assert len(s.load_matches("p1")) == 0, "dry run wrote to the store"

    def test_no_profiles_means_nothing_happens(self):
        pattern = _cache([make_match(match_id="NA1_1", puuid="p1")])
        report = import_cache.import_matches(_store(), pattern)
        assert report["imported"] == 0

    def test_the_report_names_each_player(self):
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        s.upsert_profile(_profile("p2", "Friend"))
        matches = [make_match(match_id=f"NA1_{i}", puuid="p1") for i in range(3)]
        matches.append(make_match(match_id="NA1_9", puuid="p2"))
        report = import_cache.import_matches(s, _cache(matches))
        assert report["per_profile"] == {"Bendy": 3, "Friend": 1}

    def test_progress_is_reported(self):
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        pattern = _cache([make_match(match_id=f"NA1_{i}", puuid="p1")
                          for i in range(1100)])
        messages = []
        import_cache.import_matches(s, pattern, on_progress=messages.append)
        assert messages


class TestCacheReading:
    def test_every_file_is_read_once(self):
        pattern = _cache([make_match(match_id=f"NA1_{i}", puuid="p1")
                          for i in range(7)])
        assert len(list(import_cache.cached_matches(pattern))) == 7

    def test_an_empty_cache_is_not_an_error(self):
        assert list(import_cache.cached_matches(
            os.path.join(tempfile.mkdtemp(), "*.json"))) == []


class TestRepeatedImportsAreCheap:
    def test_a_second_run_does_not_reparse_what_it_has(self):
        """`save_matches` already ignores duplicates, so skipping known ids
        changes no *output* — which is why a mutant removing the check
        survived every other test here. What it changes is work: without it,
        a re-run parses all 3,085 cached files against all eight profiles
        again to discover it has nothing to do.

        So the claim being pinned is the one the guard actually makes, and it
        needs a counter rather than an assertion about results.
        """
        import stats as stats_module

        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        pattern = _cache([make_match(match_id=f"NA1_{i}", puuid="p1")
                          for i in range(20)])

        real_parse = import_cache.stats.parse_match
        calls = {"n": 0}

        def counting(match, puuid):
            calls["n"] += 1
            return real_parse(match, puuid)

        import_cache.stats.parse_match = counting
        try:
            import_cache.import_matches(s, pattern)
            first = calls["n"]
            calls["n"] = 0
            import_cache.import_matches(s, pattern)
            second = calls["n"]
        finally:
            import_cache.stats.parse_match = real_parse

        assert first == 20, first
        assert second == 0, f"re-parsed {second} match(es) it already had"
        assert stats_module.parse_match is real_parse


class TestReparse:
    """`--reparse` exists to give old rows a newly added column.

    A mutant that made reparse skip already-stored matches survived every
    other test here, because the *counts* look identical either way: nothing
    is added in reparse mode by design. What differs is whether the stored
    rows change, so that's what has to be asserted.
    """

    def test_reparse_rewrites_rows_that_already_exist(self):
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        first = make_match(match_id="NA1_1", puuid="p1")
        import_cache.import_matches(s, _cache([first]))

        changed = make_match(match_id="NA1_1", puuid="p1")
        for participant in changed["info"]["participants"]:
            if participant["puuid"] == "p1":
                participant["championName"] = "Zed"
        import_cache.import_matches(s, _cache([changed]), reparse=True)
        assert s.load_matches("p1").iloc[0]["champion"] == "Zed"

    def test_without_reparse_the_stored_row_is_untouched(self):
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        import_cache.import_matches(s, _cache([make_match(match_id="NA1_1", puuid="p1")]))

        changed = make_match(match_id="NA1_1", puuid="p1")
        for participant in changed["info"]["participants"]:
            if participant["puuid"] == "p1":
                participant["championName"] = "Zed"
        import_cache.import_matches(s, _cache([changed]))
        assert s.load_matches("p1").iloc[0]["champion"] != "Zed"

    def test_reparse_does_not_change_how_many_rows_exist(self):
        """Run against the real database this must move 5,766 rows and add
        none of them."""
        s = _store()
        s.upsert_profile(_profile("p1", "Bendy"))
        matches = [make_match(match_id=f"NA1_{i}", puuid="p1") for i in range(12)]
        import_cache.import_matches(s, _cache(matches))
        import_cache.import_matches(s, _cache(matches), reparse=True)
        assert len(s.load_matches("p1")) == 12
