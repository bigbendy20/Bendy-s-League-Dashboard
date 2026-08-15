"""
Rule-based "recommendations" — synthesizes stats already computed elsewhere
into plain-language observations. Nothing here is a new data source; it's
just thresholded comparisons over existing win-rate breakdowns.

Every tip carries the sample it came from (`games`) and the half-width of its
95% Wilson interval (`margin`), plus a `weak` flag set when the comparison
that produced it isn't actually separable from noise. That's not decoration:
day-of-week and hour-of-day buckets routinely hold 25-40 games, where a
+/-15 point margin is normal and "your best day" is often a coin flip. The UI
shows weak tips greyed out and labelled rather than hiding them, so the
distinction stays visible instead of being quietly decided here.

Two honesty caveats that no amount of arithmetic fixes, both surfaced in the
UI rather than buried:

  * Multiple comparisons. Scanning 7 weekdays and 24 hours and reporting the
    extreme means the winner is selected partly *for* its noise, so the
    interval on the max is optimistic by construction.
  * Correlation, not cause. Friday nights may be worse because of when you
    play, who you play with, or how tired you are — the data can't separate
    those, and none of it is an instruction.
"""
import pandas as pd

from stats import (
    performance_by_hour,
    performance_by_weekday,
    hour_label,
    separated,
    split_record,
    wilson_margin,
    win_rate_after_result,
    win_rate_by,
    win_rate_by_patch,
)

MIN_GAMES_FOR_SIGNAL = 5

# Bucket scans (hour of day, day of week, champion) need a bigger floor than
# a straight two-way comparison. Naming "your best hour" off five games is
# how the card ended up claiming 83% vs 20% on six games — with 24 candidate
# hours, some bucket is always extreme. Twelve is still small; it's set where
# a real pattern can survive the correction and pure noise usually can't.
MIN_GAMES_FOR_SCAN = 12

MEANINGFUL_GAP_PP = 10  # percentage points


def _tip(tone: str, text: str, games: int, wins: float, weak: bool = False) -> dict:
    """One recommendation, with the sample it rests on attached."""
    return {
        "tone": tone,
        "text": text,
        "games": int(games),
        "margin": wilson_margin(int(round(wins)), int(games)),
        "weak": weak,
    }


def generate_recommendations(df: pd.DataFrame) -> list[dict]:
    """Returns a list of tips: tone, text, games, margin, weak."""
    tips = []
    if df.empty or len(df) < MIN_GAMES_FOR_SIGNAL:
        return tips

    d = df.sort_values("game_creation")
    total_games = len(d)
    total_wins = d["win"].sum()
    alltime_wr = d["win"].mean() * 100

    # Recent form, compared against everything *before* it rather than
    # against the whole history. The old version compared the last 10 games
    # to all games — a subset against a superset containing it, which drags
    # the baseline toward the sample and hides real swings. Same bug the live
    # tips had; it was fixed there first and left here, which is exactly the
    # kind of thing a second copy of a comparison is for.
    recent = d.tail(10)
    if len(recent) >= MIN_GAMES_FOR_SIGNAL:
        recent_wins = recent["win"].sum()
        recent_wr = recent["win"].mean() * 100
        is_recent = d.index.isin(recent.index)
        (r_games, r_wins), (prior_games, prior_wins) = split_record(d, is_recent)
        weak = not separated(r_wins, r_games, prior_wins, prior_games)
        if recent_wr <= alltime_wr - MEANINGFUL_GAP_PP:
            tips.append(_tip(
                "warning",
                f"Your last {len(recent)} games are at {recent_wr:.0f}% win rate, "
                f"below your {alltime_wr:.0f}% overall average.",
                len(recent), recent_wins, weak,
            ))
        elif recent_wr >= alltime_wr + MEANINGFUL_GAP_PP:
            tips.append(_tip(
                "positive",
                f"You're running hot — {recent_wr:.0f}% over your last {len(recent)} games "
                f"vs a {alltime_wr:.0f}% overall average.",
                len(recent), recent_wins, weak,
            ))

    # Post-loss tilt
    after = win_rate_after_result(d)
    if len(after) == 2:
        by_label = after.set_index("prev_result")
        if "After a Loss" in by_label.index and "After a Win" in by_label.index:
            row_loss = by_label.loc["After a Loss"]
            row_win = by_label.loc["After a Win"]
            gap = row_win["win_rate"] - row_loss["win_rate"]
            if row_loss["games"] >= MIN_GAMES_FOR_SIGNAL and gap >= MEANINGFUL_GAP_PP:
                tips.append(_tip(
                    "warning",
                    f"You win {row_loss['win_rate']:.0f}% of games right after a loss vs "
                    f"{row_win['win_rate']:.0f}% after a win — a possible tilt pattern.",
                    row_loss["games"], row_loss["wins"],
                    weak=not separated(
                        row_loss["wins"], row_loss["games"],
                        row_win["wins"], row_win["games"],
                    ),
                ))

    # Time of day. Local wall-clock — see stats.to_local_time; these were
    # reported in UTC until the timestamps were fixed.
    hourly = performance_by_hour(d)
    # `searched` is every bucket the max was chosen from, including the ones
    # dropped for being too small. Correcting by the survivor count instead
    # understates the selection effect badly — that was the bug that let the
    # six-game "best hour" through.
    hour_searched = len(hourly)
    hourly = hourly[hourly["games"] >= MIN_GAMES_FOR_SCAN]
    if len(hourly) >= 2:
        best = hourly.loc[hourly["win_rate"].idxmax()]
        worst = hourly.loc[hourly["win_rate"].idxmin()]
        if best["win_rate"] - worst["win_rate"] >= 15:
            tips.append(_tip(
                "neutral",
                f"You win {best['win_rate']:.0f}% of games starting around "
                f"{hour_label(best['hour'])} vs {worst['win_rate']:.0f}% around "
                f"{hour_label(worst['hour'])}.",
                best["games"], best["wins"],
                weak=not separated(
                    best["wins"], best["games"], worst["wins"], worst["games"],
                    comparisons=hour_searched,
                ),
            ))

    # Day of week
    weekday = performance_by_weekday(d)
    weekday_searched = len(weekday)
    weekday = weekday[weekday["games"] >= MIN_GAMES_FOR_SCAN]
    if len(weekday) >= 2:
        best = weekday.loc[weekday["win_rate"].idxmax()]
        worst = weekday.loc[weekday["win_rate"].idxmin()]
        if best["win_rate"] - worst["win_rate"] >= 15:
            tips.append(_tip(
                "neutral",
                f"{best['weekday']}s are your best day ({best['win_rate']:.0f}%), "
                f"{worst['weekday']}s your worst ({worst['win_rate']:.0f}%).",
                best["games"], best["wins"],
                weak=not separated(
                    best["wins"], best["games"], worst["wins"], worst["games"],
                    comparisons=weekday_searched,
                ),
            ))

    # Patch trend
    patches = win_rate_by_patch(d, min_games=3)
    if len(patches) >= 2:
        current, previous = patches.iloc[-1], patches.iloc[-2]
        weak = not separated(
            current["wins"], current["games"], previous["wins"], previous["games"],
        )
        if current["win_rate"] <= previous["win_rate"] - MEANINGFUL_GAP_PP:
            tips.append(_tip(
                "warning",
                f"Win rate dropped from {previous['win_rate']:.0f}% on patch "
                f"{previous['patch']} to {current['win_rate']:.0f}% on patch "
                f"{current['patch']}.",
                current["games"], current["wins"], weak,
            ))
        elif current["win_rate"] >= previous["win_rate"] + MEANINGFUL_GAP_PP:
            tips.append(_tip(
                "positive",
                f"Win rate is up this patch ({current['patch']}: "
                f"{current['win_rate']:.0f}%) vs last ({previous['patch']}: "
                f"{previous['win_rate']:.0f}%).",
                current["games"], current["wins"], weak,
            ))

    # Standout champion
    champs = win_rate_by(d, "champion", min_games=MIN_GAMES_FOR_SCAN)
    if not champs.empty:
        best_champ = champs.sort_values("win_rate", ascending=False).iloc[0]
        if best_champ["win_rate"] >= alltime_wr + 15:
            # Compared against your games on *other* champions, not against
            # everything including this champion.
            others_games = total_games - int(best_champ["games"])
            others_wins = int(total_wins) - int(best_champ["wins"])
            tips.append(_tip(
                "positive",
                f"{best_champ['champion']} is carrying you — "
                f"{best_champ['win_rate']:.0f}% win rate over "
                f"{int(best_champ['games'])} games, above your average on "
                f"everything else.",
                best_champ["games"], best_champ["wins"],
                weak=not separated(
                    best_champ["wins"], best_champ["games"], others_wins, others_games,
                    comparisons=len(champs),
                ),
            ))

    if not tips:
        tips.append(_tip(
            "neutral",
            "No strong patterns yet — keep playing and check back as more games come in.",
            total_games, total_wins,
        ))

    return tips
