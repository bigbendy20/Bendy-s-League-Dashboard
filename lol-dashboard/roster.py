"""
Parsing the list of people the site tracks.

Kept separate from the seeding script so the parsing rules are testable
without a network call, and separate from `profiles.py` because this is
about reading a hand-written file — a place where the input is messy by
definition.

**The roster file never enters the repository.** The repo is public (that's
what makes the 5-minute Actions cron free), and while any single Riot ID is
already lookupable on a dozen tracker sites, a published list of one
person's friends is a different artifact. The file is gitignored, resolved
to puuids once locally, and only the database holds the result.

Format, one player per line:

    Bendy#NA1
    Two Word Name#Scout = Scout        # optional display name after `=`
    # lines starting with a hash are comments

Regions default to NA and can be overridden per line:

    SomeFriend#EUW [euw1/europe]
"""

# Riot's routing values. Only the pairs that actually go together are listed,
# so a typo produces an error here rather than a 404 during the backfill that
# looks like a missing account.
REGIONS = {
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}

DEFAULT_PLATFORM = "na1"


class RosterError(ValueError):
    """A line that can't be understood. Raised rather than skipped: a silently
    dropped friend is a profile that never updates and nobody notices."""


def parse_line(line: str):
    """One roster line -> a dict, or None for blanks and comments."""
    text = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
    text = text.strip()
    if not text:
        return None

    platform = DEFAULT_PLATFORM
    if text.endswith("]") and "[" in text:
        text, _, region_part = text.rpartition("[")
        region = region_part.rstrip("]").strip().lower()
        platform = region.split("/")[0].strip()
        if platform not in REGIONS:
            raise RosterError(f"unknown region {platform!r}")
        text = text.strip()

    display_name = None
    if "=" in text:
        text, _, display_name = text.partition("=")
        text, display_name = text.strip(), display_name.strip() or None

    if "#" not in text:
        raise RosterError(f"missing #TAG in {line.strip()!r}")

    # Split on the *first* hash: Riot game names may contain spaces but never
    # a hash, so anything after the first one is the tag.
    game_name, _, tag_line = text.partition("#")
    # A trailing space before the hash is a common copy-paste artifact
    # ("Thang #0408"). Riot names can't end in a space, so stripping is safe.
    game_name, tag_line = game_name.strip(), tag_line.strip()
    if not game_name or not tag_line:
        raise RosterError(f"incomplete Riot ID in {line.strip()!r}")

    return {
        "game_name": game_name,
        "tag_line": tag_line,
        "display_name": display_name or game_name,
        "platform_region": platform,
        "continental_region": REGIONS[platform],
    }


def parse(text: str) -> list:
    """A whole roster file. Raises on the first unusable line, with its number."""
    entries = []
    seen = set()
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            entry = parse_line(line)
        except RosterError as exc:
            raise RosterError(f"line {number}: {exc}") from None
        if entry is None:
            continue
        key = (entry["game_name"].lower(), entry["tag_line"].lower())
        if key in seen:
            # A duplicate would be fetched twice and spend the budget twice
            # while producing exactly the same profile.
            raise RosterError(
                f"line {number}: {entry['game_name']}#{entry['tag_line']} listed twice"
            )
        seen.add(key)
        entries.append(entry)
    return entries
