"""
The auto-refresh poll's API cost.

This is the one property the whole feature rests on. A full refresh
re-paginates the match list — at a 1000-game target that's ten calls for ids
plus four for account/league/summoner/mastery, and it costs all fourteen even
when nothing has changed. Running *that* every five minutes would spend the
rate-limit budget discovering nothing happened.

So `latest_match_ids` must be exactly one call. If it ever quietly starts
paginating, the feature still works, returns correct results, and silently
costs fourteen times more — a regression with no symptom. Hence a test that
counts requests rather than checking the answer.
"""
import sys
import types


class _CountingMatchApi:
    def __init__(self):
        self.calls = []

    def matchlist_by_puuid(self, region, puuid, start=0, count=20, queue=None):
        self.calls.append({"start": start, "count": count})
        # Riot returns at most `count`; pretend the account has plenty of
        # history so a paginating implementation would keep asking.
        return [f"NA1_{start + i}" for i in range(count)]


class _FakeWatcher:
    def __init__(self):
        self.match = _CountingMatchApi()


def _client():
    """A RiotClient with the network swapped out.

    Built with `object.__new__` rather than the constructor, because
    `__init__` instantiates real riotwatcher objects and the point here is to
    count calls, not to exercise the library.
    """
    if "riotwatcher" not in sys.modules:
        stub = types.ModuleType("riotwatcher")
        stub.LolWatcher = object
        stub.RiotWatcher = object
        sys.modules["riotwatcher"] = stub
    from riot_client import RiotClient

    client = object.__new__(RiotClient)
    client.lol_watcher = _FakeWatcher()
    client.riot_watcher = _FakeWatcher()
    client.continental_region = "americas"
    client.platform_region = "na1"
    return client


class TestPollCost:
    def test_latest_match_ids_is_exactly_one_call(self):
        """The economic claim, asserted. Fourteen calls versus one is the
        entire reason auto-refresh is affordable at five-minute intervals."""
        client = _client()
        client.latest_match_ids("puuid", count=20)
        assert len(client.lol_watcher.match.calls) == 1

    def test_it_asks_only_for_the_newest_page(self):
        client = _client()
        client.latest_match_ids("puuid", count=20)
        call = client.lol_watcher.match.calls[0]
        assert call["start"] == 0
        assert call["count"] == 20

    def test_it_never_paginates_regardless_of_count(self):
        """Locks the contract, not just the current numbers.

        At the count the app actually uses (20), a paginating implementation
        would *also* make one call — `get_all_match_ids` stops as soon as it
        has enough. So swapping this function for the paginating one is
        invisible at 20 and the obvious test passes against both. A larger
        count is what separates them.

        Riot caps a match-id request at 100, so count=250 isn't a real usage;
        it's a probe that distinguishes "asks once" from "asks until
        satisfied". The property worth holding is the former.
        """
        client = _client()
        client.latest_match_ids("puuid", count=250)
        assert len(client.lol_watcher.match.calls) == 1

    def test_full_pagination_costs_far_more(self):
        """The comparison, made concrete rather than asserted in a comment:
        the same 1000-game target the app is configured for."""
        client = _client()
        client.get_all_match_ids("puuid", target=1000)
        assert len(client.lol_watcher.match.calls) == 10

    def test_failure_is_swallowed_and_reported_as_none(self):
        """A poller on a timer must not be able to take down the page. The
        likeliest failure is an expired dev key — they last 24 hours, so
        anyone leaving auto-refresh on overnight will hit one."""
        client = _client()

        def boom(*args, **kwargs):
            raise RuntimeError("401 Unauthorized")

        client.lol_watcher.match.matchlist_by_puuid = boom
        assert client.latest_match_ids("puuid") is None


class _ShortPageMatchApi:
    """Riot, returning fewer ids than asked for *in the middle* of a history.

    The fake above always returns exactly `count`, which is why every test
    passed against a `get_all_match_ids` that stopped on the first short
    page. Real histories don't behave that way, and the first live backfill
    proved it: all eight profiles stopped on a partial page, one of them at
    618 games with 1,741 sitting in the local cache.
    """

    def __init__(self, total, short_at=None, short_size=18):
        self.total = total
        self.short_at = short_at
        self.short_size = short_size
        self.calls = []

    def matchlist_by_puuid(self, region, puuid, start=0, count=20, queue=None):
        self.calls.append({"start": start, "count": count})
        remaining = max(0, self.total - start)
        size = min(count, remaining)
        if self.short_at is not None and start == self.short_at and size > self.short_size:
            size = self.short_size
        return [f"NA1_{start + i}" for i in range(size)]


def _client_with(api):
    client = _client()
    client.lol_watcher.match = api
    return client


class TestPaginationDoesNotStopEarly:
    def test_a_short_page_mid_history_does_not_end_pagination(self):
        """The regression test for real lost data. 1,000 games available, one
        short page at index 600 — the exact shape that truncated a real
        account to 618."""
        api = _ShortPageMatchApi(total=1000, short_at=600, short_size=18)
        ids = _client_with(api).get_all_match_ids("puuid", target=1000)
        assert len(ids) == 1000, f"stopped at {len(ids)}"

    def test_an_empty_page_does_end_pagination(self):
        """The one signal that does mean the end. Without this the loop would
        spin against an exhausted history until it hit the target."""
        api = _ShortPageMatchApi(total=250)
        ids = _client_with(api).get_all_match_ids("puuid", target=1000)
        assert len(ids) == 250
        # 100, 100, 50, then an empty page to confirm the end.
        assert len(api.calls) == 4

    def test_it_still_stops_at_the_target(self):
        api = _ShortPageMatchApi(total=5000)
        ids = _client_with(api).get_all_match_ids("puuid", target=300)
        assert len(ids) == 300
        assert len(api.calls) == 3

    def test_pages_do_not_overlap_or_skip(self):
        """`start` advances by what was actually returned, not by what was
        asked for — otherwise a short page shifts every subsequent window and
        silently duplicates or drops games."""
        api = _ShortPageMatchApi(total=500, short_at=200, short_size=30)
        ids = _client_with(api).get_all_match_ids("puuid", target=500)
        assert len(ids) == len(set(ids)), "duplicate ids"
        assert len(ids) == 500

    def test_a_history_shorter_than_one_page(self):
        api = _ShortPageMatchApi(total=31)
        ids = _client_with(api).get_all_match_ids("puuid", target=1000)
        assert len(ids) == 31

    def test_an_account_with_no_games(self):
        api = _ShortPageMatchApi(total=0)
        ids = _client_with(api).get_all_match_ids("puuid", target=1000)
        assert ids == []
        assert len(api.calls) == 1
