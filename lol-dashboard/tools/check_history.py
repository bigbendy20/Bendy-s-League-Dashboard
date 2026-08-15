"""
Ask Riot, directly, how far back it will serve one account's match list.

    python tools/check_history.py
    python tools/check_history.py "YourName"

Read-only: it fetches match *ids* only, writes nothing, and costs about
twenty requests. Nothing here touches the database.

**Why this exists.** The backfill stopped at 618 games for an account with
1,741 matches sitting in the local cache. The first explanation — pagination
treating a short page as the end — was real and is fixed. It wasn't the whole
story: with it fixed, Riot still returns nothing past that point. So the
remaining question is a question about Riot's behaviour, and the honest way
to answer it is to ask Riot rather than to reason about it. I've already
guessed wrong about this endpoint once.

The three things worth separating, which this prints side by side:

1. **Where the id list actually ends.** Page by page, with dates, so "ran
   out" and "stopped early" are distinguishable.
2. **Whether an explicit `startTime` changes the answer.** match-v5 takes a
   time window; if the default view is limited but a windowed query reaches
   further, the fix is to page by time rather than by index.
3. **Whether the older matches are still individually fetchable.** An id
   missing from the list but still readable by id means the list is the
   limitation, not the data.
"""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import env_file  # noqa: E402
import refresh_job  # noqa: E402
from riot_client import RiotClient  # noqa: E402


PAGE = 100
MAX_PAGES = 25


def when(client, match_id):
    """Match id -> its date, from the local cache if possible."""
    try:
        match = client.get_match(match_id)
        stamp = match["info"]["gameCreation"] / 1000
        return datetime.datetime.fromtimestamp(stamp).date()
    except Exception:
        return None


def main() -> int:
    # Anchored to the project root, not to this file: `.env` lives one
    # level up. The per-caller anchoring rule is right, and this is the
    # case it has to be applied thoughtfully rather than copied.
    env_file.load(os.path.join(ROOT, ".env"))
    api_key = os.getenv("RIOT_API_KEY", "").strip()
    if not api_key:
        print("RIOT_API_KEY is not set.", file=sys.stderr)
        return 2

    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    data_store = refresh_job.open_store()
    profiles = data_store.list_profiles()
    if wanted:
        profiles = [p for p in profiles
                    if wanted.lower() in (p.get("game_name") or "").lower()]
    if not profiles:
        print("No matching profile. Run seed_profiles.py first.", file=sys.stderr)
        return 2

    profile = profiles[0]
    puuid = profile["puuid"]
    print(f"Account: {profile.get('game_name')}#{profile.get('tag_line')}")
    stored = len(data_store.known_match_ids(puuid))
    print(f"Currently stored: {stored} matches\n")

    client = RiotClient(api_key, profile.get("continental_region") or "americas",
                        profile.get("platform_region") or "na1")

    print("1. Paging the id list from the top")
    print("   start   returned   oldest in page")
    total = 0
    last_id = None
    for page in range(MAX_PAGES):
        start = page * PAGE
        try:
            batch = client.get_match_ids(puuid, count=PAGE, start=start)
        except Exception as exc:
            print(f"   {start:>5}   ERROR: {exc}")
            break
        total += len(batch)
        oldest = when(client, batch[-1]) if batch else None
        print(f"   {start:>5}   {len(batch):>8}   {oldest or '-'}")
        if batch:
            last_id = batch[-1]
        if not batch:
            print(f"\n   -> the list ends at {total} ids")
            break
    else:
        print(f"\n   -> still going at {total} ids (stopped probing)")

    print("\n2. Asking for an explicit older window")
    # A year-long window ending before the point the list ran out. If this
    # returns ids the index-paged list didn't, the endpoint is windowed and
    # the backfill should page by time.
    end = when(client, last_id) if last_id else None
    if end:
        end_dt = datetime.datetime.combine(end, datetime.time())
        start_dt = end_dt - datetime.timedelta(days=365)
        try:
            windowed = client.lol_watcher.match.matchlist_by_puuid(
                client.continental_region, puuid, count=PAGE,
                start_time=int(start_dt.timestamp()),
                end_time=int(end_dt.timestamp()),
            )
            print(f"   {start_dt.date()} .. {end_dt.date()}: {len(windowed)} id(s)")
            if windowed:
                print("   -> older matches ARE reachable by time window.")
                print("      The backfill should page by startTime, not by index.")
            else:
                print("   -> nothing there either; the history genuinely ends.")
        except Exception as exc:
            print(f"   ERROR: {exc}")

    print("\n3. Are the older cached matches still fetchable individually?")
    known = data_store.known_match_ids(puuid)
    import glob
    import json

    older = []
    for path in glob.glob(
            os.path.join(ROOT, "data", "matches", "*.json")):
        try:
            match = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if puuid not in match.get("metadata", {}).get("participants", []):
            continue
        match_id = match["metadata"]["matchId"]
        if match_id not in known:
            older.append((match["info"]["gameCreation"], match_id))
    older.sort(reverse=True)
    if not older:
        print("   none cached that aren't already stored.")
        return 0

    print(f"   {len(older)} cached match(es) are not in the database.")
    for stamp, match_id in older[:3]:
        date = datetime.datetime.fromtimestamp(stamp / 1000).date()
        try:
            live = client.lol_watcher.match.by_id(client.continental_region, match_id)
            ok = bool(live)
        except Exception as exc:
            ok = f"no ({exc})"
        print(f"   {date}  {match_id}  still served by Riot: {ok}")
    print("\n   If these are still served, the ids simply aren't being listed —")
    print("   which means the local cache is the only route to them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
