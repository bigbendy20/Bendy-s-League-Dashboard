"""
Keeps every profile's data current, outside the web app.

**Why this isn't a thread inside Streamlit.** Community Cloud puts apps to
sleep after 12 quiet hours, which would stop an in-app refresher overnight —
so stats would be stale every morning until someone happened to open the
site. And the render lock (see runtime.py) makes a long fetch on the request
path actively harmful: it would block every other viewer for the duration.
Fetching has to happen somewhere the web process isn't.

So this runs from a GitHub Actions cron. The web app then only ever *reads*
the store, which is the property that makes the lock cheap.

**The rate limit is the design constraint.** One personal key serves every
profile, at 20 requests/second and 100 per 2 minutes — and that budget is
shared, so a refresher that ignores it will 429 the site for everyone. Two
consequences shape the code:

  * profiles are polled with `latest_match_ids` first, one call each, and
    only escalate to a real fetch when something is new;
  * fetching is paced and bounded, so a friend with 300 unplayed games can't
    monopolise a run — the leftovers are picked up next cycle.

**Everything is incremental and resumable.** A run that dies halfway leaves
the store consistent, because matches are saved per profile as they're
fetched rather than in one final commit. That matters for the initial
backfill, which is hours long: it can be interrupted and restarted without
losing progress or re-fetching what it already has.
"""
import time

import stats

# Riot's personal-key ceiling is 100 requests per 2 minutes. Staying
# meaningfully under it leaves room for the site's own occasional calls (a
# live-game check) without either side having to coordinate.
REQUESTS_PER_CYCLE = 60

# Pacing between match fetches. `riot_client` sleeps on its own for uncached
# calls; this is the refresher's own budget check, which is about the *shared*
# limit rather than one client's politeness.
FETCH_DELAY_SEC = 1.3

# How many matches one profile may pull in a single run. Without a cap, a
# profile with a large backlog would consume the whole cycle and the others
# would go stale — which is the failure mode of a naive "loop over everyone".
MAX_MATCHES_PER_PROFILE_PER_RUN = 40

# Riot returns at most 100 match ids per call. Mirrored from `riot_client` so
# the budget arithmetic here doesn't silently drift from the pagination there.
MAX_IDS_PER_REQUEST = 100

# How much history the one-time backfill pulls per profile.
#
# High enough to mean "everything Riot will give us" in practice. match-v5
# serves roughly the last two years, and the most active account on this
# roster has about 1,700 games in that window — so this is a runaway guard,
# not a limit anyone is expected to reach. Storage isn't the constraint:
# measured at ~4.5 KB per stored game, so even 20,000 games across the roster
# is about 90 MB against a 500 MB free tier. Time is the constraint, and the
# run is resumable.
#
# It was 1,000, which would have quietly discarded the older 40% of the
# busiest account's record — the same truncation-that-looks-like-success this
# project has now hit three times.
BACKFILL_TARGET = 5000

# How often a long run says something. At ~1.3s per match a backfill of a
# thousand games is over twenty minutes for one profile; silence that long
# reads as a hang.
PROGRESS_EVERY = 25


class Budget:
    """A shared request allowance for one run.

    Explicit rather than implicit because the interesting bugs here are all
    about *aggregate* usage across profiles. A per-profile limit would still
    let seven profiles together blow the key's ceiling.
    """

    def __init__(self, requests: int = REQUESTS_PER_CYCLE):
        self.remaining = requests
        self.spent = 0

    def take(self, n: int = 1) -> bool:
        if self.remaining < n:
            return False
        self.remaining -= n
        self.spent += n
        return True

    def exhausted(self) -> bool:
        return self.remaining <= 0


def refresh_profile(client, data_store, profile: dict, budget: Budget,
                    sleep=time.sleep, max_matches=MAX_MATCHES_PER_PROFILE_PER_RUN,
                    history_target=None, on_progress=None) -> dict:
    """Bring one profile up to date. Returns a summary of what happened.

    Two quite different jobs behind one function, chosen by `history_target`:

    * **Unset (the scheduled cycle).** One call for the newest 20 ids, then
      fetch whatever is unseen. Cheap by design — see `latest_match_ids`.
    * **Set (the one-time backfill).** Paginate the *whole* history up to
      that many games. Expensive, run by hand, once.

    The distinction was missing and it mattered: `--backfill` used the poll
    path, so it asked for twenty ids per player and stopped. The paginating
    fetch existed and simply wasn't wired to anything. A "1000 games per
    friend" backfill would have quietly delivered twenty, and the resulting
    site would have looked like it worked.

    `sleep` is injectable so tests can run without waiting; everything else
    is real. `on_progress` takes a string — a multi-hour backfill that prints
    nothing is indistinguishable from one that has hung, which is exactly how
    it was first read.
    """
    puuid = profile.get("puuid")
    name = profile.get("display_name") or (puuid or "?")[:12]
    result = {"puuid": puuid, "display_name": profile.get("display_name"),
              "new_matches": 0, "requests": 0, "status": "ok"}
    if not puuid:
        # A profile bootstrapped from .env before its first fetch. Nothing to
        # refresh yet; the web app fills the puuid in on first load.
        result["status"] = "no puuid"
        return result

    if history_target:
        # One page must be affordable before starting; the rest are charged
        # from what actually came back. Reserving `history_target / 100` up
        # front would bill fifty pages to someone with six — harmless against
        # a generous allowance, but it makes the reported request count a
        # fiction, and that count is the only evidence of what a run cost.
        if not budget.take():
            result["status"] = "budget exhausted"
            return result
        try:
            latest = client.get_all_match_ids(puuid, target=history_target)
        except Exception:
            latest = None
        # Pages actually fetched: one per full page of ids, plus the empty
        # page that signalled the end.
        pages = 1 if not latest else -(-len(latest) // MAX_IDS_PER_REQUEST) + 1
        budget.take(pages - 1)
        result["requests"] += pages
    else:
        if not budget.take():
            result["status"] = "budget exhausted"
            return result
        result["requests"] += 1
        latest = client.latest_match_ids(puuid, count=20)

    if latest is None:
        # Almost certainly an expired or invalid key. Reported rather than
        # raised so one bad profile doesn't abort the whole run.
        result["status"] = "fetch failed"
        return result

    known = data_store.known_match_ids(puuid)
    new_ids = stats.unseen_match_ids(latest, known)
    if not new_ids:
        if on_progress:
            on_progress(f"  {name}: up to date ({len(known)} already stored)")
        return result

    wanted = new_ids if max_matches is None else new_ids[:max_matches]
    if on_progress:
        on_progress(f"  {name}: {len(wanted)} match(es) to fetch "
                    f"(~{len(wanted) * FETCH_DELAY_SEC / 60:.0f} min)")

    rows = []
    for index, match_id in enumerate(wanted, start=1):
        if not budget.take():
            result["status"] = "budget exhausted"
            break
        result["requests"] += 1
        try:
            match = client.get_match(match_id, use_cache=False)
        except Exception:
            # One unreadable match shouldn't cost the rest of the batch.
            continue
        row = stats.parse_match(match, puuid)
        if row:
            rows.append(row)
        if on_progress and index % PROGRESS_EVERY == 0:
            on_progress(f"    {name}: {index}/{len(wanted)}")
        sleep(FETCH_DELAY_SEC)

    if rows:
        # Saved as we go would be better still, but the store's dedupe makes
        # a re-run cheap: an interrupted backfill re-fetches at most this
        # profile's current batch.
        result["new_matches"] = data_store.save_matches(puuid, rows)
    if on_progress:
        on_progress(f"  {name}: {result['new_matches']} stored")
    return result


def refresh_rank(client, data_store, profile: dict, budget: Budget) -> int:
    """Snapshot a profile's ranked standing. Returns rows written (0 or more).

    Deduplicated in the store, so calling this every cycle is cheap in
    storage terms — and calling it *often* is what finally makes LP-per-game
    possible, since the local app could only sample when someone opened it.
    """
    puuid = profile.get("puuid")
    if not puuid or not budget.take():
        return 0
    try:
        entries = client.get_league_entries(puuid)
    except Exception:
        return 0
    return data_store.save_rank_snapshot(puuid, entries or [])


def refresh_all(client, data_store, profiles: list, budget=None, sleep=time.sleep,
                max_matches=MAX_MATCHES_PER_PROFILE_PER_RUN,
                history_target=None, on_progress=None) -> dict:
    """One refresh cycle across every profile.

    Profiles are processed in order and share a single budget, so an early
    profile with a backlog can starve a later one within a run. That's
    deliberate and bounded: `MAX_MATCHES_PER_PROFILE_PER_RUN` caps how much
    any one can take, and the next cycle picks up where this left off. The
    alternative — dividing the budget evenly up front — wastes most of it,
    because on a typical run nobody has new games at all.
    """
    budget = budget or Budget()
    report = {"profiles": [], "new_matches": 0, "requests": 0,
              "rank_snapshots": 0, "budget_exhausted": False}
    for profile in profiles:
        outcome = refresh_profile(client, data_store, profile, budget, sleep=sleep,
                                  max_matches=max_matches,
                                  history_target=history_target,
                                  on_progress=on_progress)
        outcome["rank_snapshots"] = refresh_rank(client, data_store, profile, budget)
        report["profiles"].append(outcome)
        report["new_matches"] += outcome["new_matches"]
        report["requests"] += outcome["requests"]
        report["rank_snapshots"] += outcome["rank_snapshots"]
        if budget.exhausted():
            report["budget_exhausted"] = True
            break
    return report


def format_report(report: dict) -> str:
    """A one-screen summary for the Actions log.

    Worth being readable: this is the only window into whether the refresher
    is working, and "silently did nothing" and "working correctly on a quiet
    day" produce very similar output otherwise.
    """
    lines = [
        f"{report['new_matches']} new match(es), "
        f"{report['rank_snapshots']} rank change(s), "
        f"{report['requests']} request(s) used"
    ]
    for outcome in report["profiles"]:
        name = outcome.get("display_name") or outcome.get("puuid") or "?"
        detail = f"  {name}: {outcome['new_matches']} new"
        if outcome["status"] != "ok":
            detail += f" [{outcome['status']}]"
        lines.append(detail)
    if report["budget_exhausted"]:
        lines.append("  budget exhausted — remaining profiles roll to the next run")
    return "\n".join(lines)
