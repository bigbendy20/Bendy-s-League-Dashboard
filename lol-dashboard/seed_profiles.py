"""
Resolve a roster of Riot IDs into stored profiles. Run once, locally.

    python seed_profiles.py                    # uses roster.txt
    python seed_profiles.py --roster mine.txt
    python seed_profiles.py --dry-run          # parse only, no API calls

Separate from `refresh_job.py` because it does something that job must never
do: turn a hand-written name into a puuid. That's an `account-v1` call per
person, and it only needs doing when someone joins.

The roster file is gitignored. The repository is public — that's what makes
the 5-minute Actions schedule free — and a published list of one person's
friends is a different thing from those accounts being individually
lookupable. Names go from a local file straight into the database.
"""
import argparse
import os
import sys

import env_file
import profiles
import roster


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed profiles from a roster file.")
    parser.add_argument("--roster", default="roster.txt")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report without calling Riot or writing.")
    args = parser.parse_args(argv)

    path = args.roster
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(__file__), path)
    if not os.path.exists(path):
        print(f"No roster at {path}.", file=sys.stderr)
        print("Create it with one Riot ID per line, e.g. `Bendy#NA1`.", file=sys.stderr)
        return 2

    with open(path, encoding="utf-8") as handle:
        try:
            entries = roster.parse(handle.read())
        except roster.RosterError as exc:
            print(f"Roster error: {exc}", file=sys.stderr)
            return 2

    if not entries:
        print("Roster is empty — nothing to seed.")
        return 0

    print(f"{len(entries)} player(s) in {os.path.basename(path)}:")
    for entry in entries:
        print(f"  {entry['game_name']}#{entry['tag_line']}  [{entry['platform_region']}]")
    if args.dry_run:
        print("\nDry run — no API calls made, nothing written.")
        return 0

    # Imported here, not at module scope. `--dry-run` promises to make no API
    # calls, and it should hold on a machine where the Riot library isn't even
    # installed — a top-level import made the dry run fail before it could
    # print anything, which is the opposite of what it's for.
    from refresh_job import open_store

    env_file.load(env_file.path_for(__file__))
    api_key = os.getenv("RIOT_API_KEY", "").strip()
    if not api_key:
        print("RIOT_API_KEY is not set.", file=sys.stderr)
        print(f"Expected it in {env_file.path_for(__file__)} as "
              "RIOT_API_KEY=RGAPI-…", file=sys.stderr)
        return 2

    from riot_client import RiotClient

    data_store = open_store()
    added = 0
    for entry in entries:
        try:
            # Constructing the client is inside the `try` as well as the
            # lookup: a rejected key raises here, and outside the guard that
            # surfaced as a raw traceback instead of the per-player message
            # the guard exists to print.
            client = RiotClient(api_key, entry["continental_region"],
                                entry["platform_region"])
            puuid = client.get_puuid(entry["game_name"], entry["tag_line"])
        except Exception as exc:
            # A typo'd name shouldn't stop the rest of the roster; report it
            # and carry on, since re-running is cheap and idempotent.
            print(f"  ! {entry['game_name']}#{entry['tag_line']}: {exc}", file=sys.stderr)
            continue
        existing = data_store.get_profile(puuid) or {}
        data_store.upsert_profile(profiles.make_profile(
            puuid=puuid,
            game_name=entry["game_name"],
            tag_line=entry["tag_line"],
            platform_region=entry["platform_region"],
            continental_region=entry["continental_region"],
            display_name=entry["display_name"],
            # Preserve an email already linked by sign-in; the roster doesn't
            # carry one and shouldn't clear it.
            email=existing.get("email"),
        ))
        added += 1
        print(f"  ✓ {entry['display_name']} -> {puuid[:12]}…")

    print(f"\n{added} of {len(entries)} profile(s) seeded.")
    print("Next: python refresh_job.py --backfill")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
