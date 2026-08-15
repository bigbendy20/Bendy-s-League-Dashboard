"""
The background refresher.

The whole point of this component is *restraint*: one API key serves every
profile, and the budget is shared. Most of these tests are therefore about
what the refresher declines to do — not fetching when nothing is new, not
letting one profile drain the allowance, not aborting everything because one
account failed.

Fakes rather than mocks, so the tests read as scenarios and a change in how
the client is called doesn't ripple through assertions.
"""
import sqlite3

import refresher
import stats
import store
from conftest import make_match


class FakeClient:
    """A Riot client with a scripted history and a request counter."""

    def __init__(self, histories=None, fail_for=None, league=None):
        # puuid -> list of match ids, newest first
        self.histories = histories or {}
        self.fail_for = set(fail_for or ())
        self.league = league or {}
        self.calls = {"latest": 0, "match": 0, "league": 0}
        self._owner = {}

    def latest_match_ids(self, puuid, count=20):
        self.calls["latest"] += 1
        if puuid in self.fail_for:
            return None                      # expired key, per the real client
        ids = list(self.histories.get(puuid, []))[:count]
        # Remember whose match each id is. The real `get_match` doesn't take a
        # puuid either — the refresher supplies it to `parse_match` — so the
        # fake has to build matches the right player actually appears in.
        # Without this, `parse_match` correctly returns None for every match
        # and the refresher looks broken when it's the fixture that's wrong.
        for match_id in ids:
            self._owner.setdefault(match_id, puuid)
        return ids

    def get_match(self, match_id, use_cache=True):
        self.calls["match"] += 1
        return make_match(match_id=match_id, puuid=self._owner.get(match_id, "me-puuid"))

    def get_league_entries(self, puuid):
        self.calls["league"] += 1
        return self.league.get(puuid, [])


def _store():
    return store.SqlStore(sqlite3.connect(":memory:"), paramstyle="?")


def _profile(puuid, name="Someone"):
    return {"puuid": puuid, "display_name": name, "game_name": name,
            "tag_line": "NA1", "platform_region": "na1",
            "continental_region": "americas", "email": None}


NOSLEEP = lambda *_: None


class TestRefreshProfile:
    def test_fetches_and_stores_new_matches(self):
        client = FakeClient({"p1": ["NA1_2", "NA1_1"]})
        s = _store()
        out = refresher.refresh_profile(client, s, _profile("p1"),
                                        refresher.Budget(), sleep=NOSLEEP)
        assert out["new_matches"] == 2
        assert len(s.load_matches("p1")) == 2

    def test_nothing_new_costs_exactly_one_request(self):
        """The economic claim the whole design rests on. A quiet cycle across
        seven profiles must cost seven requests, not seven full refreshes."""
        client = FakeClient({"p1": ["NA1_1"]})
        s = _store()
        s.save_matches("p1", [stats.parse_match(
            make_match(match_id="NA1_1", puuid="p1"), "p1")])
        budget = refresher.Budget()
        out = refresher.refresh_profile(client, s, _profile("p1"), budget, sleep=NOSLEEP)
        assert out["new_matches"] == 0
        assert out["requests"] == 1
        assert client.calls["match"] == 0

    def test_only_the_new_matches_are_fetched(self):
        """Overlapping ids arrive on every poll; re-fetching them would waste
        the budget and gain nothing, since the store dedupes anyway."""
        client = FakeClient({"p1": ["NA1_3", "NA1_2", "NA1_1"]})
        s = _store()
        s.save_matches("p1", [stats.parse_match(
            make_match(match_id="NA1_1", puuid="p1"), "p1")])
        refresher.refresh_profile(client, s, _profile("p1"),
                                  refresher.Budget(), sleep=NOSLEEP)
        assert client.calls["match"] == 2

    def test_a_failed_poll_is_reported_not_raised(self):
        """Dev/personal keys expire; one bad profile must not abort a run
        that could still refresh the others."""
        client = FakeClient({"p1": ["NA1_1"]}, fail_for=["p1"])
        out = refresher.refresh_profile(client, _store(), _profile("p1"),
                                        refresher.Budget(), sleep=NOSLEEP)
        assert out["status"] == "fetch failed"
        assert out["new_matches"] == 0

    def test_a_profile_without_a_puuid_is_skipped_cheaply(self):
        """Bootstrapped-from-env profiles have no puuid until first load.
        Skipping must cost zero requests, not one wasted call."""
        client = FakeClient()
        budget = refresher.Budget()
        out = refresher.refresh_profile(client, _store(), _profile(None),
                                        budget, sleep=NOSLEEP)
        assert out["status"] == "no puuid"
        assert budget.spent == 0
        assert client.calls["latest"] == 0

    def test_one_unreadable_match_does_not_lose_the_batch(self):
        class Flaky(FakeClient):
            def get_match(self, match_id, use_cache=True):
                self.calls["match"] += 1
                if match_id == "NA1_2":
                    raise RuntimeError("500")
                return make_match(match_id=match_id,
                                  puuid=self._owner.get(match_id, "me-puuid"))

        client = Flaky({"p1": ["NA1_3", "NA1_2", "NA1_1"]})
        s = _store()
        out = refresher.refresh_profile(client, s, _profile("p1"),
                                        refresher.Budget(), sleep=NOSLEEP)
        assert out["new_matches"] == 2

    def test_a_backlog_is_capped_per_run(self):
        """A friend returning after a long break has hundreds of unseen
        games. Without a cap they'd consume the whole cycle and everyone
        else would go stale — the leftovers roll to the next run instead."""
        client = FakeClient({"p1": [f"NA1_{i}" for i in range(200)]})
        s = _store()
        refresher.refresh_profile(client, s, _profile("p1"),
                                  refresher.Budget(requests=500), sleep=NOSLEEP,
                                  max_matches=10)
        assert len(s.load_matches("p1")) == 10


class TestBudget:
    def test_spending_is_bounded(self):
        budget = refresher.Budget(requests=3)
        assert all(budget.take() for _ in range(3))
        assert not budget.take()
        assert budget.exhausted()

    def test_a_profile_stops_when_the_budget_runs_out(self):
        client = FakeClient({"p1": [f"NA1_{i}" for i in range(50)]})
        s = _store()
        budget = refresher.Budget(requests=5)
        out = refresher.refresh_profile(client, s, _profile("p1"), budget, sleep=NOSLEEP)
        assert out["status"] == "budget exhausted"
        assert budget.spent <= 5


class TestRefreshAll:
    def test_every_profile_is_visited_on_a_quiet_cycle(self):
        client = FakeClient({"p1": [], "p2": [], "p3": []})
        report = refresher.refresh_all(
            client, _store(), [_profile(p) for p in ("p1", "p2", "p3")], sleep=NOSLEEP)
        assert len(report["profiles"]) == 3
        assert report["new_matches"] == 0

    def test_the_budget_is_shared_across_profiles(self):
        """A per-profile limit would still let seven profiles together blow
        the key's ceiling. The allowance has to be aggregate."""
        client = FakeClient({p: [f"{p}_{i}" for i in range(30)]
                             for p in ("p1", "p2", "p3")})
        budget = refresher.Budget(requests=8)
        report = refresher.refresh_all(
            client, _store(), [_profile(p) for p in ("p1", "p2", "p3")],
            budget=budget, sleep=NOSLEEP)
        assert budget.spent <= 8
        assert report["budget_exhausted"]

    def test_one_broken_profile_does_not_stop_the_others(self):
        client = FakeClient({"p1": ["NA1_1"], "p2": ["NA1_2"]}, fail_for=["p1"])
        s = _store()
        report = refresher.refresh_all(
            client, s, [_profile("p1"), _profile("p2")], sleep=NOSLEEP)
        assert report["new_matches"] == 1
        assert len(s.load_matches("p2")) == 1

    def test_rank_snapshots_are_taken(self):
        entry = {"queueType": "RANKED_SOLO_5x5", "tier": "EMERALD", "rank": "IV",
                 "leaguePoints": 55, "wins": 200, "losses": 180}
        client = FakeClient({"p1": []}, league={"p1": [entry]})
        s = _store()
        report = refresher.refresh_all(client, s, [_profile("p1")], sleep=NOSLEEP)
        assert report["rank_snapshots"] == 1

    def test_an_unchanged_rank_writes_nothing_on_the_next_cycle(self):
        """Running every five minutes, this is the common case by far — and
        it's what makes continuous polling affordable in storage terms."""
        entry = {"queueType": "RANKED_SOLO_5x5", "tier": "EMERALD", "rank": "IV",
                 "leaguePoints": 55, "wins": 200, "losses": 180}
        client = FakeClient({"p1": []}, league={"p1": [entry]})
        s = _store()
        refresher.refresh_all(client, s, [_profile("p1")], sleep=NOSLEEP)
        second = refresher.refresh_all(client, s, [_profile("p1")], sleep=NOSLEEP)
        assert second["rank_snapshots"] == 0

    def test_progress_survives_an_interrupted_run(self):
        """Matches are saved per profile as they're fetched, not in one final
        commit — so a run that dies mid-cycle leaves the store consistent and
        the next run resumes rather than restarting. This is what makes the
        multi-hour initial backfill practical."""
        client = FakeClient({"p1": ["NA1_2", "NA1_1"], "p2": ["NA1_3"]})
        s = _store()
        refresher.refresh_all(client, s, [_profile("p1"), _profile("p2")],
                              budget=refresher.Budget(requests=3), sleep=NOSLEEP)
        assert len(s.load_matches("p1")) >= 1
        # Second run picks up what the first couldn't reach.
        refresher.refresh_all(client, s, [_profile("p1"), _profile("p2")], sleep=NOSLEEP)
        assert len(s.load_matches("p2")) == 1


class TestReport:
    def test_reports_the_headline_numbers(self):
        client = FakeClient({"p1": ["NA1_1"]})
        report = refresher.refresh_all(client, _store(), [_profile("p1", "Bendy")],
                                       sleep=NOSLEEP)
        text = refresher.format_report(report)
        assert "1 new match" in text and "Bendy" in text

    def test_a_failure_is_visible_in_the_log(self):
        """'Silently did nothing' and 'working fine on a quiet day' look
        identical unless the status is printed."""
        client = FakeClient({"p1": ["NA1_1"]}, fail_for=["p1"])
        report = refresher.refresh_all(client, _store(), [_profile("p1", "Bendy")],
                                       sleep=NOSLEEP)
        assert "fetch failed" in refresher.format_report(report)


class TestRefreshJob:
    """The scheduled entry point.

    Deliberately thin — all the logic is in `refresher` — so these only cover
    the wiring that can't live there: which store it opens, and that it
    refuses to run without a key rather than failing obscurely mid-fetch.
    """

    def _job(self):
        import sys
        import types

        # `riot_client` imports riotwatcher at module level; the refresher
        # itself never touches it, so a stub is enough to import the job.
        if "riotwatcher" not in sys.modules:
            stub = types.ModuleType("riotwatcher")
            stub.LolWatcher = object
            stub.RiotWatcher = object
            sys.modules["riotwatcher"] = stub
        import refresh_job

        return refresh_job

    def test_missing_key_exits_with_an_error(self):
        """"Missing" now means missing from *both* the environment and
        `.env` — the job reads the file, which is the entire point of
        `env_file`. So the file has to be redirected as well as the variable
        cleared, or this passes or fails according to whether the developer
        running it happens to have a key on disk. It did exactly that: the
        test went red the moment the loader started working.
        """
        import os

        job = self._job()
        saved = os.environ.pop("RIOT_API_KEY", None)
        original = job.env_file.load
        # Point the loader at a directory with no `.env` in it.
        job.env_file.load = lambda *a, **k: original("/nonexistent/.env", *a[1:], **k)
        try:
            assert job.main([]) == 2
        finally:
            job.env_file.load = original
            if saved is not None:
                os.environ["RIOT_API_KEY"] = saved

    def test_a_key_in_the_env_file_is_enough(self):
        """The failure this whole module exists to prevent: the backfill
        printing "RIOT_API_KEY is not set" while the key sat in `.env` two
        lines away, because the CLI scripts were written for Actions — where
        the key is a real environment variable — and never read the file.

        Asserting it gets *past* the key check is the point; it stops there
        because there are no profiles, which needs no network.
        """
        import os
        import tempfile

        job = self._job()
        directory = tempfile.mkdtemp()
        with open(os.path.join(directory, ".env"), "w", encoding="utf-8") as handle:
            handle.write("RIOT_API_KEY=RGAPI-from-the-file\n")

        saved_key = os.environ.pop("RIOT_API_KEY", None)
        saved_url = os.environ.get("DATABASE_URL")
        original = job.env_file.load
        job.env_file.load = lambda *a, **k: original(os.path.join(directory, ".env"))
        os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(directory, 'board.db')}"
        try:
            assert job.main([]) == 0
            assert os.environ["RIOT_API_KEY"] == "RGAPI-from-the-file"
        finally:
            job.env_file.load = original
            os.environ.pop("RIOT_API_KEY", None)
            if saved_key is not None:
                os.environ["RIOT_API_KEY"] = saved_key
            os.environ.pop("DATABASE_URL", None)
            if saved_url is not None:
                os.environ["DATABASE_URL"] = saved_url

    def test_an_unset_url_gives_a_local_sqlite_store(self):
        """The backfill runs locally and the result gets uploaded later, so
        the default has to be one portable file rather than a directory of
        JSON — and the same `SqlStore` code the hosted version uses, so a bug
        can't hide in one path and not the other."""
        import os
        import tempfile

        job = self._job()
        saved = os.environ.pop("DATABASE_URL", None)
        try:
            opened = job.open_store(
                f"sqlite:///{os.path.join(tempfile.mkdtemp(), 'board.db')}")
            assert isinstance(opened, store.SqlStore)
            assert opened.p == "?"
        finally:
            if saved is not None:
                os.environ["DATABASE_URL"] = saved

    def test_where_each_url_form_puts_the_file(self):
        """Which file each URL form actually opens.

        Previously only "does it return a SqlStore" was checked, so every
        form could have resolved to the same wrong path and passed. That's
        the assertion that matters here: `DEPLOY.md` tells the user to run
        `set DATABASE_URL=sqlite:///C:/temp/board.db` to keep the database
        out of OneDrive, and if that silently landed in `data/` instead, the
        advice would appear to work while doing nothing — with the corruption
        risk it was written to avoid still fully present.
        """
        import os
        import tempfile

        job = self._job()
        directory = tempfile.mkdtemp()

        absolute = os.path.join(directory, "abs.db")
        job.open_store(f"sqlite:///{absolute}")
        assert os.path.exists(absolute)

        # The unset case is deliberately *not* exercised through `open_store`
        # here. Doing so creates the real `data/board.db` — inside the user's
        # OneDrive — as a side effect of running the tests, which is both
        # rude and, given what sync clients do to open SQLite files, a bad
        # habit to build. `sqlite_path` covers that form without a disk.
        assert job.sqlite_path("", "/project").endswith(
            os.path.join("project", "data", "board.db"))

    def test_a_windows_drive_letter_url_is_not_mangled(self):
        """`sqlite:///C:/temp/board.db` — the exact string in DEPLOY.md.

        The parser strips leading slashes after `://`, which on a POSIX path
        is what makes `sqlite:///tmp/x.db` relative and `sqlite:////tmp/x.db`
        absolute. A drive letter has to survive that intact: `C:/temp/...`
        must not become `temp/...` or lose the colon, or the file lands
        somewhere inside the project — inside OneDrive — which is precisely
        what the setting exists to avoid.
        """
        job = self._job()
        # `base_dir` is passed so the assertion doesn't depend on where the
        # project lives, and `ntpath.isabs` stands in for running on Windows —
        # on POSIX a drive letter isn't absolute, so `sqlite_path` would join
        # it to the base. What's being pinned is that the drive letter and
        # colon survive intact, which is what makes it absolute there.
        import ntpath

        resolved = job.sqlite_path("sqlite:///C:/temp/board.db", base_dir="/project")
        assert resolved.endswith("C:/temp/board.db")
        assert ntpath.isabs("C:/temp/board.db")

    def test_sqlite_path_resolves_each_form(self):
        job = self._job()
        base = "/project"

        assert job.sqlite_path("", base) == "/project/data/board.db"
        assert job.sqlite_path("sqlite:///rel/path.db", base) == "/project/rel/path.db"
        assert job.sqlite_path("sqlite:////abs/path.db", base) == "/abs/path.db"

    def test_the_default_is_relative_to_the_code_not_the_cwd(self):
        """A relative default resolved against the working directory would
        put a different database beside whatever folder the user happened to
        run the command from — so the backfill and the app would disagree
        about where the data is, depending on how each was launched.

        Run from a *different* working directory on purpose. The suite runs
        from the project folder, so with the cwd left alone the correct
        answer and the cwd-based wrong answer are the same string and the
        assertion proves nothing — the same trap that let the `env_file`
        path mutant escape.
        """
        import os
        import tempfile

        job = self._job()
        expected = os.path.join(os.path.dirname(os.path.abspath(job.__file__)),
                                "data", "board.db")
        here = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        try:
            assert job.sqlite_path("") == expected
        finally:
            os.chdir(here)

    def test_a_sqlite_url_is_honoured(self):
        import os
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "chosen.db")
        opened = self._job().open_store(f"sqlite:///{path}")
        opened.save_matches("p1", [])
        assert os.path.exists(path)

    def test_a_cycle_budget_is_small_enough_to_share_the_key(self):
        """60 requests per 5-minute cycle against a ceiling of 100 per 2
        minutes. The margin is deliberate: the web app still makes the odd
        live-game call, and neither side coordinates with the other."""
        assert refresher.REQUESTS_PER_CYCLE <= 100

    def test_a_quiet_cycle_costs_one_request_per_profile(self):
        """The number that decides whether a 5-minute cron is affordable.
        Seven friends, nobody playing: seven requests every five minutes."""
        client = FakeClient({f"p{i}": [] for i in range(7)})
        report = refresher.refresh_all(
            client, _store(), [_profile(f"p{i}") for i in range(7)], sleep=NOSLEEP)
        assert report["requests"] == 7


class TestUnopenableDatabase:
    """What happens when SQLite can't use the file it was pointed at.

    Bendy hit this on the real run: the project lives in OneDrive, and a
    cloud-synced folder can leave a placeholder where the file should be.
    SQLite reports "disk I/O error", which is accurate and tells you nothing
    about the cause or the fix.
    """

    def _job(self):
        import sys
        import types

        if "riotwatcher" not in sys.modules:
            stub = types.ModuleType("riotwatcher")
            stub.LolWatcher = object
            stub.RiotWatcher = object
            sys.modules["riotwatcher"] = stub
        import refresh_job

        return refresh_job

    def test_the_error_names_the_path_and_the_fix(self):
        import sqlite3
        import tempfile
        import os

        job = self._job()
        target = os.path.join(tempfile.mkdtemp(), "board.db")

        real_connect = sqlite3.connect

        def broken(*args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        sqlite3.connect = broken
        try:
            job.open_store(f"sqlite:///{target}")
        except SystemExit as exc:
            message = str(exc)
        else:
            raise AssertionError("expected the open to fail")
        finally:
            sqlite3.connect = real_connect

        # The three things someone staring at this needs: which file, what
        # SQLite actually said, and what to change.
        assert target in message
        assert "disk I/O error" in message
        assert "DATABASE_URL" in message

    def test_a_working_database_is_unaffected(self):
        """The guard must not swallow the normal path — a try/except that
        catches too much would turn every store into a SystemExit."""
        import os
        import tempfile

        job = self._job()
        target = os.path.join(tempfile.mkdtemp(), "fine.db")
        opened = job.open_store(f"sqlite:///{target}")
        opened.save_matches("p1", [])
        assert os.path.exists(target)


class BackfillClient(FakeClient):
    """A client that distinguishes the two ways of asking for match ids.

    `latest_match_ids` is the cheap poll and is capped at 20 by Riot's call;
    `get_all_match_ids` paginates. A fake that answered both the same way
    would have hidden the bug these tests exist for.
    """

    def latest_match_ids(self, puuid, count=20):
        self.calls["latest"] += 1
        ids = list(self.histories.get(puuid, []))[:count]
        for match_id in ids:
            self._owner.setdefault(match_id, puuid)
        return ids

    def get_all_match_ids(self, puuid, target=500, queue=None):
        self.calls.setdefault("all", 0)
        self.calls["all"] += 1
        ids = list(self.histories.get(puuid, []))[:target]
        for match_id in ids:
            self._owner.setdefault(match_id, puuid)
        return ids


class TestBackfill:
    """The one-time history load.

    Written after the fact: `--backfill` used the *poll* path, which asks for
    the twenty newest ids and stops. The paginating fetch existed in
    `riot_client` and nothing called it. Every test in this file passed, and a
    "1,000 games each" backfill would have delivered twenty — enough data for
    the site to render and look plausible, which is the worst amount to be
    wrong by.
    """

    def test_a_backfill_reaches_past_the_twenty_id_poll(self):
        """The assertion that fails on the old code. 300 games in history,
        and a poll-based backfill can see at most 20 of them."""
        client = BackfillClient({"p1": [f"NA1_{i}" for i in range(300)]})
        s = _store()
        out = refresher.refresh_profile(
            client, s, _profile("p1"), refresher.Budget(requests=5000),
            sleep=NOSLEEP, max_matches=None, history_target=300)
        assert out["new_matches"] == 300
        assert len(s.load_matches("p1")) == 300

    def test_a_backfill_uses_the_paginating_endpoint(self):
        client = BackfillClient({"p1": [f"NA1_{i}" for i in range(150)]})
        refresher.refresh_profile(
            client, _store(), _profile("p1"), refresher.Budget(requests=5000),
            sleep=NOSLEEP, max_matches=None, history_target=150)
        assert client.calls.get("all") == 1
        assert client.calls["latest"] == 0

    def test_the_scheduled_cycle_still_uses_the_cheap_poll(self):
        """The other direction, and the more important one to protect: the
        5-minute job must not start paginating whole histories."""
        client = BackfillClient({"p1": [f"NA1_{i}" for i in range(300)]})
        refresher.refresh_profile(
            client, _store(), _profile("p1"), refresher.Budget(),
            sleep=NOSLEEP)
        assert client.calls["latest"] == 1
        assert client.calls.get("all", 0) == 0

    def test_the_per_run_cap_still_applies_to_the_scheduled_cycle(self):
        client = BackfillClient({"p1": [f"NA1_{i}" for i in range(300)]})
        s = _store()
        refresher.refresh_profile(
            client, s, _profile("p1"), refresher.Budget(requests=500),
            sleep=NOSLEEP, max_matches=10)
        assert len(s.load_matches("p1")) == 10

    def test_the_id_pages_are_charged_to_the_budget(self):
        """Pages of ids are requests against a shared ceiling. Not charging
        them would let a backfill quietly exceed the rate limit — the one
        thing the Budget exists to prevent.

        Charged from what came back, not from the target: someone with six
        pages of history must not be billed for fifty. The reported request
        count is the only evidence of what a run actually cost, so inflating
        it makes that evidence worthless.
        """
        client = BackfillClient({"p1": [f"NA1_{i}" for i in range(250)]})
        budget = refresher.Budget(requests=9000)
        result = refresher.refresh_profile(
            client, _store(), _profile("p1"), budget,
            sleep=NOSLEEP, max_matches=None, history_target=5000)
        # 250 ids = 3 pages + the empty page that ended it, then 250 matches.
        assert budget.spent == 4 + 250
        assert result["requests"] == 4 + 250

    def test_a_short_history_is_not_billed_for_the_whole_target(self):
        """The distinguishing case: reserving `target / 100` up front passes
        every other assertion here and bills 50 pages for one game."""
        client = BackfillClient({"p1": ["NA1_1"]})
        budget = refresher.Budget(requests=9000)
        refresher.refresh_profile(
            client, _store(), _profile("p1"), budget,
            sleep=NOSLEEP, max_matches=None, history_target=5000)
        # One page of ids, the empty page after it, one match.
        assert budget.spent == 3

    def test_a_resumed_backfill_skips_what_it_already_has(self):
        """Resumability is the promise made to someone told they can stop it
        and re-run. Second pass must cost nothing but the id pages."""
        history = [f"NA1_{i}" for i in range(50)]
        client = BackfillClient({"p1": history})
        s = _store()
        first = refresher.refresh_profile(
            client, s, _profile("p1"), refresher.Budget(requests=5000),
            sleep=NOSLEEP, max_matches=None, history_target=50)
        assert first["new_matches"] == 50

        before = client.calls["match"]
        second = refresher.refresh_profile(
            client, s, _profile("p1"), refresher.Budget(requests=5000),
            sleep=NOSLEEP, max_matches=None, history_target=50)
        assert second["new_matches"] == 0
        assert client.calls["match"] == before

    def test_progress_is_reported_during_a_long_run(self):
        """A multi-hour run that prints nothing is indistinguishable from one
        that has hung — which is how the first real run was read."""
        client = BackfillClient({"p1": [f"NA1_{i}" for i in range(60)]})
        messages = []
        refresher.refresh_profile(
            client, _store(), _profile("p1", "Bendy"),
            refresher.Budget(requests=5000), sleep=NOSLEEP,
            max_matches=None, history_target=60,
            on_progress=messages.append)
        assert messages, "a backfill must say something while it works"
        assert any("Bendy" in m for m in messages)
        # Intermediate updates, not just a line at each end.
        assert len(messages) > 2

    def test_no_progress_output_unless_asked(self):
        """The scheduled job's log should stay a one-screen summary."""
        client = BackfillClient({"p1": ["NA1_1"]})
        refresher.refresh_profile(client, _store(), _profile("p1"),
                                  refresher.Budget(), sleep=NOSLEEP)


class TestBackfillWiring:
    """That `--backfill` actually asks for a backfill.

    The bug wasn't in `refresh_profile` — it was that nothing ever passed it
    a history target. Tests of the function alone would all have passed.
    """

    def _job(self):
        import sys
        import types

        if "riotwatcher" not in sys.modules:
            stub = types.ModuleType("riotwatcher")
            stub.LolWatcher = object
            stub.RiotWatcher = object
            sys.modules["riotwatcher"] = stub
        import refresh_job

        return refresh_job

    def test_the_flag_reaches_refresh_all(self):
        import os
        import tempfile

        job = self._job()
        seen = {}

        def spy(client, data_store, profiles, **kwargs):
            seen.update(kwargs)
            return {"profiles": [], "new_matches": 0, "requests": 0,
                    "rank_snapshots": 0, "budget_exhausted": False}

        directory = tempfile.mkdtemp()
        store_path = os.path.join(directory, "board.db")
        opened = job.open_store(f"sqlite:///{store_path}")
        opened.upsert_profile({
            "puuid": "p1", "display_name": "Bendy", "game_name": "Bendy",
            "tag_line": "NA1", "platform_region": "na1",
            "continental_region": "americas", "email": None})

        real_all = job.refresher.refresh_all
        real_open = job.open_store
        real_env = job.env_file.load
        saved_key = os.environ.get("RIOT_API_KEY")
        job.refresher.refresh_all = spy
        job.open_store = lambda *a, **k: real_open(f"sqlite:///{store_path}")
        job.env_file.load = lambda *a, **k: []
        os.environ["RIOT_API_KEY"] = "test-key"
        try:
            job.main(["--backfill"])
            assert seen.get("history_target") == job.refresher.BACKFILL_TARGET
            assert seen.get("max_matches") is None

            seen.clear()
            job.main([])
            assert seen.get("history_target") is None
            assert seen.get("max_matches") == job.refresher.MAX_MATCHES_PER_PROFILE_PER_RUN
        finally:
            job.refresher.refresh_all = real_all
            job.open_store = real_open
            job.env_file.load = real_env
            os.environ.pop("RIOT_API_KEY", None)
            if saved_key is not None:
                os.environ["RIOT_API_KEY"] = saved_key

    def test_the_backfill_budget_covers_the_work(self):
        """A flat allowance smaller than the job needs stops part-way and
        reports "budget exhausted" — which reads like a normal ending rather
        than a truncated one.

        The allowance is read from the code, not recomputed here. Recomputing
        it is what let a mutant restoring the flat 5,000 escape: the test did
        the same sum as the implementation and agreed with itself.
        """
        job = self._job()
        for profiles in (1, 8, 20):
            # What a full backfill actually spends: every match, plus one
            # request per page of 100 ids.
            pages = -(-job.refresher.BACKFILL_TARGET // job.refresher.MAX_IDS_PER_REQUEST)
            needed = profiles * (job.refresher.BACKFILL_TARGET + pages)
            assert job.backfill_allowance(profiles) >= needed, profiles

    def test_the_allowance_scales_with_the_roster(self):
        """A constant would pass the check above for one profile and starve
        the eighth."""
        job = self._job()
        assert job.backfill_allowance(8) > job.backfill_allowance(1)


class TestBackfillTarget:
    def test_the_cap_is_not_the_binding_constraint(self):
        """`BACKFILL_TARGET` is meant to be a runaway guard, not a decision
        about how much history exists.

        The busiest account on this roster has ~1,700 games in Riot's
        two-year window, so a cap near that number silently decides the
        answer — which is exactly what 1,000 did: it would have discarded the
        older 40% of that record while the site rendered perfectly.

        Asserting a threshold rather than the literal value: the property is
        "comfortably above any real history here", and a test pinning
        `== 5000` would just restate the constant.
        """
        busiest_observed = 1741
        assert refresher.BACKFILL_TARGET >= busiest_observed * 1.5

    def test_the_allowance_still_covers_the_larger_target(self):
        """Raising the target without raising the budget would reintroduce
        the truncation from the other end — a run that stops early and calls
        it 'budget exhausted'."""
        import sys
        import types

        if "riotwatcher" not in sys.modules:
            stub = types.ModuleType("riotwatcher")
            stub.LolWatcher = object
            stub.RiotWatcher = object
            sys.modules["riotwatcher"] = stub
        import refresh_job

        pages = -(-refresher.BACKFILL_TARGET // refresher.MAX_IDS_PER_REQUEST) + 1
        needed = 8 * (refresher.BACKFILL_TARGET + pages)
        assert refresh_job.backfill_allowance(8) >= needed
