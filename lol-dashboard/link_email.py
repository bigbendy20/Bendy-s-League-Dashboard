"""
Attach a sign-in email to a League profile.

    python link_email.py                              # show who's linked
    python link_email.py "Name#TAG" them@example.com
    python link_email.py "Name#TAG" --clear

**Why this is needed.** Two separate identities have to be tied together: the
email someone signs in with, and the Riot account whose games they want to
see. `allowed_emails` in secrets decides who gets *in*; this decides whose
page they land on.

Without the link, a friend signs in successfully and then lands on whoever
sorts first alphabetically, and can't set their own climb goal — the
ownership check matches on the same field. They can still switch profiles by
hand, so it's a papercut rather than a wall, but it's the first thing each of
them will hit.

Deliberately a command rather than a button in the app. A "this profile is
me" control would be one click for the right person and one click for anyone
who mis-clicks, and undoing it means finding this file anyway. Eight people,
once each — a command is proportionate.

**Both stores are updated.** The local SQLite file is what the backfill and
reparse use; Postgres is what the deployed site reads. Writing one and not
the other is the failure this whole project keeps producing, so this does
both and says which it touched.
"""
import argparse
import os
import sys

import auth
import profiles as profiles_module


def link(data_store, riot_id: str, email: str | None):
    """Attach `email` to the profile with this Riot ID. Returns the profile.

    Raises `LookupError` if no profile matches, and `ValueError` if the email
    already belongs to someone else — that second case matters more than it
    looks. `resolve_active` picks the *first* profile whose email matches, so
    two profiles sharing an address doesn't error anywhere, it just quietly
    sends one person to the other's page forever.
    """
    wanted = riot_id.strip().lstrip("#").lower()
    everyone = data_store.list_profiles()

    match = None
    for profile in everyone:
        if profiles_module.riot_id(profile).lower() == wanted:
            match = profile
            break
        # Bare name, when it's unambiguous — nobody types the tag from memory.
        if (profile.get("game_name") or "").lower() == wanted:
            if match is not None:
                raise ValueError(
                    f"{riot_id!r} matches more than one profile — use the full "
                    f"Name#TAG form.")
            match = profile
    if match is None:
        known = ", ".join(sorted(profiles_module.riot_id(p) for p in everyone))
        raise LookupError(f"No profile for {riot_id!r}. Known: {known}")

    normalised = auth.normalise(email) if email else None
    if normalised:
        for profile in everyone:
            if (profile["puuid"] != match["puuid"]
                    and auth.normalise(profile.get("email")) == normalised):
                raise ValueError(
                    f"{normalised} is already linked to "
                    f"{profiles_module.riot_id(profile)}. One address, one "
                    f"profile — sharing it would send someone to the wrong page.")

    updated = dict(match)
    updated["email"] = normalised
    data_store.upsert_profile(updated)
    return updated


def report(data_store) -> str:
    """Who's linked and who isn't, longest column first."""
    rows = data_store.list_profiles()
    if not rows:
        return "No profiles registered yet — run seed_profiles.py first."
    width = max(len(profiles_module.riot_id(p)) for p in rows) + 2
    lines = []
    # Unlinked first: this list is a to-do list. `bool(email)` puts
    # False (unlinked) ahead of True, where `not email` did the reverse.
    for profile in sorted(rows, key=lambda p: (bool(p.get("email")),
                                              (p.get("game_name") or "").lower())):
        name = profiles_module.riot_id(profile)
        lines.append(f"  {name:<{width}}{profile.get('email') or '— not linked —'}")
    unlinked = sum(1 for p in rows if not p.get("email"))
    lines.append("")
    lines.append(f"{len(rows) - unlinked} of {len(rows)} linked."
                 + ("" if not unlinked else
                    "  Unlinked profiles land on whoever sorts first."))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Link a sign-in email to a League profile.")
    parser.add_argument("riot_id", nargs="?", help='e.g. "Name#TAG"')
    parser.add_argument("email", nargs="?", help="the address they sign in with")
    parser.add_argument("--clear", action="store_true",
                        help="Remove the link instead of setting one.")
    args = parser.parse_args(argv)

    import env_file
    import store

    env_file.load(env_file.path_for(__file__))
    targets = [("local", store.open_store(base_dir=os.path.dirname(
        os.path.abspath(__file__))))]
    hosted_url = os.getenv("POSTGRES_URL", "").strip()
    hosted_state = "absent"
    if hosted_url:
        hosted_state = "unreachable"
        try:
            targets.append(("hosted", store.open_store(hosted_url)))
            hosted_state = "ok"
        except Exception as exc:
            print(f"Could not reach the hosted database: {exc}", file=sys.stderr)
            print("Continuing with the local one only.\n", file=sys.stderr)

    if not args.riot_id:
        print(report(targets[0][1]))
        return 0
    if not args.email and not args.clear:
        parser.error("give an email address, or --clear to remove one")

    email = None if args.clear else args.email
    for label, data_store in targets:
        try:
            updated = link(data_store, args.riot_id, email)
        except (LookupError, ValueError) as exc:
            print(f"{label}: {exc}", file=sys.stderr)
            return 2
        verb = "unlinked" if args.clear else f"linked to {updated['email']}"
        print(f"{label}: {profiles_module.riot_id(updated)} {verb}")

    # Two very different situations, and telling them apart is the whole
    # value of the message: one needs a config line, the other needs the
    # command re-run once the database is reachable. Saying "POSTGRES_URL
    # isn't set" when it is and simply failed would send someone to edit a
    # file that's already correct.
    if hosted_state == "absent":
        print("\nLocal database only — POSTGRES_URL isn't in .env, so the "
              "deployed site is unchanged.")
    elif hosted_state == "unreachable":
        print("\nLocal database only — the hosted one couldn't be reached. "
              "Re-run this when it's back, or the site won't know about the "
              "change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
