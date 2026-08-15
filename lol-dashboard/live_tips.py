"""
Advice for the game you're in right now, computed from your own match history.

Two halves: working out who's playing what role (spectator-v5 doesn't tell
you), and turning that into tips grounded in games you've actually played.

**What spectator-v5 gives us.** Per participant: puuid, teamId, championId,
spell1Id/spell2Id, perks. There is no position field — Riot simply doesn't
publish one for live games. So roles here are *inferred*, and every function
below reports how confident that inference is rather than presenting a guess
as fact. The UI shows the confidence and lets you override it.

**The one reliable signal is Smite.** Checked against 599 of Bendy's own
ranked/normal games: 397 games with Smite, 397 of them jungle; 202 games
without Smite, none of them jungle. Everything else is genuinely ambiguous —
Teleport, for instance, split 14 mid / 8 top in the same history.

**Sample sizes here are small and that's the hard part.** "You're 2-1 against
Ahri" is a coin flip wearing a statistic's clothes. Every tip carries its
game count and the half-width of its 95% Wilson interval, and sets `weak`
when the comparison can't be separated from noise — the same machinery the
patterns card uses, for the same reason.

**What this deliberately doesn't do.** It won't tell you how to play the
matchup; it has no model of champions, only of your results. A tip saying
you're 30% into Vi as jungle Shaco is a fact about your history, not a
diagnosis, and certainly not a reason to dodge.
"""
import pandas as pd

from stats import (
    LANE_ROLES,
    ROLE_ORDER,
    separated,
    split_record as _split,
    wilson_margin,
)

# The five actual positions, in lane order. `ROLE_ORDER` is not the right
# list here: it also contains "ARAM", "Arena" and "Unknown", which are
# role *labels* for modes without lanes. Using it meant two live bugs —
# priors learned "Sona: ARAM 1" as if ARAM were a position, and the
# leftover-assignment step could hand a live player the role "ARAM".
LANE_ORDER = [r for r in ROLE_ORDER if r in LANE_ROLES]

# Summoner spell id 11. Verified against the user's own history rather than
# assumed: Smite appeared in 397 games, every one of them jungle.
SMITE_ID = 11

# A champion needs to have been seen in a role this many times before that
# counts as evidence rather than an accident of one game.
MIN_PRIOR_OBSERVATIONS = 3

# Below this many games, a matchup or comp record is reported but flagged.
MIN_GAMES_FOR_TIP = 3

CONFIDENCE_CERTAIN = "certain"
CONFIDENCE_LIKELY = "likely"
CONFIDENCE_GUESS = "guess"


def champion_role_priors(df: pd.DataFrame) -> dict:
    """champion -> {role: times seen} learned from your own history.

    Two sources, both already in the data:

      * your games — your champion in your role;
      * your lane opponents — by definition they were in *your* role, which
        is what makes `opponent_champion` usable as a role observation for
        champions you've never played.

    That second source matters a lot: it's the only reason this knows
    anything about the 150-odd champions you don't play.
    """
    priors: dict = {}

    def record(champ, role):
        if not champ or not role or role not in LANE_ROLES:
            return
        priors.setdefault(champ, {}).setdefault(role, 0)
        priors[champ][role] += 1

    if df.empty:
        return priors
    for champ, role in zip(df.get("champion", []), df.get("role_label", [])):
        record(champ, role)
    if "opponent_champion" in df.columns:
        for champ, role in zip(df["opponent_champion"], df["role_label"]):
            record(champ, role)
    return priors


def _best_roles(champion: str, priors: dict) -> list:
    """Roles for a champion, most-observed first, above the noise floor."""
    seen = priors.get(champion, {})
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], LANE_ORDER.index(kv[0])
                                                  if kv[0] in LANE_ORDER else 99))
    return [role for role, count in ranked if count >= MIN_PRIOR_OBSERVATIONS]


def infer_roles(team: list, priors: dict) -> dict:
    """Assign roles to one team of five.

    `team` is a list of dicts with `puuid`, `champion` and `spells` (an
    iterable of summoner spell ids). Returns puuid -> (role, confidence).

    The algorithm is deliberately simple, because a clever one would be
    confidently wrong more often:

      1. Anyone with Smite is the jungler. Certain.
      2. Everyone else is assigned greedily by how strongly your history
         associates their champion with a still-unclaimed role.
      3. Whatever's left over gets the leftover roles, marked as a guess.

    Roles are assigned uniquely — a team has one of each — so a strong prior
    that's already taken falls through to the next-best option.
    """
    assignments: dict = {}
    remaining = list(LANE_ORDER)
    unassigned = []

    for player in team:
        if SMITE_ID in set(player.get("spells") or ()):
            if "Jungle" in remaining:
                remaining.remove("Jungle")
            assignments[player["puuid"]] = ("Jungle", CONFIDENCE_CERTAIN)
        else:
            unassigned.append(player)

    # Strongest evidence first, so a champion you've seen 40 times in a role
    # claims it before one you've seen 3 times.
    def strength(player):
        seen = priors.get(player.get("champion"), {})
        return -max(seen.values(), default=0)

    for player in sorted(unassigned, key=strength):
        pick = next((r for r in _best_roles(player.get("champion"), priors)
                     if r in remaining), None)
        if pick:
            remaining.remove(pick)
            assignments[player["puuid"]] = (pick, CONFIDENCE_LIKELY)

    for player in unassigned:
        if player["puuid"] not in assignments:
            pick = remaining.pop(0) if remaining else None
            assignments[player["puuid"]] = (pick, CONFIDENCE_GUESS)
    return assignments


def _tip(tone, text, games, wins, baseline=None, note=""):
    """One live tip, carrying the sample it rests on.

    `baseline` is the complement — see `_split`. A 55% win rate on a champion
    means nothing if you're 54% across everything else.
    """
    weak = True
    if baseline and games:
        base_games, base_wins = baseline
        weak = not separated(wins, games, base_wins, base_games)
    return {
        "tone": tone,
        "text": text,
        "games": int(games),
        "margin": wilson_margin(int(wins), int(games)) if games else 100.0,
        "weak": weak,
        "note": note,
    }


def champion_tip(df, champion, role=None, baseline=None):
    """Your record on the champion you've locked in, scoped to the role."""
    if df.empty or not champion:
        return None
    mask = df["champion"] == champion
    scope = "overall"
    if role and "role_label" in df.columns:
        role_mask = mask & (df["role_label"] == role)
        # Only narrow to the role if doing so leaves a usable sample —
        # otherwise the role scoping trades a real number for a noisy one.
        if int(role_mask.sum()) >= MIN_GAMES_FOR_TIP:
            mask, scope = role_mask, role.lower()
    (games, wins), rest = _split(df, mask)
    if not games:
        return _tip("neutral", f"No history on {champion} yet — this is new ground.", 0, 0)
    rate = wins / games * 100
    tone = "positive" if rate >= 55 else "warning" if rate <= 45 else "neutral"
    return _tip(
        tone,
        f"{champion} {scope}: {rate:.0f}% over {games} games ({wins}W {games - wins}L).",
        games, wins, rest,
    )


def matchup_tip(df, champion, opponent, role=None, baseline=None):
    """Your record on `champion` against `opponent` in lane."""
    if df.empty or not champion or not opponent:
        return None
    if "opponent_champion" not in df.columns:
        return None
    mask = (df["champion"] == champion) & (df["opponent_champion"] == opponent)
    if role and "role_label" in df.columns:
        role_mask = mask & (df["role_label"] == role)
        if int(role_mask.sum()) >= MIN_GAMES_FOR_TIP:
            mask = role_mask
    (games, wins), rest = _split(df, mask)
    if games < MIN_GAMES_FOR_TIP:
        return _tip(
            "neutral",
            f"You've played {champion} into {opponent} {games} time"
            f"{'' if games == 1 else 's'} — not enough to say anything.",
            games, wins,
        )
    rate = wins / games * 100
    tone = "positive" if rate >= 60 else "warning" if rate <= 40 else "neutral"
    return _tip(
        tone,
        f"{champion} into {opponent}: {rate:.0f}% over {games} games.",
        games, wins, rest,
    )


def _team_champion_records(df, column, champions, baseline=None):
    """Your record in games where each champion appeared in `column`.

    `baseline` is ignored; each champion is compared against the games it
    *wasn't* in. Passing one global baseline would reintroduce the
    subset-inside-superset problem `_split` exists to avoid.
    """
    out = []
    if df.empty or column not in df.columns:
        return out
    for champ in champions:
        mask = df[column].map(lambda comp, c=champ: c in (comp or ()))
        (games, wins), rest = _split(df, mask)
        if games < MIN_GAMES_FOR_TIP:
            continue
        base_games, base_wins = rest
        out.append({
            "champion": champ,
            "games": games,
            "wins": wins,
            "win_rate": wins / games * 100,
            "weak": not separated(wins, games, base_wins, base_games,
                                  comparisons=max(1, len(champions))),
        })
    return sorted(out, key=lambda r: r["win_rate"])


def enemy_tips(df, enemy_champions, baseline, limit=2):
    """Enemies whose presence lines up with unusually bad or good results.

    Heavily confounded and labelled as such in the UI: a champion being on
    the enemy team says nothing about whether *they* were your problem. It's
    a prompt to pay attention, not an explanation.
    """
    records = _team_champion_records(df, "enemy_champions", enemy_champions, baseline)
    strong = [r for r in records if not r["weak"]]
    tips = []
    for row in strong[:limit]:
        tips.append(_tip(
            "warning",
            f"You're {row['win_rate']:.0f}% in {row['games']} games with "
            f"{row['champion']} on the enemy team.",
            row["games"], row["wins"], baseline,
            note="Correlation only — they weren't necessarily the problem.",
        ))
    for row in reversed(strong[-limit:]):
        if row["win_rate"] > 50 and row not in strong[:limit]:
            tips.append(_tip(
                "positive",
                f"You're {row['win_rate']:.0f}% in {row['games']} games with "
                f"{row['champion']} on the enemy team.",
                row["games"], row["wins"], baseline,
                note="Correlation only — they weren't necessarily why.",
            ))
    return tips


def ally_tips(df, ally_champions, baseline, limit=1):
    """Same idea for your own team."""
    records = _team_champion_records(df, "ally_champions", ally_champions, baseline)
    strong = [r for r in records if not r["weak"]]
    tips = []
    for row in strong[-limit:]:
        if row["win_rate"] > 50:
            tips.append(_tip(
                "positive",
                f"{row['champion']} on your team has gone well — "
                f"{row['win_rate']:.0f}% over {row['games']} games.",
                row["games"], row["wins"], baseline,
                note="Mostly says the champion is strong, not that you synergise.",
            ))
    for row in strong[:limit]:
        if row["win_rate"] < 50:
            tips.append(_tip(
                "warning",
                f"{row['champion']} on your team: {row['win_rate']:.0f}% "
                f"over {row['games']} games.",
                row["games"], row["wins"], baseline,
                note="Mostly says the champion is weak, not that you clash.",
            ))
    return tips


def build_live_tips(df, my_champion, my_role, opponent, ally_champions,
                    enemy_champions):
    """Everything the live card shows, in priority order.

    Ordered most-specific first: your champion, then the lane matchup, then
    the diffuse team-level correlations. That ordering is the advice — the
    first two are about a fight you're actually in.
    """
    tips = []
    if df is None or df.empty:
        return tips
    baseline = (len(df), int(df["win"].sum()))

    for tip in (champion_tip(df, my_champion, my_role, baseline),
                matchup_tip(df, my_champion, opponent, my_role, baseline)):
        if tip:
            tips.append(tip)

    tips.extend(enemy_tips(df, enemy_champions or (), baseline))
    tips.extend(ally_tips(df, ally_champions or (), baseline))
    return tips
