"""
Thin caching layer over RiotWatcher.

Handles:
- Resolving a Riot ID (name#tag) to a PUUID
- Pulling a list of recent match IDs
- Pulling full match detail JSON, with a local file cache so we don't
  re-hit the API (and its rate limit) for matches we've already fetched.
"""
import json
import os
import time
from pathlib import Path

from riotwatcher import LolWatcher, RiotWatcher

DATA_DIR = Path(__file__).parent / "data"
MATCH_CACHE_DIR = DATA_DIR / "matches"
MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
TIMELINE_CACHE_DIR = DATA_DIR / "timelines"
TIMELINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Riot's match-v5 matchlist endpoint caps `count` at 100 per call, so pulling
# more than that means paginating with `start`.
MAX_IDS_PER_REQUEST = 100
# Dev keys are rate-limited to 100 requests / 2 min. Pace uncached match-detail
# calls to stay under that; cached hits skip this delay entirely. A production
# key (no daily expiry, much higher limits) removes the need for this.
UNCACHED_REQUEST_DELAY_SEC = 1.3


class RiotClient:
    def __init__(self, api_key: str, continental_region: str, platform_region: str):
        self.riot_watcher = RiotWatcher(api_key)
        self.lol_watcher = LolWatcher(api_key)
        self.continental_region = continental_region
        self.platform_region = platform_region

    def get_puuid(self, game_name: str, tag_line: str) -> str:
        account = self.riot_watcher.account.by_riot_id(
            self.continental_region, game_name, tag_line
        )
        return account["puuid"]

    def get_match_ids(
        self, puuid: str, count: int = 20, start: int = 0, queue: int | None = None
    ) -> list[str]:
        return self.lol_watcher.match.matchlist_by_puuid(
            self.continental_region, puuid, start=start, count=count, queue=queue
        )

    def latest_match_ids(self, puuid: str, count: int = 20) -> list[str] | None:
        """The most recent match ids — **one** API call, no pagination.

        This is the whole point of the auto-refresh design. A full refresh
        re-paginates the entire match list: at a 1000-game target that's ten
        calls for the ids alone, plus four more for account/league/summoner/
        mastery, and it costs all fourteen even when nothing has changed.
        Running that on a timer would spend the rate-limit budget discovering
        that nothing happened.

        So the poll asks a cheaper question — "are the newest ids different
        from what I already have?" — and only escalates to a real refresh
        when the answer is yes.

        Returns None rather than raising if the call fails. A poll running on
        a timer must not be able to take down the page, and the most likely
        failure by far is an expired dev key: they last 24 hours, so anyone
        who leaves this on overnight will hit a 401 in the morning.
        """
        try:
            return self.get_match_ids(puuid, count=count)
        except Exception:
            return None

    def get_all_match_ids(
        self, puuid: str, target: int = 500, queue: int | None = None
    ) -> list[str]:
        """Paginate through match history in batches of up to 100 (Riot's
        per-call max) until we hit `target` or run out of history.

        **Only an empty page means the end.** A short page does not, and
        assuming it did cost real data: the first backfill stopped every one
        of eight players on a partial page — 618 games for an account with
        1,741 still sitting in the local cache, all of them fetchable and all
        of them parsing fine. The give-away was that *every* profile ended on
        a partial page, which is not what eight independent histories look
        like.

        Riot's matchlist is index-based and will return fewer entries than
        asked for in the middle of a history. Treating that as "no more
        games" silently truncates, and truncation is invisible — the site
        renders, the stats compute, they're just quietly built on less than
        half the record.

        The cost of being wrong the other way is one extra request per
        profile, which is nothing next to losing two thirds of someone's
        history.
        """
        all_ids: list[str] = []
        start = 0
        while len(all_ids) < target:
            batch_size = min(MAX_IDS_PER_REQUEST, target - len(all_ids))
            batch = self.get_match_ids(puuid, count=batch_size, start=start, queue=queue)
            if not batch:
                break  # an empty page is the only reliable end-of-history signal
            all_ids.extend(batch)
            start += len(batch)
        return all_ids

    def get_league_entries(self, puuid: str) -> list[dict]:
        """Current ranked standing (solo queue, flex, etc.) — league-v4.
        Returns [] if the account has no ranked history for this season."""
        return self.lol_watcher.league.by_puuid(self.platform_region, puuid)

    def get_summoner(self, puuid: str) -> dict:
        """Profile icon id, summoner level, etc. — summoner-v4."""
        return self.lol_watcher.summoner.by_puuid(self.platform_region, puuid)

    def get_active_game(self, puuid: str) -> dict | None:
        """The game you're in right now — spectator-v5 — or None.

        A 404 here is the normal case, not an error: it just means you're
        not in a game. Everything is swallowed for the same reason the
        mastery call is, since a live-game card is a nice-to-have and
        shouldn't be able to break a page load.

        Note the method name: riotwatcher calls it `by_summoner`, but the
        v5 endpoint takes a PUUID. Verified against the library source
        rather than its published docs, which still describe v4.
        """
        try:
            return self.lol_watcher.spectator.by_summoner(self.platform_region, puuid)
        except Exception:
            return None

    def get_champion_mastery(self, puuid: str) -> list[dict]:
        """Mastery points/level per champion — champion-mastery-v4.

        Deliberately defensive: older riotwatcher releases only exposed the
        now-deprecated summoner-id-based `by_summoner()`, and this project's
        requirements floor allows those. Rather than hard-crash the whole
        dashboard load on an AttributeError for a nice-to-have stat, a
        missing method (or any API error) degrades to an empty list and the
        Mastery card renders its own "unavailable" state.

        Note the champions here are keyed by numeric `championId`, not the
        string name match-v5 uses — see ddragon.get_champions() for the map.
        """
        try:
            return self.lol_watcher.champion_mastery.by_puuid(self.platform_region, puuid)
        except Exception:
            return []

    def get_match(self, match_id: str, use_cache: bool = True) -> dict:
        cache_path = MATCH_CACHE_DIR / f"{match_id}.json"
        if use_cache and cache_path.exists():
            with open(cache_path, "r") as f:
                return json.load(f)

        match = self.lol_watcher.match.by_id(self.continental_region, match_id)
        with open(cache_path, "w") as f:
            json.dump(match, f)
        return match

    def fetch_recent_matches(
        self, puuid: str, count: int = 500, queue: int | None = None, use_cache: bool = True
    ) -> list[dict]:
        """Fetch full match JSON for up to `count` recent matches (paginating
        past Riot's 100-per-call cap as needed), using the local cache where
        possible. Only paces/delays on actual API calls — cache hits are instant."""
        match_ids = self.get_all_match_ids(puuid, target=count, queue=queue)
        matches = []
        for match_id in match_ids:
            cache_path = MATCH_CACHE_DIR / f"{match_id}.json"
            was_cached = use_cache and cache_path.exists()
            matches.append(self.get_match(match_id, use_cache=use_cache))
            if not was_cached:
                time.sleep(UNCACHED_REQUEST_DELAY_SEC)
        return matches

    def get_timeline(self, match_id: str, use_cache: bool = True) -> dict | None:
        """Match timeline (per-event data incl. item purchases). Not all
        matches have timeline data, so this can legitimately return None."""
        cache_path = TIMELINE_CACHE_DIR / f"{match_id}.json"
        if use_cache and cache_path.exists():
            with open(cache_path, "r") as f:
                return json.load(f)

        try:
            timeline = self.lol_watcher.match.timeline_by_match(self.continental_region, match_id)
        except Exception:
            return None
        with open(cache_path, "w") as f:
            json.dump(timeline, f)
        return timeline

    def fetch_timelines(
        self, match_ids: list[str], use_cache: bool = True, on_progress=None
    ) -> dict[str, dict]:
        """Fetch timelines for a specific set of matches (e.g. all games on
        one champion), not the whole history — timelines double the API
        calls, so we only pull them on demand rather than for every match
        up front. Only paces on actual (uncached) API calls.

        `on_progress`, if given, is called as on_progress(done, total) after
        each match so the caller can drive a progress bar."""
        timelines = {}
        total = len(match_ids)
        for i, match_id in enumerate(match_ids):
            cache_path = TIMELINE_CACHE_DIR / f"{match_id}.json"
            was_cached = use_cache and cache_path.exists()
            timeline = self.get_timeline(match_id, use_cache=use_cache)
            if timeline is not None:
                timelines[match_id] = timeline
            if not was_cached:
                time.sleep(UNCACHED_REQUEST_DELAY_SEC)
            if on_progress:
                on_progress(i + 1, total)
        return timelines
