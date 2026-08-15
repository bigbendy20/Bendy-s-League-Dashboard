"""
Who the site is showing, and how that's decided.

The local app had exactly one identity, read from `.env`. The hosted version
has several — a handful of friends, all able to view each other — so
"whose stats am I looking at?" becomes a real question with three answers
layered on top of each other:

  1. an explicit `?profile=<puuid>` in the URL, so a page can be linked;
  2. otherwise the signed-in user's own profile, matched by email;
  3. otherwise the first registered profile, so the site shows *something*
     rather than an empty state.

**Local mode still works, and that's deliberate.** With no store profiles
registered, `bootstrap_from_env()` turns the existing `.env` into a
single-profile list. The app then behaves exactly as it always has, which
keeps the offline path alive during the migration rather than requiring a
database to run anything at all.

Nothing here talks to Riot or to Streamlit — it's plain functions over
dictionaries, so the resolution rules are testable without a browser, a
network, or a database.
"""

# The fields a profile carries. `email` is how a signed-in Google account
# maps to a League account; it's optional because a profile should be
# registerable (and backfillable) before its owner has ever logged in.
PROFILE_FIELDS = (
    "puuid",
    "game_name",
    "tag_line",
    "platform_region",
    "continental_region",
    "display_name",
    "email",
)


def make_profile(puuid, game_name, tag_line, platform_region="na1",
                 continental_region="americas", display_name=None, email=None) -> dict:
    """One profile record, with the display name defaulted to the Riot name."""
    return {
        "puuid": puuid,
        "game_name": game_name,
        "tag_line": (tag_line or "").lstrip("#"),
        "platform_region": platform_region,
        "continental_region": continental_region,
        "display_name": display_name or game_name,
        "email": email,
    }


def riot_id(profile: dict) -> str:
    """`Name#TAG`, the form players actually recognise."""
    if not profile:
        return ""
    return f"{profile.get('game_name', '')}#{profile.get('tag_line', '')}"


def bootstrap_from_env(env: dict) -> list:
    """A single profile built from `.env`, or nothing if it isn't configured.

    The bridge between the two modes. It deliberately produces no puuid —
    that requires an API call, and profile *resolution* shouldn't depend on
    the network. The caller fills it in after the first fetch.
    """
    game_name = (env.get("RIOT_GAME_NAME") or "").strip()
    tag_line = (env.get("RIOT_TAG_LINE") or "").strip().lstrip("#")
    if not game_name or not tag_line:
        return []
    return [make_profile(
        puuid=None,
        game_name=game_name,
        tag_line=tag_line,
        platform_region=(env.get("PLATFORM_REGION") or "na1").strip(),
        continental_region=(env.get("CONTINENTAL_REGION") or "americas").strip(),
    )]


def find_by_email(profiles: list, email: str):
    """The signed-in user's own profile. Email match is case-insensitive,
    because identity providers are not consistent about casing."""
    if not email:
        return None
    wanted = email.strip().lower()
    for profile in profiles:
        if (profile.get("email") or "").strip().lower() == wanted:
            return profile
    return None


def find_by_puuid(profiles: list, puuid: str):
    if not puuid:
        return None
    return next((p for p in profiles if p.get("puuid") == puuid), None)


def resolve_active(profiles: list, requested_puuid=None, signed_in_email=None):
    """Which profile the page should show.

    Order matters and is the whole point:

      * an explicit request wins, so `?profile=…` links work and so a viewer
        can look at a friend's page without it being overridden back to
        their own on the next rerun;
      * then the signed-in user's own profile, so the default landing page
        is yours rather than whoever happens to sort first;
      * then the first profile, so a signed-out or unregistered visitor sees
        a populated site instead of an error.

    An unknown `requested_puuid` falls through rather than erroring — links
    outlive profiles, and a stale bookmark should degrade to a sensible page.
    """
    if not profiles:
        return None
    return (
        find_by_puuid(profiles, requested_puuid)
        or find_by_email(profiles, signed_in_email)
        or profiles[0]
    )


def is_own_profile(profile: dict, signed_in_email: str) -> bool:
    """Whether the viewer is looking at themselves.

    Drives the UI's tone — settings and goals belong on your own page, and a
    banner saying whose stats these are belongs on everyone else's.
    """
    if not profile or not signed_in_email:
        return False
    return (profile.get("email") or "").strip().lower() == signed_in_email.strip().lower()
