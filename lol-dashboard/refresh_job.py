"""
Entry point for the scheduled refresh. Run by GitHub Actions, and usable by
hand for the initial backfill.

    python refresh_job.py                # one normal cycle
    python refresh_job.py --backfill     # long run, for first population

Deliberately thin: everything interesting lives in `refresher.py`, which is
tested. This file only assembles the pieces from the environment, so the
part that can't be unit-tested is the part with no logic in it.
"""
import argparse
import os
import sys

import env_file
import profiles
import refresher
import store
from riot_client import RiotClient


# The store factory lives in `store.py` — see the note there. Re-exported
# under the old names because these are the project's vocabulary for "open the
# database", and both the tests and `upload_store`/`seed_profiles`/
# `import_cache` reach for them here.
DEFAULT_LOCAL_DB = store.DEFAULT_LOCAL_DB
sqlite_path = store.sqlite_path
open_store = store.open_store


def backfill_allowance(profile_count: int) -> int:
    """Requests a full backfill needs for this many profiles.

    Derived rather than a round number. The previous flat 5,000 was less than
    eight profiles x (their whole history + id pages), so the run would have
    stopped part-way and printed "budget exhausted" — which reads like a
    normal ending rather than a truncated one. It's resumable, so no data
    would have been lost; it would simply have looked finished when it wasn't.
    """
    per_profile = refresher.BACKFILL_TARGET + refresher.MAX_IDS_PER_REQUEST
    return max(1, profile_count) * per_profile + 100


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Refresh every profile.")
    parser.add_argument(
        "--backfill", action="store_true",
        help="Long run for initial population: a much larger request budget "
             "and no per-profile cap. Intended to be run by hand, not on a "
             "schedule — it can take hours against a personal key.",
    )
    parser.add_argument("--requests", type=int, default=None,
                        help="Override the request budget for this run.")
    args = parser.parse_args(argv)

    env_file.load(env_file.path_for(__file__))
    api_key = os.getenv("RIOT_API_KEY", "").strip()
    if not api_key:
        print("RIOT_API_KEY is not set.", file=sys.stderr)
        print(f"Expected it in {env_file.path_for(__file__)} as "
              "RIOT_API_KEY=RGAPI-…", file=sys.stderr)
        return 2

    data_store = open_store()
    registered = data_store.list_profiles()
    if not registered:
        registered = profiles.bootstrap_from_env(os.environ)
    if not registered:
        print("No profiles registered — nothing to refresh.")
        return 0

    # A backfill needs a budget big enough to matter but still paced; the
    # client sleeps between uncached calls, so this bounds a run's length
    # rather than its rate.
    #
    # Sized from the actual work rather than a round number. A flat 5,000 was
    # less than eight profiles × (1,000 matches + ten pages of ids) need, so
    # the run would have stopped part-way through and reported "budget
    # exhausted" as though that were a normal outcome. It is resumable, so
    # nothing would have been lost — but it would have looked finished.
    if args.requests is not None:
        allowance = args.requests
    elif args.backfill:
        allowance = backfill_allowance(len(registered))
    else:
        allowance = refresher.REQUESTS_PER_CYCLE
    budget = refresher.Budget(allowance)

    if args.backfill:
        print(f"Backfilling up to {refresher.BACKFILL_TARGET} games for "
              f"{len(registered)} profile(s). Resumable — stop and re-run any "
              f"time.\n")

    # Profiles can be on different platforms, so a client is built per
    # profile rather than once — the region is part of the client, not the
    # request. Cheap: it's just an object wrapping the key.
    report = {"profiles": [], "new_matches": 0, "requests": 0,
              "rank_snapshots": 0, "budget_exhausted": False}
    for profile in registered:
        client = RiotClient(
            api_key,
            profile.get("continental_region") or "americas",
            profile.get("platform_region") or "na1",
        )
        one = refresher.refresh_all(
            client, data_store, [profile], budget=budget,
            # The backfill wants everything; the scheduled cycle wants to be
            # cheap and fair. Same code, opposite priorities.
            history_target=refresher.BACKFILL_TARGET if args.backfill else None,
            max_matches=None if args.backfill else refresher.MAX_MATCHES_PER_PROFILE_PER_RUN,
            # Actions captures stdout, so progress is useful in both places —
            # but it's the local hours-long run that needs it.
            on_progress=print if args.backfill else None,
        )
        report["profiles"].extend(one["profiles"])
        for key in ("new_matches", "requests", "rank_snapshots"):
            report[key] += one[key]
        if one["budget_exhausted"]:
            report["budget_exhausted"] = True
            break

    print(refresher.format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
