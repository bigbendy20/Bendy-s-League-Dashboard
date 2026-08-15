"""
Import matches from the local JSON cache into the database.

    python import_cache.py            # do it
    python import_cache.py --dry-run  # count only, write nothing

**Why this is needed, measured rather than assumed.** Riot's match *list*
endpoint only goes back so far. For this account it returns exactly 620 ids,
ending 2026-02-07 — six full pages, a page of 20, then an empty page. An
explicit `startTime` window for the year before that returns nothing either,
so it isn't an index-paging problem and it isn't something a different query
gets around.

But the older matches are still served individually: fetching
`NA1_5486617040` by id works fine today. They are unlisted, not gone.

And 1,123 of them are already on this machine, cached by the local app over
months of use — back to September 2024, nearly three times what Riot will
still enumerate. That cache is the only route to them, and once it's gone
those games are unrecoverable from any source.

So this reads the cache rather than the API. No requests, no rate limit, and
it can run any number of times: every write goes through `save_matches`,
which ignores ids the store already has.

The same data flows through `stats.parse_match` that the refresher uses — a
separate import path would be a second place for the schema to drift.
"""
import argparse
import glob
import json
import os
import sys

import stats

# `refresh_job` is imported inside `main`, not here. It pulls in the Riot
# client, which imports riotwatcher — and this script makes no API calls at
# all. Importing it at module scope would make a purely local operation fail
# on a machine without the network library installed.


CACHE_GLOB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "matches", "*.json")


def cached_matches(pattern=None):
    """Yield every readable match in the cache, once each.

    A generator because the cache is thousands of files and there's no reason
    to hold them all in memory; a corrupt or half-written file is skipped
    rather than aborting an import that would otherwise succeed.
    """
    for path in sorted(glob.glob(pattern or CACHE_GLOB)):
        try:
            with open(path, encoding="utf-8") as handle:
                yield json.load(handle)
        except Exception:
            continue


def import_matches(data_store, pattern=None, on_progress=None, dry_run=False,
                   reparse: bool = False) -> dict:
    """Store every cached match belonging to a tracked profile.

    One pass over the cache, checking each match against every profile, since
    a single game can involve several people on the roster — reading the
    files once per profile would be eight times the work for the same result.
    """
    profiles = data_store.list_profiles()
    tracked = {p["puuid"]: (p.get("display_name") or p.get("game_name") or p["puuid"][:12])
               for p in profiles if p.get("puuid")}
    if not tracked:
        return {"scanned": 0, "imported": 0, "per_profile": {}}

    # In reparse mode nothing counts as known: the point is to rewrite rows
    # that already exist, because `parse_match` gained a column and stored
    # rows only carry the fields that existed when they were written.
    known = {puuid: (set() if reparse else set(data_store.known_match_ids(puuid)))
             for puuid in tracked}
    pending = {puuid: [] for puuid in tracked}
    report = {"scanned": 0, "imported": 0, "per_profile": {}}

    for match in cached_matches(pattern):
        report["scanned"] += 1
        match_id = match.get("metadata", {}).get("matchId")
        if not match_id:
            continue
        # `metadata.participants` would be a cheaper filter, and using it was
        # a mistake: it made "did this player play in this game?" a second,
        # independent judgement that has to agree with `parse_match`. Letting
        # parse_match answer it — it returns None when the puuid isn't in the
        # match — means there's one rule, not two that can drift. The cost is
        # a parse per profile per file, which measures in seconds across the
        # whole cache.
        for puuid in tracked:
            if match_id in known[puuid]:
                continue
            row = stats.parse_match(match, puuid)
            if row:
                pending[puuid].append(row)
                known[puuid].add(match_id)
        if on_progress and report["scanned"] % 500 == 0:
            found = sum(len(rows) for rows in pending.values())
            on_progress(f"  scanned {report['scanned']} cached file(s), "
                        f"{found} to import")

    for puuid, rows in pending.items():
        if not rows:
            continue
        written = len(rows) if dry_run else data_store.save_matches(
            puuid, rows, overwrite=reparse)
        report["per_profile"][tracked[puuid]] = written
        report["imported"] += written
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Import cached matches that Riot no longer lists.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be imported, write nothing.")
    parser.add_argument("--reparse", action="store_true",
                        help="Rewrite matches already stored, so games from "
                             "before a new stat existed gain its columns.")
    args = parser.parse_args(argv)

    from refresh_job import open_store

    data_store = open_store()
    print("Reading the local cache. No API calls, nothing downloaded.\n")
    if args.reparse:
        print("Reparsing every cached match, including ones already stored.\n")
    report = import_matches(data_store, on_progress=print, dry_run=args.dry_run,
                            reparse=args.reparse)

    if not report["scanned"]:
        print("No cached matches found.", file=sys.stderr)
        return 1

    print(f"\nScanned {report['scanned']} cached match(es).")
    if not report["per_profile"]:
        print("Nothing to import — everything cached is already stored.")
        return 0

    for name, count in sorted(report["per_profile"].items(),
                              key=lambda item: -item[1]):
        print(f"  {name}: {count}")
    verb = "would be imported" if args.dry_run else "imported"
    print(f"\n{report['imported']} match(es) {verb}.")
    if args.dry_run:
        print("Dry run — nothing was written.")
    else:
        print("Safe to re-run; nothing is duplicated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
