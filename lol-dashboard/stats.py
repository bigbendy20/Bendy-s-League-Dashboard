"""
Turn raw match-v5 JSON into a flat pandas DataFrame, one row per game
(from the tracked player's perspective), and compute win-rate / performance
breakdowns.
"""
import datetime

import pandas as pd


def to_local_time(epoch_ms, tz=None) -> pd.Timestamp:
    """Riot's epoch-millisecond timestamp as *local wall-clock* time.

    Riot returns UTC. Parsing it with `pd.to_datetime(..., unit="ms")` gives a
    naive timestamp that is silently still UTC, which quietly corrupted every
    time-based stat: a 9pm Thursday game in New York was filed as Friday, so
    "Fridays are your best day" was partly Thursday evenings, and "you win most
    around 19:00" meant 19:00 UTC — 2pm Eastern.

    `datetime.fromtimestamp()` is doing the real work here. It converts through
    the OS timezone database using the offset in force *at that instant*, so
    games from January get EST and games from July get EDT. A fixed offset
    (`now().astimezone().utcoffset()`) would be an hour wrong for whichever
    half of the year you aren't currently in.

    The result is deliberately naive again — local wall-clock, no tzinfo — so
    every existing comparison, sort and `.strftime` keeps working unchanged.
    `tz` is for tests; leave it None to use the machine's own timezone.
    """
    seconds = epoch_ms / 1000
    if tz is None:
        return pd.Timestamp(datetime.datetime.fromtimestamp(seconds))
    return pd.Timestamp(datetime.datetime.fromtimestamp(seconds, tz).replace(tzinfo=None))

def unseen_match_ids(latest: list | None, known) -> list:
    """Ids in `latest` that aren't already loaded, newest first.

    The poll's decision function, kept here so it's testable without a Riot
    client. `None` means the call failed — treated as "nothing new" rather
    than as an error, because a failed poll should be quiet, not disruptive.

    Order is preserved from Riot's response (newest first) so the caller can
    report "2 new games" meaningfully.
    """
    if not latest:
        return []
    known = set(known or ())
    return [mid for mid in latest if mid not in known]


def split_record(df: pd.DataFrame, mask) -> tuple:
    """((games, wins) inside the mask, (games, wins) outside it).

    The comparison a "is this real?" question needs is *these games versus
    the others*, not these games versus everything. A subset compared against
    a superset containing it is biased toward finding no difference, because
    the subset drags the baseline toward itself — 188 Shaco games out of 599
    move the "overall" number by a third of the gap you're trying to detect.

    Lives in stats because both tip engines need it. It was written twice
    otherwise, and the copy in insights.py was the one that stayed wrong.
    """
    if df.empty:
        return (0, 0), (0, 0)
    inside, outside = df[mask], df[~mask]
    return ((len(inside), int(inside["win"].sum())),
            (len(outside), int(outside["win"].sum())))


def role_scope(df: pd.DataFrame, role: str | None) -> pd.DataFrame:
    """Restrict a frame to one role. `None` means every role.

    Runes, summoner spells, item builds and lane matchups are all
    role-dependent in the same way, so the Deep-Dive scopes the frame once
    and every card inherits it rather than each taking its own `role=`
    argument. This helper exists so that decision is testable: the scoping
    itself is one line of pandas, but "does the page actually apply it" is
    the part that has regressed before.
    """
    if df.empty or role is None or "role_label" not in df.columns:
        return df
    return df[df["role_label"] == role]


def most_played_champion(df: pd.DataFrame) -> str | None:
    """Your most-played champion in `df`, or None if there's nothing to pick.

    Drives the site background. Ties break alphabetically rather than by
    whatever order `value_counts` happens to produce — an unstable background
    that flips between two equally-played champions on every refresh would
    look like a bug.
    """
    if df.empty or "champion" not in df.columns:
        return None
    counts = df["champion"].dropna().value_counts()
    if counts.empty:
        return None
    top = counts.max()
    return sorted(counts[counts == top].index)[0]


def hour_label(hour: int) -> str:
    """A 0-23 hour as "8 PM". Midnight is 12 AM, noon is 12 PM.

    One function rather than a format string at each call site, because the
    hour appears in both the tip text and the chart axis and they'd otherwise
    drift. The awkward cases are the ones people get wrong by hand: hour 0
    is 12 AM (not 0 AM), and hour 12 is 12 PM (not 12 AM).
    """
    hour = int(hour) % 24
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display} {suffix}"


def recent_window(df: pd.DataFrame, games: int | None) -> pd.DataFrame:
    """The most recent `games` rows, chronologically. None means everything.

    Lives here rather than inline in the renderer so it can be tested without
    Streamlit — a mutation that deleted the slice entirely was invisible while
    this logic sat inside a UI function.
    """
    if not games or "game_creation" not in df.columns or len(df) <= games:
        return df
    return df.sort_values("game_creation").tail(games)


def wilson_margin(wins: int, games: int, z: float = 1.96) -> float:
    """Half-width of the 95% Wilson score interval, in percentage points.

    Answers "how much of this number is real?". A 63% win rate over 38 games
    carries a margin of roughly +/-15 points, which is the difference between
    "your best day" and "noise" — and every day-of-week or hour-of-day tip in
    this app is computed over samples that small.

    Wilson rather than the textbook normal approximation because the normal
    one misbehaves badly at small n and near 0% or 100%, which is exactly
    where these buckets live. Returns 100.0 for zero games: no data means no
    constraint, not a narrow interval.
    """
    if games <= 0:
        return 100.0
    p_hat = wins / games
    denom = 1 + z**2 / games
    half = z * ((p_hat * (1 - p_hat) / games + z**2 / (4 * games**2)) ** 0.5) / denom
    return half * 100


def z_for(comparisons: int = 1, alpha: float = 0.05) -> float:
    """Critical z, widened for how many buckets were scanned.

    The day-of-week tip picks the best of 7 days; the hour tip picks the best
    of up to 24 hours. Reporting the maximum of many noisy buckets and then
    testing it at the usual 95% is circular — with 24 buckets you'd expect
    roughly one to clear that bar by luck alone. A Bonferroni split of alpha
    across the buckets is the crude but honest correction: z goes 1.96 -> 2.69
    for 7 days, -> 3.08 for 24 hours.

    This is why an 83%-vs-20% "best hour" built on six games no longer gets
    stated as fact.
    """
    import statistics

    comparisons = max(1, int(comparisons))
    return statistics.NormalDist().inv_cdf(1 - alpha / comparisons / 2)


def separated(wins_a, games_a, wins_b, games_b, comparisons: int = 1) -> bool:
    """True when two win rates differ by more than chance explains.

    A two-proportion z-test on the *difference*, not a check for overlapping
    confidence intervals. That distinction turned out to matter: the earlier
    version asked whether each rate's own interval cleared the other's, which
    is a far stricter bar than it looks — non-overlap corresponds to roughly
    alpha = 0.005, not the 0.05 the intervals are drawn at. It was quietly
    discarding real effects while claiming to test at 95%.

    Concretely, on the history this was built against: 57.4% over 188 Shaco
    jungle games versus 46.2% over the other 411 is p = 0.011, and the old
    rule called it noise. The weekday example it was meant to reject — 63%
    over 38 versus 44% over 32, p = 0.105 — is still rejected. The fix makes
    the test more sensitive without making it credulous.

    `wilson_margin` is still what the UI *displays*, since a per-rate
    interval is the useful thing to show a reader; it's only the
    accept/reject decision that changed.

    `comparisons` widens the critical value when these two buckets were
    picked as the extremes of a larger scan — see `z_for`.
    """
    if games_a <= 0 or games_b <= 0:
        return False
    p_pooled = (wins_a + wins_b) / (games_a + games_b)
    variance = p_pooled * (1 - p_pooled) * (1 / games_a + 1 / games_b)
    if variance <= 0:
        # Both samples unanimous and identical — no difference to detect.
        return False
    a, b = wins_a / games_a, wins_b / games_b
    return abs(a - b) / (variance ** 0.5) > z_for(comparisons)


# Common queueIds -> human labels. Not exhaustive — anything unmapped falls
# back to "Other (<id>)" rather than breaking.
QUEUE_LABELS = {
    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
    480: "Swiftplay",
    490: "Quickplay",
    700: "Clash",
    720: "ARAM Clash",
    900: "ARURF",
    1700: "Arena",
    1710: "Arena",
    1750: "Arena",
    1900: "URF",
}

# The queues whose stats describe "playing League the normal way": 5v5
# Summoner's Rift, drafted or ranked. Everything else gets its own tab.
#
# Deliberately an allow-list, not a block-list. Riot adds rotating modes
# constantly (Nexus Blitz, One for All, Ultimate Spellbook, whatever is next),
# and an unrecognised queue id defaulting to *excluded* is the safe direction
# — a new mode quietly polluting your ranked CS/min is much worse than it
# sitting in the Other Modes tab until someone maps it.
#
# Swiftplay (480) is on Summoner's Rift and has lanes, but runs on accelerated
# XP and gold with shorter games. Pooling it with standard SR would bias every
# per-minute and game-length stat, so it lives with the other modes.
CORE_QUEUES = frozenset({400, 420, 430, 440, 490})


def queue_label(queue_id: int | None) -> str:
    if queue_id is None:
        return "Unknown"
    return QUEUE_LABELS.get(queue_id, f"Other ({queue_id})")


def mode_label(queue_id: int | None, game_mode: str | None = None) -> str:
    """Display name for a queue, falling back to Riot's gameMode string.

    An unmapped id would otherwise read "Other (880)", which tells you
    nothing. Riot's own `gameMode` is right there on every match and at least
    says SWIFTPLAY or CHERRY. Keeping the id in the label is deliberate: it's
    what you'd need to look the queue up, and it makes clear the name is
    inferred rather than curated.
    """
    if queue_id in QUEUE_LABELS:
        return QUEUE_LABELS[queue_id]
    if game_mode:
        pretty = str(game_mode).replace("_", " ").title()
        return f"{pretty} (queue {queue_id})"
    return queue_label(queue_id)


def is_core_queue(queue_id: int | None) -> bool:
    """Does this queue count toward the site's headline stats?"""
    return queue_id in CORE_QUEUES


def core_only(df: pd.DataFrame) -> pd.DataFrame:
    """Standard Summoner's Rift games — normal draft/blind/quickplay and ranked.

    Applied once at the data boundary rather than sprinkled through the
    pages, so there's exactly one place that decides what "your stats" means.
    ARAM has no lane opponent and different gold curves; Arena isn't even 5v5;
    Swiftplay accelerates XP. Averaging any of them into a CS/min or a lane
    differential produces a number that describes nothing.
    """
    if df.empty or "queue_id" not in df.columns:
        return df
    return df[df["queue_id"].isin(CORE_QUEUES)]


def non_core_only(df: pd.DataFrame) -> pd.DataFrame:
    """The complement of `core_only` — everything the Other Modes tab shows.

    Defined as the literal complement rather than its own id list, so a queue
    can never fall through both filters and vanish from the app entirely.
    """
    if df.empty or "queue_id" not in df.columns:
        return df.iloc[0:0]
    return df[~df["queue_id"].isin(CORE_QUEUES)]


def mode_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Games, wins and win rate per mode, for the Other Modes tab."""
    if df.empty or "queue_id" not in df.columns:
        return pd.DataFrame(columns=["mode", "games", "wins", "win_rate"])
    d = df.copy()
    modes = d.get("game_mode", pd.Series([None] * len(d), index=d.index))
    d["mode"] = [mode_label(q, m) for q, m in zip(d["queue_id"], modes)]
    return win_rate_by(d, "mode").sort_values("games", ascending=False).reset_index(drop=True)


# Coarser grouping for the main filter — "Ranked" covers both Solo/Duo and
# Flex, "Normal" covers both Draft and Blind, so the filter reads Normal /
# ARAM / Ranked / etc. rather than every individual queueId.
QUEUE_CATEGORIES = {
    400: "Normal",
    430: "Normal",
    490: "Normal",
    420: "Ranked",
    440: "Ranked",
    450: "ARAM",
    480: "Swiftplay",
    700: "Clash",
    720: "ARAM",
    900: "ARURF",
    1700: "Arena",
    1710: "Arena",
    1750: "Arena",
    1900: "URF",
}


def queue_category(queue_id: int | None) -> str:
    if queue_id is None:
        return "Other"
    return QUEUE_CATEGORIES.get(queue_id, "Other")


GAME_LENGTH_ORDER = ["<20 min", "20-30 min", "30-40 min", "40+ min"]

# ==================== Roles ====================
# match-v5 reports positions as TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY.
# Those are fine as grouping keys but read badly in a UI, so display uses
# the names players actually say. The raw value is kept on every row as
# `role` so grouping stays stable if Riot ever changes display conventions.
ROLE_LABELS = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "Bot",
    "UTILITY": "Support",
}
# Lane order, so charts and tables read Top → Support rather than being
# sorted by how often you happen to play each one.
ROLE_ORDER = ["Top", "Jungle", "Mid", "Bot", "Support", "ARAM", "Arena", "Unknown"]

# Modes with no lanes at all. Riot reports a blank position for these, which
# would otherwise show as "Unknown" — but "ARAM" is the true answer, not a
# gap in the data. Kept separate from LANE_ROLES below because they're also
# the modes where lane/objective stats don't apply.
LANELESS_QUEUES = {"ARAM": "ARAM", "Arena": "Arena"}
# The five actual Summoner's Rift positions, for anything counting "how many
# roles do you play" — ARAM isn't a sixth role.
LANE_ROLES = set(ROLE_LABELS.values())


def role_label(raw_role: str | None, queue_cat: str | None = None) -> str:
    """Friendly role name.

    Laneless modes report their mode name instead of a position, since
    that's the honest answer — an ARAM game doesn't have a missing role, it
    has no roles. Anything else unrecognised falls back to "Unknown"."""
    if queue_cat in LANELESS_QUEUES:
        return LANELESS_QUEUES[queue_cat]
    if not raw_role:
        return "Unknown"
    return ROLE_LABELS.get(str(raw_role).upper(), "Unknown")


def win_rate_by_role(df: pd.DataFrame, min_games: int = 1) -> pd.DataFrame:
    """Win rate per role, in lane order rather than by frequency."""
    result = win_rate_by(df, "role_label", min_games=min_games)
    if result.empty:
        return result
    order = {r: i for i, r in enumerate(ROLE_ORDER)}
    result["_sort"] = result["role_label"].map(order).fillna(99)
    return result.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)


def primary_roles(df: pd.DataFrame) -> dict:
    """champion -> the role you play them in most often. Champions get
    played in several positions (Shaco jungle vs. support, Pantheon top vs.
    support), so this is the modal role, not the only one."""
    if df.empty or "role_label" not in df.columns:
        return {}
    counts = df.groupby(["champion", "role_label"]).size().reset_index(name="games")
    counts = counts.sort_values("games", ascending=False)
    return {
        champ: group.iloc[0]["role_label"]
        for champ, group in counts.groupby("champion")
    }


def role_split_for_champion(df: pd.DataFrame, champion: str, min_games: int = 1) -> pd.DataFrame:
    """How one champion's games break down across roles, with win rate for
    each — the useful view when a champion is played in more than one spot."""
    subset = df[df["champion"] == champion]
    return win_rate_by_role(subset, min_games=min_games)


def relative_time(then, now=None) -> str:
    """Human "how long ago" string for the data-freshness indicator.

    Deliberately coarse — "3 hours ago" is what you want to know about a
    cache, and minute-level precision would imply the data is more live than
    it is. Returns "just now" for anything under a minute, and "unknown" for
    a missing timestamp rather than raising or printing a bare None."""
    if then is None:
        return "unknown"
    now = now or pd.Timestamp.utcnow()
    then = pd.Timestamp(then)
    # Compare like with like — a naive timestamp against an aware one raises.
    if then.tzinfo is None and now.tzinfo is not None:
        then = then.tz_localize(now.tzinfo)
    elif then.tzinfo is not None and now.tzinfo is None:
        now = now.tz_localize(then.tzinfo)

    # A negative span (clock skew, or a timestamp from the future) falls
    # through the same branch as "under a minute" and reports "just now",
    # which is the behaviour we want. An explicit `seconds < 0` guard lived
    # here briefly; mutation testing showed it could never fire, so it's
    # gone rather than sitting around looking like a real guard.
    seconds = (now - then).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        n = int(minutes)
        return f"{n} minute{'s' if n != 1 else ''} ago"
    hours = minutes / 60
    if hours < 24:
        n = int(hours)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    days = int(hours / 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


# Riot's numeric id for Flash. Slot 1 is the D key, slot 2 is F.
FLASH_SPELL_ID = 4

# Ping counters on every match-v5 participant. Undocumented in Riot's public
# reference but present in the payload — see developer-relations issue #754.
# Mapped to friendlier names since "enemyMissingPings" is the yellow question
# mark everyone calls "MIA".
PING_FIELDS = {
    "ping_all_in": "allInPings",
    "ping_assist_me": "assistMePings",
    "ping_bait": "baitPings",
    "ping_basic": "basicPings",
    "ping_command": "commandPings",
    "ping_danger": "dangerPings",
    "ping_enemy_missing": "enemyMissingPings",
    "ping_enemy_vision": "enemyVisionPings",
    "ping_hold": "holdPings",
    "ping_need_vision": "needVisionPings",
    "ping_on_my_way": "onMyWayPings",
    "ping_push": "pushPings",
    "ping_vision_cleared": "visionClearedPings",
}
PING_LABELS = {
    "ping_all_in": "All In",
    "ping_assist_me": "Assist Me",
    "ping_bait": "Bait",
    "ping_basic": "Basic",
    "ping_command": "Command",
    "ping_danger": "Danger",
    "ping_enemy_missing": "Enemy Missing",
    "ping_enemy_vision": "Enemy Vision",
    "ping_hold": "Hold",
    "ping_need_vision": "Need Vision",
    "ping_on_my_way": "On My Way",
    "ping_push": "Push",
    "ping_vision_cleared": "Vision Cleared",
}


def side_win_rate(df: pd.DataFrame, min_games: int = 1) -> pd.DataFrame:
    """Win rate on blue side vs. red, always in Blue-then-Red order so the
    two rows don't swap places as your record changes."""
    result = win_rate_by(df, "side", min_games=min_games)
    if result.empty:
        return result
    order = {"Blue": 0, "Red": 1}
    result["_sort"] = result["side"].map(order).fillna(99)
    return result.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)


def ping_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Average pings per game by type, most-used first. Blank when the
    columns aren't present — these fields only appear on reasonably recent
    matches, so an older cache legitimately won't have them."""
    cols = [c for c in PING_FIELDS if c in df.columns]
    if df.empty or not cols:
        return pd.DataFrame(columns=["ping", "per_game", "total"])
    rows = [
        {
            "ping": PING_LABELS.get(col, col),
            "per_game": round(df[col].mean(), 2),
            "total": int(df[col].sum()),
        }
        for col in cols
    ]
    result = pd.DataFrame(rows)
    result = result[result["total"] > 0]
    return result.sort_values("total", ascending=False).reset_index(drop=True)


def surrender_summary(df: pd.DataFrame) -> dict:
    """How often your games end in a surrender, and how those split between
    wins and losses. Surrendering isn't inherently good or bad — a fast FF
    on a lost game saves time — so this is reported flat, without a verdict."""
    empty = {
        "surrender_rate": 0.0, "early_surrender_rate": 0.0,
        "surrendered_games": 0, "wins_by_surrender": 0, "losses_by_surrender": 0,
    }
    if df.empty or "ended_in_surrender" not in df.columns:
        return empty
    ff = df[df["ended_in_surrender"]]
    return {
        "surrender_rate": round(len(ff) / len(df) * 100, 1),
        "early_surrender_rate": round(df["ended_in_early_surrender"].mean() * 100, 1)
        if "ended_in_early_surrender" in df.columns else 0.0,
        "surrendered_games": len(ff),
        "wins_by_surrender": int(ff["win"].sum()),
        "losses_by_surrender": int((~ff["win"]).sum()),
    }


def flash_slot_win_rate(df: pd.DataFrame, min_games: int = 1) -> pd.DataFrame:
    """Win rate with Flash on D vs. on F — the long-running community
    argument, answerable from your own games. Any real difference is far
    more likely to be habit and sample size than the keybind itself."""
    if df.empty or "flash_slot" not in df.columns:
        return pd.DataFrame(columns=["flash_slot", "games", "wins", "win_rate"])
    # No explicit dropna: `win_rate_by` groups with pandas, which already
    # drops null keys, so games without Flash fall out on their own. A
    # dropna() call lived here briefly; mutation testing showed removing it
    # changed nothing, so it's gone rather than implying it does work.
    result = win_rate_by(df, "flash_slot", min_games=min_games)
    if result.empty:
        return result
    order = {"D": 0, "F": 1}
    result["_sort"] = result["flash_slot"].map(order).fillna(99)
    return result.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)


def game_length_bucket(duration_min: float) -> str:
    if duration_min < 20:
        return "<20 min"
    if duration_min < 30:
        return "20-30 min"
    if duration_min < 40:
        return "30-40 min"
    return "40+ min"


def parse_match(match: dict, puuid: str) -> dict | None:
    """Pull out the tracked player's participant entry from a match-v5
    response. Returns None if the player isn't found in this match
    (shouldn't happen, but keeps things defensive)."""
    info = match["info"]
    participants = info["participants"]
    participant = next((p for p in participants if p["puuid"] == puuid), None)
    if participant is None:
        return None

    duration_min = max(info.get("gameDuration", 0) / 60, 1 / 60)  # avoid div-by-zero
    cs = participant.get("totalMinionsKilled", 0) + participant.get("neutralMinionsKilled", 0)
    deaths = participant.get("deaths", 0)
    kills = participant.get("kills", 0)
    assists = participant.get("assists", 0)
    queue_id = info.get("queueId")

    items = [participant.get(f"item{i}", 0) for i in range(6)]  # item0-5, excludes trinket slot 6
    build = tuple(sorted(i for i in items if i))  # order-independent "what did you end with"

    team_kills = sum(p.get("kills", 0) for p in participants if p["teamId"] == participant["teamId"])
    team_damage = sum(
        p.get("totalDamageDealtToChampions", 0)
        for p in participants
        if p["teamId"] == participant["teamId"]
    )
    damage_dealt = participant.get("totalDamageDealtToChampions", 0)

    teams = info.get("teams", [])
    my_team = next((t for t in teams if t.get("teamId") == participant["teamId"]), None)
    objectives = (my_team or {}).get("objectives", {})

    game_version = info.get("gameVersion", "0.0")
    patch = ".".join(game_version.split(".")[:2]) if game_version else "unknown"

    # Runes: perks.styles[0] is the primary tree, its first selection is the
    # keystone; perks.styles[1] (if present) is the secondary tree.
    perk_styles = participant.get("perks", {}).get("styles", [])
    primary_selections = perk_styles[0].get("selections", []) if perk_styles else []
    keystone_id = primary_selections[0].get("perk") if primary_selections else None
    primary_style_id = perk_styles[0].get("style") if perk_styles else None
    sub_style_id = perk_styles[1].get("style") if len(perk_styles) > 1 else None

    summoner1_id = participant.get("summoner1Id")
    summoner2_id = participant.get("summoner2Id")
    summoner_combo = tuple(sorted(s for s in [summoner1_id, summoner2_id] if s is not None))

    teammates = [p for p in participants if p["teamId"] == participant["teamId"] and p["puuid"] != puuid]
    teammate_puuids = tuple(p["puuid"] for p in teammates)
    teammate_names = tuple((p.get("riotIdGameName") or p.get("summonerName") or "?") for p in teammates)

    # Everyone's champion, both sides. This was already sitting in the cached
    # match JSON and simply never extracted — so adding it costs nothing but a
    # re-parse, no extra API calls against the rate-limited dev key. It's what
    # makes the live-game tips possible: without it there's no way to ask
    # "how do I do when this champion is on the other team?".
    #
    # Sorted so the tuples are comparable and group cleanly; the draft order
    # they arrive in carries no meaning worth preserving here.
    enemies = [p for p in participants if p["teamId"] != participant["teamId"]]
    ally_champions = tuple(sorted(
        p.get("championName") for p in teammates if p.get("championName")
    ))
    enemy_champions = tuple(sorted(
        p.get("championName") for p in enemies if p.get("championName")
    ))

    return {
        "match_id": match["metadata"]["matchId"],
        "game_creation": to_local_time(info["gameCreation"]),
        "game_duration_min": round(duration_min, 1),
        "game_mode": info.get("gameMode"),
        "patch": patch,
        "queue_id": queue_id,
        "queue_type": queue_label(queue_id),
        "queue_category": queue_category(queue_id),
        "game_length_bucket": game_length_bucket(duration_min),
        "champion": participant.get("championName"),
        "skin_id": participant.get("skinId", 0),
        "role": participant.get("teamPosition") or participant.get("individualPosition"),
        "role_label": role_label(
            participant.get("teamPosition") or participant.get("individualPosition"),
            queue_category(queue_id),
        ),
        "win": bool(participant.get("win")),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(deaths, 1), 2),
        "cs": cs,
        "cs_per_min": round(cs / duration_min, 1),
        "vision_score": participant.get("visionScore", 0),
        "gold_earned": participant.get("goldEarned", 0),
        "damage_dealt": damage_dealt,
        "kill_participation": round((kills + assists) / max(team_kills, 1) * 100, 1),
        "damage_share": round(damage_dealt / max(team_damage, 1) * 100, 1),
        "first_blood": bool(participant.get("firstBloodKill") or participant.get("firstBloodAssist")),
        "first_tower": bool(participant.get("firstTowerKill") or participant.get("firstTowerAssist")),
        "team_first_blood": bool(objectives.get("champion", {}).get("first")),
        "team_first_dragon": bool(objectives.get("dragon", {}).get("first")),
        "team_first_baron": bool(objectives.get("baron", {}).get("first")),
        "team_first_tower": bool(objectives.get("tower", {}).get("first")),
        "items": items,
        "trinket": participant.get("item6", 0),
        "build": build,
        "keystone_id": keystone_id,
        "primary_style_id": primary_style_id,
        "sub_style_id": sub_style_id,
        "summoner1_id": summoner1_id,
        "summoner2_id": summoner2_id,
        "summoner_combo": summoner_combo,
        "duo_partner": _find_same_team(participants, participant, opposing=False),
        "opponent_champion": _find_same_team(participants, participant, opposing=True),
        "opponent_puuid": _find_same_team_puuid(participants, participant, opposing=True),
        "teammate_puuids": teammate_puuids,
        "ally_champions": ally_champions,
        "enemy_champions": enemy_champions,
        "teammate_names": teammate_names,
        "double_kills": participant.get("doubleKills", 0),
        "triple_kills": participant.get("tripleKills", 0),
        "quadra_kills": participant.get("quadraKills", 0),
        "penta_kills": participant.get("pentaKills", 0),
        "side": "Blue" if participant.get("teamId") == 100 else "Red",
        # Which key each summoner spell was on. Riot reports slot 1 = D and
        # slot 2 = F; the existing `summoner_combo` sorts them, which loses
        # this, so it's captured separately.
        "flash_slot": (
            "D" if participant.get("summoner1Id") == FLASH_SPELL_ID
            else "F" if participant.get("summoner2Id") == FLASH_SPELL_ID
            else None
        ),
        "ended_in_surrender": bool(participant.get("gameEndedInSurrender")),
        "ended_in_early_surrender": bool(participant.get("gameEndedInEarlySurrender")),
        "total_pings": sum(participant.get(f, 0) or 0 for f in PING_FIELDS.values()),
        **{
            name: participant.get(field, 0) or 0
            for name, field in PING_FIELDS.items()
        },
        "wards_placed": participant.get("wardsPlaced", 0),
        "wards_killed": participant.get("wardsKilled", 0),
        "vision_wards_bought": participant.get("visionWardsBoughtInGame", 0),
        "cc_score": participant.get("timeCCingOthers", 0),
    }


def _find_same_team_participant(participants: list[dict], me: dict, opposing: bool) -> dict | None:
    """Find the player in the same role — either the duo partner (same team,
    complementary bot-lane role) or the lane opponent (same role, other team).

    These are heuristics: Riot's public API doesn't expose premade party
    data, and "opponent" for jungle/support is a looser concept than for
    solo lanes, but same-role-opposite-team is still the standard proxy
    used by most LoL stat sites.
    """
    my_role = me.get("teamPosition")
    if not my_role:
        return None

    if opposing:
        candidates = [p for p in participants if p["teamId"] != me["teamId"]]
        target_role = my_role
    else:
        if my_role not in ("BOTTOM", "UTILITY"):
            return None
        candidates = [p for p in participants if p["teamId"] == me["teamId"]]
        target_role = "UTILITY" if my_role == "BOTTOM" else "BOTTOM"

    return next((p for p in candidates if p.get("teamPosition") == target_role), None)


def _find_same_team(participants: list[dict], me: dict, opposing: bool) -> str | None:
    p = _find_same_team_participant(participants, me, opposing)
    return p.get("championName") if p else None


def _find_same_team_puuid(participants: list[dict], me: dict, opposing: bool) -> str | None:
    p = _find_same_team_participant(participants, me, opposing)
    return p.get("puuid") if p else None


def match_scoreboard(match: dict, puuid: str) -> pd.DataFrame:
    """Full 10-player breakdown for one match — both teams, not just the
    tracked player — for the match-detail dropdown."""
    info = match["info"]
    participants = info["participants"]
    me = next((p for p in participants if p["puuid"] == puuid), None)
    my_team = me["teamId"] if me else None
    duration_min = max(info.get("gameDuration", 0) / 60, 1 / 60)

    rows = []
    for p in participants:
        cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
        rows.append({
            "side": "Your Team" if p["teamId"] == my_team else "Enemy Team",
            "is_me": p["puuid"] == puuid,
            "champion": p.get("championName"),
            "role": p.get("teamPosition") or p.get("individualPosition"),
            "role_label": role_label(
                p.get("teamPosition") or p.get("individualPosition"),
                queue_category(info.get("queueId")),
            ),
            "name": p.get("riotIdGameName") or p.get("summonerName") or "?",
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "assists": p.get("assists", 0),
            "cs": cs,
            "cs_per_min": round(cs / duration_min, 1),
            "gold_earned": p.get("goldEarned", 0),
            "items": [p.get(f"item{i}", 0) for i in range(7)],  # includes trinket slot 6
            "win": bool(p.get("win")),
        })
    return pd.DataFrame(rows)


def build_dataframe(matches: list[dict], puuid: str) -> pd.DataFrame:
    rows = [parse_match(m, puuid) for m in matches]
    rows = [r for r in rows if r is not None]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("game_creation").reset_index(drop=True)
    return df


def win_rate_by(df: pd.DataFrame, column: str, min_games: int = 1) -> pd.DataFrame:
    """Win rate + game count grouped by an arbitrary column (champion, role, etc.)."""
    if df.empty or column not in df.columns:
        return pd.DataFrame(columns=[column, "games", "wins", "win_rate"])

    grouped = (
        df.groupby(column)["win"]
        .agg(games="count", wins="sum")
        .reset_index()
    )
    grouped["win_rate"] = (grouped["wins"] / grouped["games"] * 100).round(1)
    grouped = grouped[grouped["games"] >= min_games]
    return grouped.sort_values("games", ascending=False).reset_index(drop=True)


def matchup_win_rate(df: pd.DataFrame, champion: str, min_games: int = 1,
                     role: str | None = None) -> pd.DataFrame:
    """Win rate for `champion` broken down by opponent faced in lane.

    `role` takes a display label ("Jungle", "Mid", ...) and restricts to games
    played there. Without it, a champion you play in two positions pools two
    completely different matchups into one number: Shaco jungle facing an
    enemy jungler and Shaco top facing a top laner are not the same fight, and
    averaging them describes neither.
    """
    subset = df[(df["champion"] == champion) & df["opponent_champion"].notna()]
    if role and "role_label" in subset.columns:
        subset = subset[subset["role_label"] == role]
    return win_rate_by(subset, "opponent_champion", min_games=min_games).rename(
        columns={"opponent_champion": "vs_champion"}
    )


def roles_played(df: pd.DataFrame, champion: str | None = None) -> list[str]:
    """Roles present in `df` (optionally for one champion), in lane order.

    Drives the role selector on the deep-dive. Ordered Top -> Support rather
    than by frequency so the control doesn't reshuffle itself as you play.
    """
    subset = df if champion is None else df[df["champion"] == champion]
    if subset.empty or "role_label" not in subset.columns:
        return []
    present = set(subset["role_label"].dropna().unique())
    ordered = [r for r in ROLE_ORDER if r in present]
    # Anything unexpected (a new position label, or ARAM leaking in) still
    # gets shown rather than silently dropped.
    return ordered + sorted(present - set(ordered))


def matchup_win_rate_by_role(df: pd.DataFrame, champion: str,
                             min_games: int = 1) -> pd.DataFrame:
    """Every matchup for `champion`, tagged with the role it was played in."""
    frames = []
    for role in roles_played(df, champion):
        table = matchup_win_rate(df, champion, min_games=min_games, role=role)
        if not table.empty:
            table = table.copy()
            table.insert(0, "role", role)
            frames.append(table)
    if not frames:
        return pd.DataFrame(columns=["role", "vs_champion", "games", "wins", "win_rate"])
    return pd.concat(frames, ignore_index=True)


def build_win_rate(df: pd.DataFrame, champion: str, min_games: int = 1) -> pd.DataFrame:
    """Win rate grouped by final item build (item0-5, order-independent) for
    one champion. This reflects what you ended games with, not purchase
    order — Riot's match-v5 endpoint only gives final inventory. Purchase
    order would require a separate timeline API call per match."""
    subset = df[(df["champion"] == champion) & (df["build"].map(len) > 0)]
    if subset.empty:
        return pd.DataFrame(columns=["build", "games", "wins", "win_rate"])
    grouped = win_rate_by(subset, "build", min_games=min_games)
    return grouped


def skin_usage(df: pd.DataFrame, champion: str) -> pd.DataFrame:
    """Games played (and win rate) per skin for one champion."""
    subset = df[df["champion"] == champion]
    return win_rate_by(subset, "skin_id")


KILL_DIFF_ORDER = ["-5 or worse", "-4 to -1", "Even", "+1 to +4", "+5 or more"]


def _kill_diff_bucket(diff: int) -> str:
    if diff <= -5:
        return "-5 or worse"
    if diff <= -1:
        return "-4 to -1"
    if diff == 0:
        return "Even"
    if diff <= 4:
        return "+1 to +4"
    return "+5 or more"


def kill_diff_win_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Win rate grouped by kills-minus-deaths — a cheap, no-extra-API-calls
    proxy for "were you up or down kills" in a game."""
    if df.empty:
        return pd.DataFrame(columns=["kill_diff_bucket", "games", "wins", "win_rate"])
    d = df.copy()
    d["kill_diff_bucket"] = (d["kills"] - d["deaths"]).apply(_kill_diff_bucket)
    result = win_rate_by(d, "kill_diff_bucket")
    result["kill_diff_bucket"] = pd.Categorical(
        result["kill_diff_bucket"], categories=KILL_DIFF_ORDER, ordered=True
    )
    return result.sort_values("kill_diff_bucket").reset_index(drop=True)


def find_participant_id(timeline: dict, puuid: str) -> int | None:
    """Match timelines identify players by a per-match `participantId`
    (1-10), not puuid directly — resolve it from the timeline's own
    participant list."""
    for p in timeline.get("info", {}).get("participants", []):
        if p.get("puuid") == puuid:
            return p.get("participantId")
    return None


def parse_purchase_events(timeline: dict, puuid: str) -> list[dict]:
    """Chronological ITEM_PURCHASED events for one player in one match.
    Doesn't reconcile ITEM_SOLD/ITEM_UNDO — this is the raw purchase log,
    which is what most build-order timelines show anyway."""
    participant_id = find_participant_id(timeline, puuid)
    if participant_id is None:
        return []

    events = []
    for frame in timeline.get("info", {}).get("frames", []):
        for event in frame.get("events", []):
            if event.get("type") == "ITEM_PURCHASED" and event.get("participantId") == participant_id:
                events.append(
                    {"item_id": event["itemId"], "timestamp_min": round(event["timestamp"] / 60000, 1)}
                )
    return sorted(events, key=lambda e: e["timestamp_min"])


# ==================== Skill order ====================
# Summoner's Rift caps skills at Q/W/E 5 points each + R 3 = 18 total.
SKILL_SLOT_NAMES = {1: "Q", 2: "W", 3: "E", 4: "R"}
MAX_BASIC_SKILL_POINTS = 5
MAX_ULT_POINTS = 3
MAX_SKILL_POINTS = MAX_BASIC_SKILL_POINTS * 3 + MAX_ULT_POINTS  # 18


def parse_skill_level_ups(timeline: dict, puuid: str) -> list[int] | None:
    """Chronological list of skill slots (1=Q, 2=W, 3=E, 4=R) this player
    leveled, or None when the data is untrustworthy.

    Defensive because of a live, unresolved Riot bug: since patch 15.17,
    match-v5 timelines intermittently emit *duplicate* SKILL_LEVEL_UP events
    — identical participantId/skillSlot/timestamp within a frame — which can
    inflate a player past 30 skill-ups when 18 is the hard maximum. Riot's
    developer-relations issue #1100 is open and escalated, so this isn't
    something that'll fix itself client-side.

    Handling: drop exact duplicates (same slot at the same timestamp), then
    sanity-check the result against the game's real limits. Riot's reporter
    suggested discarding *both* copies of a duplicate pair, but that's lossy
    and can delete a legitimate event, so this de-duplicates instead and
    simply refuses to guess when the totals still come out impossible —
    returning None so callers can exclude the game and say so, rather than
    silently charting a wrong skill order."""
    participant_id = find_participant_id(timeline, puuid)
    if participant_id is None:
        return None

    seen = set()
    events = []
    for frame in timeline.get("info", {}).get("frames", []):
        for event in frame.get("events", []):
            if event.get("type") != "SKILL_LEVEL_UP":
                continue
            if event.get("participantId") != participant_id:
                continue
            slot = event.get("skillSlot")
            if slot not in SKILL_SLOT_NAMES:
                continue
            key = (slot, event.get("timestamp"))
            if key in seen:
                continue  # exact duplicate — the known API bug
            seen.add(key)
            events.append((event.get("timestamp", 0), slot))

    if not events:
        return None

    events.sort()
    slots = [slot for _, slot in events]

    # Sanity check against the game's actual rules. If it still doesn't add
    # up, the payload is corrupt for this match and we say nothing.
    #
    # Per-slot caps are the authoritative check, and they also imply the
    # overall MAX_SKILL_POINTS limit — 5+5+5+3 is exactly 18, so any total
    # above that necessarily breaks one of these. (An explicit total check
    # lived here briefly; mutation testing showed it could never fire, so
    # it's gone rather than sitting around looking like a real guard.)
    for slot in (1, 2, 3):
        if slots.count(slot) > MAX_BASIC_SKILL_POINTS:
            return None
    if slots.count(4) > MAX_ULT_POINTS:
        return None
    return slots


def skill_max_order(slots: list[int] | None) -> tuple[str, ...]:
    """Which basic abilities got maxed, in the order they hit 5 points —
    the "Q > E > W" shorthand these sites display. Falls back to ordering by
    points invested when a game ended before anything was maxed, so short
    games still contribute their priority rather than being dropped."""
    if not slots:
        return ()
    order = []
    counts = {1: 0, 2: 0, 3: 0}
    for slot in slots:
        if slot not in counts:
            continue
        counts[slot] += 1
        if counts[slot] == MAX_BASIC_SKILL_POINTS:
            order.append(SKILL_SLOT_NAMES[slot])
    if order:
        return tuple(order)
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return tuple(SKILL_SLOT_NAMES[slot] for slot, n in ranked if n > 0)


def skill_order_win_rate(
    df: pd.DataFrame, orders: dict, champion: str, min_games: int = 1
) -> pd.DataFrame:
    """Win rate grouped by max order for one champion. `orders` maps
    match_id -> skill_max_order() tuple, computed by the caller from
    timelines (same on-demand pattern as opening builds)."""
    subset = df[df["champion"] == champion].copy()
    subset["skill_order"] = subset["match_id"].map(orders)
    subset = subset[subset["skill_order"].apply(lambda o: isinstance(o, tuple) and len(o) > 0)]
    if subset.empty:
        return pd.DataFrame(columns=["skill_order", "games", "wins", "win_rate"])
    return win_rate_by(subset, "skill_order", min_games=min_games)


GOLD_CHECKPOINTS = [10, 15, 20, 25]


def gold_at_minute(timeline: dict, puuid: str, minute: int) -> int | None:
    """Total gold for one player at the frame closest to `minute`. Frames
    are ~1/min, so this is a snapshot, not an exact-second reading."""
    participant_id = find_participant_id(timeline, puuid)
    frames = timeline.get("info", {}).get("frames", [])
    if participant_id is None or not frames:
        return None
    target_ms = minute * 60000
    closest = min(frames, key=lambda f: abs(f.get("timestamp", 0) - target_ms))
    pframe = closest.get("participantFrames", {}).get(str(participant_id))
    return pframe.get("totalGold") if pframe else None


def gold_curve(timelines: dict, puuid: str) -> pd.DataFrame:
    """Gold at each checkpoint minute for a set of {match_id: timeline}."""
    rows = []
    for match_id, tl in timelines.items():
        row = {"match_id": match_id}
        for m in GOLD_CHECKPOINTS:
            row[f"gold_{m}"] = gold_at_minute(tl, puuid, m)
        rows.append(row)
    return pd.DataFrame(rows)


def gold_curve_summary(gold_df: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Average gold at each checkpoint, overall and split by win/loss."""
    if gold_df.empty:
        return pd.DataFrame(columns=["minute", "avg_gold", "avg_gold_win", "avg_gold_loss"])
    merged = gold_df.merge(df[["match_id", "win"]], on="match_id", how="left")
    rows = []
    for m in GOLD_CHECKPOINTS:
        col = f"gold_{m}"
        if col not in merged:
            continue
        wins = merged[merged["win"] == True][col]  # noqa: E712
        losses = merged[merged["win"] == False][col]  # noqa: E712
        rows.append({
            "minute": m,
            "avg_gold": round(merged[col].mean(), 0) if merged[col].notna().any() else None,
            "avg_gold_win": round(wins.mean(), 0) if wins.notna().any() else None,
            "avg_gold_loss": round(losses.mean(), 0) if losses.notna().any() else None,
        })
    return pd.DataFrame(rows)


def opening_build(purchase_events: list[dict], items_data: dict, n: int = 3) -> tuple[int, ...]:
    """First `n` core (non-consumable/trinket) items purchased, in order —
    used to group games by "what did you rush" for a build-order win rate."""
    from ddragon import is_core_item  # local import: keeps stats.py free of a hard ddragon dependency

    core_ids = [
        e["item_id"] for e in purchase_events if is_core_item(e["item_id"], items_data)
    ]
    return tuple(core_ids[:n])


def opening_build_win_rate(
    df: pd.DataFrame, openings: dict, champion: str, min_games: int = 1
) -> pd.DataFrame:
    """Win rate grouped by opening build order for one champion.
    `openings` maps match_id -> opening_build() tuple (computed by the
    caller, since it requires timeline data fetched on demand)."""
    subset = df[df["champion"] == champion].copy()
    subset["opening"] = subset["match_id"].map(openings)
    subset = subset[subset["opening"].apply(lambda o: isinstance(o, tuple) and len(o) > 0)]
    if subset.empty:
        return pd.DataFrame(columns=["opening", "games", "wins", "win_rate"])
    return win_rate_by(subset, "opening", min_games=min_games)


def overall_win_rate(df: pd.DataFrame) -> tuple[int, int, float]:
    if df.empty:
        return 0, 0, 0.0
    games = len(df)
    wins = int(df["win"].sum())
    return games, wins, round(wins / games * 100, 1)


def averages(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "kda": 0.0, "cs_per_min": 0.0, "vision_score": 0.0,
            "kill_participation": 0.0, "damage_share": 0.0, "cc_score": 0.0,
        }
    return {
        "kda": round(df["kda"].mean(), 2),
        "cs_per_min": round(df["cs_per_min"].mean(), 1),
        "vision_score": round(df["vision_score"].mean(), 1),
        "kill_participation": round(df["kill_participation"].mean(), 1),
        "damage_share": round(df["damage_share"].mean(), 1),
        "cc_score": round(df["cc_score"].mean(), 1) if "cc_score" in df.columns else 0.0,
    }


def vision_summary(df: pd.DataFrame) -> dict:
    """Average wards placed/killed and control wards bought per game —
    counts only (not positions, which Riot's API doesn't expose for privacy/
    competitive-integrity reasons — see the death/kill heatmap's note on
    that same limitation). Useful on its own, especially for support mains,
    where kill participation and damage share undersell the role's value."""
    if df.empty:
        return {"wards_placed": 0.0, "wards_killed": 0.0, "vision_wards_bought": 0.0}
    cols = ["wards_placed", "wards_killed", "vision_wards_bought"]
    return {
        col: round(df[col].mean(), 1) if col in df.columns else 0.0
        for col in cols
    }


def win_rate_by_length(df: pd.DataFrame, min_games: int = 3) -> pd.DataFrame:
    """Win rate grouped by game-length bucket, in logical short-to-long
    order (not by frequency) — a quick read on whether you're a snowballer
    (better in short games) or a scaler (better in long ones)."""
    result = win_rate_by(df, "game_length_bucket", min_games=min_games)
    if result.empty:
        return result
    order = {b: i for i, b in enumerate(GAME_LENGTH_ORDER)}
    result["_sort"] = result["game_length_bucket"].map(order).fillna(99)
    return result.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)


def rolling_trend(df: pd.DataFrame, column: str, window: int = 10) -> pd.DataFrame:
    """Per-game values plus a rolling average for smoother trend lines."""
    sorted_df = df.sort_values("game_creation").reset_index(drop=True)
    sorted_df[f"{column}_rolling"] = (
        sorted_df[column].rolling(window=window, min_periods=1).mean().round(2)
    )
    return sorted_df


def recent_vs_alltime(df: pd.DataFrame, column: str, recent_n: int = 10) -> tuple[float, float]:
    """Average of the last `recent_n` games vs. the all-time average, for a
    quick 'trending up or down' delta indicator."""
    if df.empty:
        return 0.0, 0.0
    sorted_df = df.sort_values("game_creation")
    recent_avg = round(sorted_df[column].tail(recent_n).mean(), 2)
    alltime_avg = round(sorted_df[column].mean(), 2)
    return recent_avg, alltime_avg


# ==================== Tilt detection (free — no extra API calls) ====================

WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def performance_by_hour(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["hour", "games", "wins", "win_rate"])
    d = df.copy()
    d["hour"] = d["game_creation"].dt.hour
    return win_rate_by(d, "hour").sort_values("hour").reset_index(drop=True)


def performance_by_weekday(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["weekday", "games", "wins", "win_rate"])
    d = df.copy()
    d["weekday"] = d["game_creation"].dt.day_name()
    result = win_rate_by(d, "weekday")
    result["weekday"] = pd.Categorical(result["weekday"], categories=WEEKDAY_ORDER, ordered=True)
    return result.sort_values("weekday").reset_index(drop=True)


def win_rate_after_result(df: pd.DataFrame) -> pd.DataFrame:
    """Win rate in the game right after a win vs. right after a loss — a
    rough 'tilt' signal. Uses games in `df` sorted chronologically as a
    proxy for "the previous game"; if a queue filter is applied there may
    be gaps, so this approximates back-to-back sessions rather than
    guaranteeing them."""
    if len(df) < 2:
        return pd.DataFrame(columns=["prev_result", "games", "wins", "win_rate"])
    d = df.sort_values("game_creation").reset_index(drop=True)
    d["prev_result"] = d["win"].shift(1).map({True: "After a Win", False: "After a Loss"})
    d = d.dropna(subset=["prev_result"])
    return win_rate_by(d, "prev_result")


def longest_streak(df: pd.DataFrame, want_win: bool = True) -> int:
    """Longest consecutive run of wins (or losses) in chronological order."""
    if df.empty:
        return 0
    longest = current = 0
    for win in df.sort_values("game_creation")["win"]:
        if win == want_win:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


# ==================== Objective participation (timeline-based) ====================

def objective_participation(timeline: dict, puuid: str) -> dict:
    """Count dragon/baron/herald/tower kills this player was the killer or
    an assist on, from timeline events."""
    counts = {"dragons": 0, "barons": 0, "heralds": 0, "towers": 0}
    participant_id = find_participant_id(timeline, puuid)
    if participant_id is None:
        return counts

    for frame in timeline.get("info", {}).get("frames", []):
        for event in frame.get("events", []):
            involved = (
                event.get("killerId") == participant_id
                or participant_id in event.get("assistingParticipantIds", [])
            )
            if not involved:
                continue
            etype = event.get("type")
            if etype == "ELITE_MONSTER_KILL":
                monster = event.get("monsterType")
                if monster == "DRAGON":
                    counts["dragons"] += 1
                elif monster == "BARON_NASHOR":
                    counts["barons"] += 1
                elif monster == "RIFTHERALD":
                    counts["heralds"] += 1
            elif etype == "BUILDING_KILL" and event.get("buildingType") == "TOWER_BUILDING":
                counts["towers"] += 1
    return counts


def objective_participation_summary(timelines: dict, puuid: str) -> pd.DataFrame:
    rows = []
    for match_id, tl in timelines.items():
        c = objective_participation(tl, puuid)
        c["match_id"] = match_id
        rows.append(c)
    return pd.DataFrame(rows)


# ==================== Lane-opponent gold/CS differential ====================

def gold_diff_curve(timelines: dict, opponent_puuids: dict, puuid: str) -> pd.DataFrame:
    """Gold difference (you minus lane opponent) at each checkpoint minute.
    `opponent_puuids` maps match_id -> opponent puuid (from the main
    per-match DataFrame's `opponent_puuid` column, itself a same-role-
    opposite-team heuristic)."""
    rows = []
    for match_id, tl in timelines.items():
        opp_puuid = opponent_puuids.get(match_id)
        if not opp_puuid:
            continue
        row = {"match_id": match_id}
        for m in GOLD_CHECKPOINTS:
            mine = gold_at_minute(tl, puuid, m)
            theirs = gold_at_minute(tl, opp_puuid, m)
            row[f"diff_{m}"] = (mine - theirs) if (mine is not None and theirs is not None) else None
        rows.append(row)
    return pd.DataFrame(rows)


def cs_at_minute(timeline: dict, puuid: str, minute: int) -> int | None:
    """Creep score (lane minions + jungle camps) for one player at the frame
    closest to `minute`. Same frame-snapshot approach as gold_at_minute —
    participantFrames carry `minionsKilled` and `jungleMinionsKilled`
    separately, so real CS is the sum of both (a jungler's CS is almost
    entirely the second field)."""
    participant_id = find_participant_id(timeline, puuid)
    frames = timeline.get("info", {}).get("frames", [])
    if participant_id is None or not frames:
        return None
    target_ms = minute * 60000
    closest = min(frames, key=lambda f: abs(f.get("timestamp", 0) - target_ms))
    pframe = closest.get("participantFrames", {}).get(str(participant_id))
    if not pframe:
        return None
    return pframe.get("minionsKilled", 0) + pframe.get("jungleMinionsKilled", 0)


def cs_diff_curve(timelines: dict, opponent_puuids: dict, puuid: str) -> pd.DataFrame:
    """CS difference (you minus lane opponent) at each checkpoint minute.
    Same shape and same timeline fetch as `gold_diff_curve` — computing this
    alongside it is free. CS lead is often a cleaner read on lane phase than
    gold, since gold also swings on kills/plates/bounties that aren't
    strictly about winning the lane."""
    rows = []
    for match_id, tl in timelines.items():
        opp_puuid = opponent_puuids.get(match_id)
        if not opp_puuid:
            continue
        row = {"match_id": match_id}
        for m in GOLD_CHECKPOINTS:
            mine = cs_at_minute(tl, puuid, m)
            theirs = cs_at_minute(tl, opp_puuid, m)
            row[f"diff_{m}"] = (mine - theirs) if (mine is not None and theirs is not None) else None
        rows.append(row)
    return pd.DataFrame(rows)


def cs_diff_summary(diff_df: pd.DataFrame) -> pd.DataFrame:
    if diff_df.empty:
        return pd.DataFrame(columns=["minute", "avg_diff"])
    rows = []
    for m in GOLD_CHECKPOINTS:
        col = f"diff_{m}"
        if col not in diff_df:
            continue
        rows.append({
            "minute": m,
            "avg_diff": round(diff_df[col].mean(), 1) if diff_df[col].notna().any() else None,
        })
    return pd.DataFrame(rows)


def current_streak(df: pd.DataFrame) -> tuple[str | None, int]:
    """Your *active* streak right now — ("win"|"loss", length) counting back
    from the most recent game. Distinct from `longest_streak`, which is a
    historical best; this is the "you're on a 3-game skid right now" signal,
    which is more actionable than a retrospective average."""
    if df.empty:
        return None, 0
    ordered = df.sort_values("game_creation", ascending=False)
    latest = bool(ordered.iloc[0]["win"])
    count = 0
    for _, row in ordered.iterrows():
        if bool(row["win"]) != latest:
            break
        count += 1
    return ("win" if latest else "loss"), count


def personal_bests(df: pd.DataFrame) -> list[dict]:
    """Career highs across the given games — the single best game for each of
    a few headline stats. Returns [] rather than a partial list when there's
    no data, so callers can render one clean empty state."""
    if df.empty:
        return []
    specs = [
        ("kills", "Most Kills", lambda v: f"{int(v)} kills"),
        ("kda", "Highest KDA", lambda v: f"{v} KDA"),
        ("cs", "Most CS", lambda v: f"{int(v)} CS"),
        ("damage_dealt", "Most Damage", lambda v: f"{int(v):,} dmg"),
        ("vision_score", "Highest Vision", lambda v: f"{int(v)} vision"),
        ("game_duration_min", "Longest Game", lambda v: f"{v} min"),
    ]
    results = []
    for col, label, fmt in specs:
        if col not in df.columns or df[col].isna().all():
            continue
        row = df.loc[df[col].idxmax()]
        results.append({
            "label": label,
            "value": fmt(row[col]),
            "champion": row.get("champion"),
            "date_str": row["game_creation"].strftime("%b %d, %Y"),
            "win": bool(row["win"]),
        })
    return results


# ==================== Win probability (empirical, from your own games) ====================
# Inspired by Coachless' Win Probability Added work. Big honest difference:
# theirs is a logistic model trained across a large multi-player dataset with
# rank filtering. This is an *empirical* lookup built only from your own
# match history — "when my team was +3k at 20 minutes, I went on to win X of
# Y games." That's a much smaller, noisier sample, so every reading carries
# its own game count and thin buckets fall back to 50% rather than inventing
# a confident number.
WP_MINUTE_BUCKET = 5      # minutes per bin
WP_GOLD_BUCKET = 2000     # gold-diff per bin
WP_MIN_SAMPLE = 5         # below this, don't claim to know


def participant_teams(timeline: dict, match: dict) -> dict[int, int]:
    """participantId -> teamId, joined on puuid.

    Deliberately does NOT rely on the common convention that participants
    1-5 are team 100 and 6-10 are team 200. That's usually true but isn't
    guaranteed anywhere in Riot's docs, and this is cheap to do properly
    since the match JSON is already cached alongside every timeline."""
    puuid_to_team = {
        p.get("puuid"): p.get("teamId") for p in match.get("info", {}).get("participants", [])
    }
    teams = {}
    for p in timeline.get("info", {}).get("participants", []):
        team = puuid_to_team.get(p.get("puuid"))
        if team is not None and p.get("participantId") is not None:
            teams[p["participantId"]] = team
    return teams


def team_gold_diff_series(timeline: dict, teams: dict[int, int], my_team: int) -> list[tuple[int, int]]:
    """[(minute, my_team_gold - enemy_team_gold)] across the whole game.
    Team totals, not the lane-opponent differential computed elsewhere —
    win probability is about the game state, not your lane."""
    if not teams:
        return []
    series = []
    for frame in timeline.get("info", {}).get("frames", []):
        pframes = frame.get("participantFrames", {})
        if not pframes:
            continue
        mine = enemy = 0
        for pid_str, pf in pframes.items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            gold = pf.get("totalGold", 0)
            team = teams.get(pid)
            if team is None:
                continue
            if team == my_team:
                mine += gold
            else:
                enemy += gold
        series.append((round(frame.get("timestamp", 0) / 60000), mine - enemy))
    return series


def _wp_bucket(minute: int, gold_diff: int) -> tuple[int, int]:
    return (
        int(minute // WP_MINUTE_BUCKET) * WP_MINUTE_BUCKET,
        int(gold_diff // WP_GOLD_BUCKET) * WP_GOLD_BUCKET,
    )


def build_win_probability_table(series_by_match: dict, wins_by_match: dict) -> dict:
    """{(minute_bin, gold_bin): {"wins": int, "games": int}} from your own
    history. One contribution per game per bucket, so a long game doesn't
    get to vote repeatedly for the same state."""
    table: dict[tuple[int, int], dict] = {}
    for match_id, series in series_by_match.items():
        if match_id not in wins_by_match:
            continue
        won = bool(wins_by_match[match_id])
        seen_buckets = set()
        for minute, diff in series:
            bucket = _wp_bucket(minute, diff)
            if bucket in seen_buckets:
                continue
            seen_buckets.add(bucket)
            entry = table.setdefault(bucket, {"wins": 0, "games": 0})
            entry["games"] += 1
            entry["wins"] += int(won)
    return table


def win_probability_at(table: dict, minute: int, gold_diff: int) -> tuple[float, int]:
    """(probability 0-100, sample size). Returns 50.0 with n=0 when the
    bucket is too thin to say anything — an honest shrug rather than a
    number that looks authoritative and isn't."""
    entry = table.get(_wp_bucket(minute, gold_diff))
    if not entry or entry["games"] < WP_MIN_SAMPLE:
        return 50.0, (entry or {}).get("games", 0)
    return round(entry["wins"] / entry["games"] * 100, 1), entry["games"]


def win_probability_curve(series: list[tuple[int, int]], table: dict) -> pd.DataFrame:
    """Win probability across one game, as a chartable frame."""
    if not series:
        return pd.DataFrame(columns=["minute", "gold_diff", "win_prob", "samples"])
    rows = []
    for minute, diff in series:
        prob, n = win_probability_at(table, minute, diff)
        rows.append({"minute": minute, "gold_diff": diff, "win_prob": prob, "samples": n})
    return pd.DataFrame(rows)


def biggest_swings(curve: pd.DataFrame, top_n: int = 3, window: int = 3) -> list[dict]:
    """The moments that moved the game most — the point of the whole
    exercise, since it turns "review your replay" into "watch these three
    timestamps." Compares win probability across a rolling window and
    returns the largest absolute changes, non-overlapping so three adjacent
    minutes of one teamfight don't fill all the slots."""
    if curve.empty or len(curve) <= window:
        return []
    ordered = curve.sort_values("minute").reset_index(drop=True)
    candidates = []
    for i in range(len(ordered) - window):
        start, end = ordered.iloc[i], ordered.iloc[i + window]
        # Only trust a swing if both ends rest on real samples.
        if start["samples"] == 0 or end["samples"] == 0:
            continue
        delta = end["win_prob"] - start["win_prob"]
        if delta == 0:
            continue
        candidates.append({
            "start_minute": int(start["minute"]),
            "end_minute": int(end["minute"]),
            "delta": round(float(delta), 1),
            "direction": "gain" if delta > 0 else "loss",
        })

    candidates.sort(key=lambda c: abs(c["delta"]), reverse=True)
    chosen: list[dict] = []
    for cand in candidates:
        if any(not (cand["end_minute"] < c["start_minute"] or cand["start_minute"] > c["end_minute"])
               for c in chosen):
            continue  # overlaps one we already took
        chosen.append(cand)
        if len(chosen) >= top_n:
            break
    return chosen


# ==================== Selection bias helpers ====================
# Coachless' central statistical point: "players who build X win 58%" is
# confounded, because you finish X in games you were already winning. The
# honest fixes available to us are (a) say so, and (b) let comparisons be
# restricted to games that were still even at a checkpoint, so the game
# state is roughly controlled for.
EVEN_GOLD_THRESHOLD = 1500  # team gold diff within this = "even"
EVEN_CHECKPOINT_MINUTE = 15

SELECTION_BIAS_NOTE = (
    "Win rates by choice (build, runes, skill order) are affected by selection bias: "
    "you're more likely to complete a given build in games you were already winning, so "
    "part of what these numbers measure is 'was I ahead', not 'is this good'. Treat them "
    "as descriptive, not as proof one option causes wins."
)


def was_even_at(series: list[tuple[int, int]], minute: int = EVEN_CHECKPOINT_MINUTE,
                threshold: int = EVEN_GOLD_THRESHOLD) -> bool | None:
    """Was the game still close at `minute`? None when the game ended before
    the checkpoint (so callers can exclude rather than guess)."""
    if not series:
        return None
    reached = [(m, d) for m, d in series if m <= minute]
    if not reached or max(m for m, _ in reached) < minute - 1:
        return None
    _, diff = min(reached, key=lambda md: abs(md[0] - minute))
    return abs(diff) <= threshold


OBJECTIVE_COLUMNS = ["dragons", "barons", "heralds", "towers"]


def objective_participation_rates(objectives_df: pd.DataFrame) -> dict:
    """Percentage of games where you took part in at least one of each
    objective type. Reads more naturally than the raw per-game average it
    replaced — "I'm there for 62% of dragons" lands better than "1.4 dragons
    per game", which is easy to misread as a count of objectives rather than
    a participation rate."""
    if objectives_df is None or objectives_df.empty:
        return {col: 0.0 for col in OBJECTIVE_COLUMNS}
    return {
        col: round((objectives_df[col] > 0).mean() * 100, 1) if col in objectives_df.columns else 0.0
        for col in OBJECTIVE_COLUMNS
    }


def _scale(value: float, low: float, high: float) -> float:
    """Map a raw stat onto 0-100 against a fixed reference band, clamped."""
    if high <= low:
        return 0.0
    return round(max(0.0, min(1.0, (value - low) / (high - low))) * 100, 1)


PERFORMANCE_DIMENSIONS = [
    "Aggression", "Farming", "Survivability", "Vision",
    "Teamplay", "Objectives", "Consistency", "Versatility",
]


def rift_only(df: pd.DataFrame) -> pd.DataFrame:
    """Games on Summoner's Rift — the only modes the radar's reference bands
    actually describe.

    ARAM and Arena distort nearly every dimension, not just the obvious one:
    ARAM has no recall and permanent teamfighting, so deaths and assists are
    both far higher by design; almost no warding happens, so vision score
    sits near the floor; and kill participation is inflated because everyone
    is in every fight. Scoring those games against Rift bands measures how
    much ARAM you play, not how you play."""
    if df.empty or "queue_category" not in df.columns:
        return df
    return df[~df["queue_category"].isin(LANELESS_QUEUES)]


# CS/min reference bands per role. Farming means something different in
# every position — a support on 1.5 CS/min is farming normally, while a mid
# laner on 1.5 is not — so one shared band made this dimension read
# permanently low for junglers and supports regardless of how they played.
# These are my own approximations of a typical range, not published figures.
FARMING_BANDS = {
    "Top": (4.0, 8.5),
    "Jungle": (3.0, 7.0),      # jungle camps count toward CS
    "Mid": (4.5, 9.0),
    "Bot": (5.0, 9.5),
    "Support": (0.3, 2.5),
}
DEFAULT_FARMING_BAND = (2.0, 9.0)


def _farming_score(df: pd.DataFrame) -> float:
    """CS/min scored against each role's own band, weighted by how many
    games you played in that role — so a jungle/support two-role player gets
    a blended score rather than being judged by whichever came first."""
    if df.empty:
        return 0.0
    if "role_label" not in df.columns:
        return _scale(df["cs_per_min"].mean(), *DEFAULT_FARMING_BAND)

    total, weight = 0.0, 0
    for role, group in df.groupby("role_label"):
        low, high = FARMING_BANDS.get(role, DEFAULT_FARMING_BAND)
        total += _scale(group["cs_per_min"].mean(), low, high) * len(group)
        weight += len(group)
    return round(total / weight, 1) if weight else 0.0


def performance_radar(df: pd.DataFrame, objectives_df: pd.DataFrame | None = None) -> dict:
    """An eight-dimension 0-100 "fingerprint" of how you play, in the spirit
    of Mobalytics' GPI — one glanceable shape instead of twenty scattered
    numbers.

    Important difference worth being upfront about: Mobalytics scores you
    against rank-matched peers using a trained model. There's no way to do
    that from a single account's data, so these are scored against fixed
    reference bands (roughly "typical range for a ranked game"), not against
    other players at your rank. Read it as a shape describing your *style* —
    where you sit relative to the game's normal spread — not as a verdict on
    how you compare to your peers.

    Scope: **Summoner's Rift only**. See `rift_only()` — ARAM and Arena
    distort nearly every dimension here, so they're excluded up front rather
    than patched out dimension by dimension. An all-ARAM history returns all
    zeros, which callers should present as "no Rift games" rather than "you
    scored zero".

    Bands are deliberately wide and documented rather than tuned, since any
    tuning I did would be invented precision.
    """
    empty = {d: 0.0 for d in PERFORMANCE_DIMENSIONS}
    if df.empty:
        return empty

    # One scoping decision, applied to everything, instead of per-dimension
    # special cases that are easy to forget when adding a ninth dimension.
    df = rift_only(df)
    if df.empty:
        return empty

    avg = averages(df)
    vis = vision_summary(df)

    # Aggression: kill participation, plus a nudge from damage share.
    aggression = (
        _scale(avg["kill_participation"], 30, 75) * 0.6
        + _scale(avg["damage_share"], 10, 35) * 0.4
    )
    # Farming: CS/min against each role's own band (see _farming_score).
    farming = _farming_score(df)
    # Survivability: fewer deaths is better, so this band is inverted.
    survivability = 100 - _scale(df["deaths"].mean(), 2, 9)
    vision = _scale(avg["vision_score"], 10, 60) * 0.7 + _scale(vis["wards_placed"], 5, 25) * 0.3
    # Teamplay: assists relative to kills, plus CC contribution.
    assist_share = df["assists"].sum() / max(df["kills"].sum() + df["assists"].sum(), 1) * 100
    teamplay = _scale(assist_share, 30, 75) * 0.7 + _scale(avg["cc_score"], 5, 60) * 0.3

    # Objectives: real participation counts when a timeline load has happened,
    # otherwise fall back to the team-objective flags that are always present.
    # No laneless-mode guard needed any more — `df` is already Rift-only.
    if objectives_df is not None and not objectives_df.empty:
        per_game = objectives_df[["dragons", "barons", "heralds", "towers"]].sum(axis=1).mean()
        objectives = _scale(per_game, 0.5, 6)
    else:
        flags = [c for c in ["team_first_dragon", "team_first_baron", "team_first_tower"]
                 if c in df.columns]
        objectives = _scale(df[flags].mean().mean() * 100, 25, 75) if flags else 0.0

    # Consistency: how tightly your KDA clusters. A low spread relative to
    # your own average scores high; this one is genuinely self-relative.
    mean_kda = df["kda"].mean()
    consistency = 100 - _scale(df["kda"].std() / max(mean_kda, 0.1), 0.3, 1.5) if len(df) > 1 else 50.0

    # Versatility: breadth of champion pool and roles actually played.
    champ_count = df["champion"].nunique()
    if "role_label" in df.columns:
        role_count = df.loc[df["role_label"].isin(LANE_ROLES), "role_label"].nunique()
    else:
        role_count = 1
    versatility = _scale(champ_count, 1, 15) * 0.6 + _scale(role_count, 1, 5) * 0.4

    return {
        "Aggression": round(aggression, 1),
        "Farming": round(farming, 1),
        "Survivability": round(survivability, 1),
        "Vision": round(vision, 1),
        "Teamplay": round(teamplay, 1),
        "Objectives": round(objectives, 1),
        "Consistency": round(max(0.0, min(100.0, consistency)), 1),
        "Versatility": round(versatility, 1),
    }


# What each radar dimension actually means in practice, and one concrete
# thing to try. Deliberately phrased as things to pay attention to rather
# than promises — no dashboard can know why you're losing, and a personal
# stats tool claiming otherwise would be overselling.
FOCUS_GUIDANCE = {
    "Aggression": (
        "You're involved in fewer of your team's kills than most of your other stats suggest.",
        "Try following up when a teammate engages nearby, rather than farming through the fight.",
    ),
    "Farming": (
        "Your CS per minute is the lowest-scoring part of your profile.",
        "Pick one game and focus only on last-hitting through the first 10 minutes — nothing else.",
    ),
    "Survivability": (
        "You're dying more often than the rest of your profile would predict.",
        "Before stepping forward, check whether the enemy jungler has been seen in the last 15 seconds.",
    ),
    "Vision": (
        "Vision score and wards placed are your weakest area.",
        "Buy a control ward every single back for a few games and see if it moves your win rate.",
    ),
    "Teamplay": (
        "Your assists and crowd control contribution are low relative to your kills.",
        "Look for chances to group with your team in the mid game instead of taking side lanes alone.",
    ),
    "Objectives": (
        "You're present for fewer dragons, barons and towers than the rest of your play suggests.",
        "Start moving toward the next objective about 30 seconds before it spawns.",
    ),
    "Consistency": (
        "Your game-to-game results swing more widely than anything else in your profile.",
        "Consistency usually improves by playing fewer champions, not by playing better.",
    ),
    "Versatility": (
        "You play a narrow pool. That's not automatically bad — one-tricking is a legitimate strategy.",
        "Only worth changing if you're getting banned out or counterpicked often.",
    ),
}


def primary_focus(scores: dict, df: pd.DataFrame | None = None) -> dict | None:
    """Pick ONE thing to work on, in the spirit of concept-at-a-time coaching:
    a single focus beats a wall of equally-weighted tips you'll skim past.

    Chooses the lowest-scoring radar dimension. Returns None when there's no
    meaningful spread between dimensions — if everything's within a few
    points, singling one out would be manufacturing a signal out of noise.
    Versatility is skipped as a focus unless it's dramatically low, since a
    narrow champion pool is a valid choice rather than a weakness."""
    if not scores or all(v == 0 for v in scores.values()):
        return None

    ranked = sorted(scores.items(), key=lambda kv: kv[1])
    spread = ranked[-1][1] - ranked[0][1]
    if spread < 15:
        return None  # too flat to call anything a weak point

    for name, value in ranked:
        if name == "Versatility" and value > 20:
            continue  # narrow pool is a choice, not a flaw
        guidance = FOCUS_GUIDANCE.get(name)
        if not guidance:
            continue
        return {
            "dimension": name,
            "score": value,
            "observation": guidance[0],
            "suggestion": guidance[1],
            "caveat": guidance[2] if len(guidance) > 2 else None,
            "games": int(len(df)) if df is not None else None,
        }
    return None


def radar_highlights(scores: dict) -> tuple[str | None, str | None]:
    """Highest and lowest scoring dimension, for a one-line plain-language
    summary under the chart."""
    if not scores or all(v == 0 for v in scores.values()):
        return None, None
    ranked = sorted(scores.items(), key=lambda kv: kv[1])
    return ranked[-1][0], ranked[0][0]


def champion_pool_concentration(df: pd.DataFrame, top_n: int = 3) -> dict:
    """How concentrated your champion pool is — unique champions played and
    what share of games your top N account for. A rough one-tricking vs.
    spreading-thin read; deliberately not framed as good or bad, since both
    are legitimate approaches depending on role and goals."""
    if df.empty or "champion" not in df.columns:
        return {"unique_champions": 0, "top_n": top_n, "top_n_share": 0.0, "total_games": 0}
    counts = df["champion"].value_counts()
    total = int(counts.sum())
    top_share = round(counts.head(top_n).sum() / total * 100, 1) if total else 0.0
    return {
        "unique_champions": int(counts.size),
        "top_n": top_n,
        "top_n_share": top_share,
        "total_games": total,
    }


def _patch_sort_key(patch: str) -> tuple:
    try:
        parts = patch.split(".")
        return (int(parts[0]), int(parts[1]))
    except Exception:
        return (0, 0)


def win_rate_by_patch(df: pd.DataFrame, min_games: int = 1) -> pd.DataFrame:
    """Win rate grouped by patch, sorted chronologically (not alphabetically —
    "14.9" needs to sort before "14.15")."""
    result = win_rate_by(df, "patch", min_games=min_games)
    if result.empty:
        return result
    result["_sort"] = result["patch"].map(_patch_sort_key)
    return result.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)


def best_matchup_overall(df: pd.DataFrame, min_games: int = 3) -> dict | None:
    """Highest win-rate champion-vs-opponent pairing across the whole
    dataset (not scoped to one champion) — used in the season recap."""
    subset = df.dropna(subset=["opponent_champion"])
    if subset.empty:
        return None
    grouped = (
        subset.groupby(["champion", "opponent_champion"])["win"]
        .agg(games="count", wins="sum")
        .reset_index()
    )
    grouped["win_rate"] = (grouped["wins"] / grouped["games"] * 100).round(1)
    grouped = grouped[grouped["games"] >= min_games]
    if grouped.empty:
        return None
    best = grouped.sort_values(["win_rate", "games"], ascending=[False, False]).iloc[0]
    return {
        "champion": best["champion"],
        "opponent": best["opponent_champion"],
        "win_rate": float(best["win_rate"]),
        "games": int(best["games"]),
    }


def gold_diff_summary(diff_df: pd.DataFrame) -> pd.DataFrame:
    if diff_df.empty:
        return pd.DataFrame(columns=["minute", "avg_diff"])
    rows = []
    for m in GOLD_CHECKPOINTS:
        col = f"diff_{m}"
        if col not in diff_df:
            continue
        rows.append({
            "minute": m,
            "avg_diff": round(diff_df[col].mean(), 0) if diff_df[col].notna().any() else None,
        })
    return pd.DataFrame(rows)


# ==================== Death/kill position heatmap ====================
# Riot's timeline API includes `position` (x, y on the map) on CHAMPION_KILL
# events, so deaths and kills can be plotted. WARD_PLACED events do NOT
# include position — Riot has explicitly declined to expose ward locations
# for privacy/competitive-integrity reasons — so a ward heatmap isn't
# possible from public API data. Map coordinates run roughly 0-14870 (x) by
# 0-14980 (y), origin at the bottom-left (blue side base).
MAP_SIZE = 14980  # both axes are close enough to square to share one constant

def death_kill_positions(timelines: dict, puuid: str) -> pd.DataFrame:
    """Map (x, y) for every death and kill this player was involved in,
    across a set of {match_id: timeline}. `kind` is "death" (this player was
    the victim) or "kill" (this player was the killer — assists aren't
    positional in the same way, so they're not included)."""
    rows = []
    for match_id, tl in timelines.items():
        participant_id = find_participant_id(tl, puuid)
        if participant_id is None:
            continue
        for frame in tl.get("info", {}).get("frames", []):
            for event in frame.get("events", []):
                if event.get("type") != "CHAMPION_KILL":
                    continue
                position = event.get("position")
                if not position:
                    continue
                if event.get("victimId") == participant_id:
                    kind = "death"
                elif event.get("killerId") == participant_id:
                    kind = "kill"
                else:
                    continue
                rows.append({
                    "match_id": match_id,
                    "x": position.get("x"),
                    "y": position.get("y"),
                    "kind": kind,
                })
    return pd.DataFrame(rows, columns=["match_id", "x", "y", "kind"])


# ==================== Highlight reel ====================

def standout_games(df: pd.DataFrame, n: int = 6) -> list[dict]:
    """Auto-flagged best/worst games by a handful of simple superlatives —
    not a ranking of "the" best game, just a few different lenses on what
    stands out, deduped so the same game isn't listed twice under two labels."""
    if df.empty:
        return []

    d = df.copy()
    d["kill_diff"] = d["kills"] - d["deaths"]
    candidates = []

    def add(subset: pd.DataFrame, ascending: bool, sort_col: str, label: str, tone: str):
        if subset.empty:
            return
        s = subset.sort_values(sort_col, ascending=ascending)
        if s.empty:
            return
        row = s.iloc[0]
        candidates.append({"match_id": row["match_id"], "label": label, "tone": tone, "row": row})

    if "penta_kills" in d.columns and (d["penta_kills"] > 0).any():
        add(d[d["penta_kills"] > 0], False, "penta_kills", "Penta Kill!", "positive")
    elif "quadra_kills" in d.columns and (d["quadra_kills"] > 0).any():
        add(d[d["quadra_kills"] > 0], False, "quadra_kills", "Quadra Kill", "positive")
    add(d[d["win"]], False, "kda", "Best KDA (win)", "positive")
    add(d[d["win"]], False, "kill_diff", "Biggest kill lead", "positive")
    add(d[d["win"]], False, "damage_share", "Highest damage share (win)", "positive")
    add(d, False, "kill_participation", "Highest kill participation", "positive")
    add(d[~d["win"]], True, "kda", "Roughest game (loss)", "warning")
    add(d[~d["win"]], True, "kill_diff", "Biggest deficit (loss)", "warning")

    seen_match_ids = set()
    deduped = []
    for c in candidates:
        if c["match_id"] in seen_match_ids:
            continue
        seen_match_ids.add(c["match_id"])
        deduped.append(c)
        if len(deduped) >= n:
            break
    return deduped


def multikill_summary(df: pd.DataFrame) -> dict:
    """Total double/triple/quadra/penta kills across the given games — these
    fields exist on every match-v5 participant already but nothing in this
    dashboard surfaced them until now."""
    cols = ["double_kills", "triple_kills", "quadra_kills", "penta_kills"]
    labels = ["Double Kills", "Triple Kills", "Quadra Kills", "Penta Kills"]
    return {
        label: int(df[col].sum()) if col in df.columns and not df.empty else 0
        for col, label in zip(cols, labels)
    }


def nemesis_and_free_win(df: pd.DataFrame, min_games: int = 3) -> tuple[dict | None, dict | None]:
    """Headline stat: the lane opponent champion you lose to most (Nemesis)
    and beat most (Free Win), at a minimum sample size. Reuses the existing
    `opponent_champion` column (same-role-opposite-team heuristic, already
    computed per game) and the generic `win_rate_by` grouping — no new
    tracking, just a different lens on data already collected."""
    if "opponent_champion" not in df.columns:
        return None, None
    wr = win_rate_by(df.dropna(subset=["opponent_champion"]), "opponent_champion", min_games=min_games)
    if wr.empty:
        return None, None
    nemesis = wr.sort_values("win_rate", ascending=True).iloc[0].to_dict()
    free_win = wr.sort_values("win_rate", ascending=False).iloc[0].to_dict()
    return nemesis, free_win


def teammate_synergy(df: pd.DataFrame, min_games: int = 3) -> pd.DataFrame:
    """Win rate with any frequently-recurring teammate — any role, not just
    the ADC+support bot-lane pairing `duo_partner` already covers. Explodes
    each game's full teammate list (captured per-game in `parse_match`,
    already free — no extra API calls) into one row per (game, teammate)
    pair, then groups by teammate puuid so name changes don't split the
    same person into two rows. Still a heuristic stand-in for real premade-
    party data, since Riot's public API doesn't expose that."""
    if df.empty or "teammate_puuids" not in df.columns:
        return pd.DataFrame(columns=["teammate", "games", "wins", "win_rate"])

    rows = []
    for _, row in df.iterrows():
        puuids = row.get("teammate_puuids") or ()
        names = row.get("teammate_names") or ()
        for puuid_, name in zip(puuids, names):
            rows.append({"puuid": puuid_, "name": name, "win": bool(row["win"])})

    if not rows:
        return pd.DataFrame(columns=["teammate", "games", "wins", "win_rate"])

    exploded = pd.DataFrame(rows)
    grouped = (
        exploded.groupby("puuid")
        .agg(teammate=("name", "last"), games=("win", "size"), wins=("win", "sum"))
        .reset_index(drop=True)
    )
    grouped = grouped[grouped["games"] >= min_games].copy()
    grouped["win_rate"] = (grouped["wins"] / grouped["games"] * 100).round(1)
    return grouped.sort_values(["games", "win_rate"], ascending=[False, False]).reset_index(drop=True)
