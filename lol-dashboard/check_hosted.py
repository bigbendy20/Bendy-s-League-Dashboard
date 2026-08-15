"""
Report what's actually in the hosted database.

    python check_hosted.py

Reads `POSTGRES_URL` from `.env`, connects, and prints per-profile counts and
date ranges. Read-only — it opens the store and queries; nothing is written.

Worth having as its own command rather than a snippet in the deploy guide.
"Did the upload work?" is a question that recurs: after the first upload,
after the refresher's first run, and any time the site looks emptier than
expected. And the two failure modes it separates look identical from the
outside — an empty database and a database the app isn't reading are both
"no games on the page".
"""
import os
import sys

import env_file
import store


def summarise(data_store) -> list:
    """[(name, games, first, last)] for every profile, busiest first."""
    rows = []
    for profile in data_store.list_profiles():
        puuid = profile.get("puuid")
        if not puuid:
            continue
        frame = data_store.load_matches(puuid)
        name = profile.get("display_name") or profile.get("game_name") or puuid[:12]
        if frame.empty:
            rows.append((name, 0, None, None))
        else:
            rows.append((name, len(frame),
                         frame["game_creation"].min(), frame["game_creation"].max()))
    return sorted(rows, key=lambda r: -r[1])


def main(argv=None) -> int:
    env_file.load(env_file.path_for(__file__))
    url = os.getenv("POSTGRES_URL", "").strip()
    if not url:
        print("POSTGRES_URL is not set in .env.", file=sys.stderr)
        return 2

    print("Connecting to the hosted database...\n")
    try:
        data_store = store.open_store(url)
    except Exception as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 1

    rows = summarise(data_store)
    if not rows:
        print("No profiles found. The upload has not run, or ran against a "
              "different database.")
        return 1

    print(f"{'player':<16}{'games':>7}   history")
    total = 0
    for name, count, first, last in rows:
        total += count
        span = f"{str(first)[:10]} -> {str(last)[:10]}" if count else "(none)"
        print(f"{name:<16}{count:>7}   {span}")
    print(f"{'TOTAL':<16}{total:>7}")

    if total == 0:
        print("\nProfiles exist but no matches. Run: Upload to Postgres.bat")
        return 1
    print("\nThis is what the deployed site will show.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
