"""
Copy a local store into the hosted one. Run once, after the backfill.

    python upload_store.py --to "postgresql://user:pass@host/db"
    python upload_store.py --to "..." --dry-run

This exists so the slow part and the paid-attention part can be separated.
The backfill takes hours against a rate-limited key and only needs your own
machine; the upload takes seconds and needs a database that doesn't have to
exist yet. Doing them together would mean signing up for Postgres before you
could start the fetch.

**Safe to re-run.** Every write goes through the same `save_matches` /
`upsert_profile` / `save_rank_snapshot` calls the app uses, all of which
ignore what's already there. So an upload that fails halfway can simply be
run again, and a second full upload changes nothing.

**Never deletes.** If the destination already has rows the source doesn't,
they're left alone. That's the right default for a one-way "push what I
collected" operation — the alternative silently discards games the live
refresher picked up while the backfill was running.
"""
import argparse
import os
import sys

import refresh_job


def copy_store(source, destination, on_progress=None, dry_run=False) -> dict:
    """Copy every profile, match and rank snapshot across.

    Returns counts. `on_progress` is called with a message per profile, so
    the caller decides how chatty to be — a copy of eight profiles and eight
    thousand matches should say something while it works.
    """
    report = {"profiles": 0, "matches": 0, "snapshots": 0, "skipped": 0}
    profiles = source.list_profiles()
    if not profiles:
        return report

    for profile in profiles:
        puuid = profile.get("puuid")
        if not puuid:
            # A bootstrap profile that never got a puuid. Nothing to key its
            # data by, so there is nothing to copy.
            report["skipped"] += 1
            continue

        frame = source.load_matches(puuid)
        rows = frame.to_dict("records") if not frame.empty else []
        snapshots = source.load_rank_snapshots(puuid)

        if not dry_run:
            destination.upsert_profile(profile)
            if rows:
                report["matches"] += destination.save_matches(puuid, rows)
            for snapshot in snapshots:
                # Replayed one at a time so the destination's own dedupe
                # decides what's worth keeping, exactly as it would live.
                report["snapshots"] += destination.save_rank_snapshot(
                    puuid,
                    [{
                        "queueType": snapshot.get("queue_type"),
                        "tier": snapshot.get("tier"),
                        "rank": snapshot.get("rank"),
                        "leaguePoints": snapshot.get("league_points"),
                        "wins": snapshot.get("wins"),
                        "losses": snapshot.get("losses"),
                    }],
                    timestamp=snapshot.get("timestamp"),
                )
        report["profiles"] += 1

        if on_progress:
            name = profile.get("display_name") or puuid[:12]
            on_progress(f"  {name}: {len(rows)} match(es), {len(snapshots)} snapshot(s)")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Upload the local store to Postgres.")
    parser.add_argument("--to", default=None,
                        help="Destination URL (postgresql://…). Defaults to "
                             "POSTGRES_URL from .env.")
    parser.add_argument("--from", dest="source", default=None,
                        help="Source store. Defaults to the local SQLite file.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be copied without writing.")
    args = parser.parse_args(argv)

    if not args.to:
        # Read from `.env` rather than taking it on the command line, and
        # deliberately under a *different* name than DATABASE_URL. Setting
        # DATABASE_URL locally would point the source store at Postgres too,
        # making this a copy from the destination to itself — a no-op that
        # reports success. Separate names make that impossible rather than
        # merely unlikely.
        import env_file

        env_file.load(env_file.path_for(__file__))
        args.to = os.getenv("POSTGRES_URL", "").strip()

    if not args.to:
        print("No destination. Either pass --to \"postgresql://…\" or add\n"
              "POSTGRES_URL=postgresql://… to .env (which is gitignored).",
              file=sys.stderr)
        return 2

    if args.to.startswith("sqlite") and args.source is None:
        # Copying the default local store onto itself would be a no-op that
        # looks like success — worth refusing rather than reporting zeroes.
        print("Destination is a local SQLite file and no --from was given.",
              file=sys.stderr)
        return 2

    source = refresh_job.open_store(args.source or "")
    destination = refresh_job.open_store(args.to)

    print(f"Copying from {args.source or 'local SQLite'} …")
    report = copy_store(source, destination, on_progress=print, dry_run=args.dry_run)

    if args.dry_run:
        print(f"\nDry run — {report['profiles']} profile(s) would be copied.")
        return 0
    print(f"\n{report['profiles']} profile(s), {report['matches']} new match(es), "
          f"{report['snapshots']} rank snapshot(s) written.")
    if report["skipped"]:
        print(f"{report['skipped']} profile(s) skipped — no puuid resolved yet.")
    print("\nSafe to re-run; nothing is duplicated and nothing is deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
