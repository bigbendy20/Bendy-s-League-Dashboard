"""
Tilt patterns.

Every function here compares two sets of the same player's games, so the
tests are mostly about the *shape* of that comparison rather than arithmetic:
that the baseline is present, that the split lands where it should, and that
missing inputs produce an empty result rather than a confident wrong one.

One of these is written from a real failure. `quick_requeue_effect` first
read a `game_duration` column that doesn't exist — the parsed row calls it
`game_duration_min`, in minutes — and the guard for a missing column returned
zeroes. Against 1,546 real games the card said "no data". A fixture built
from `make_match` has the right column name, which is why these use it rather
than hand-written frames.
"""
import datetime

import pandas as pd

import stats
from conftest import make_match


def frame(results, gaps_min=None, durations_min=None, start_hour=12):
    """A chronological frame of games with controllable spacing.

    `results` is a list of wins/losses; `gaps_min[i]` is the minutes between
    the *end* of game i and the start of game i+1.
    """
    gaps_min = gaps_min or [30] * (len(results) - 1)
    durations_min = durations_min or [30] * len(results)
    rows, when = [], datetime.datetime(2026, 3, 1, start_hour, 0)
    for i, win in enumerate(results):
        match = make_match(match_id=f"NA1_{i}", puuid="p1", win=win,
                           creation_ms=int(when.timestamp() * 1000))
        row = stats.parse_match(match, "p1")
        row["game_duration_min"] = durations_min[i]
        rows.append(row)
        if i < len(results) - 1:
            when += datetime.timedelta(minutes=durations_min[i] + gaps_min[i])
    return pd.DataFrame(rows)


W, L = True, False


class TestLosingStreakEffect:
    def test_the_baseline_row_is_present(self):
        """Without "after a win" the streak rows can't be interpreted at all."""
        out = stats.losing_streak_effect(frame([W, L, W, L, W, W, L, W]))
        assert "After a win" in set(out["after"])

    def test_games_are_attributed_to_the_streak_that_preceded_them(self):
        # W L L W  ->  game 2 follows 1 loss, game 3 follows 2 losses.
        out = stats.losing_streak_effect(frame([W, L, L, W]), max_streak=3)
        by = {r["after"]: r for _, r in out.iterrows()}
        assert by["After 1 loss"]["games"] == 1
        assert by["After 2 losses"]["games"] == 1

    def test_a_win_resets_the_streak(self):
        """The distinguishing case. Counting losses cumulatively instead of
        consecutively puts the last game in a 3+ bucket it doesn't belong in."""
        out = stats.losing_streak_effect(frame([L, L, W, L, W]), max_streak=3)
        assert "After 3+ losses" not in set(out["after"])

    def test_the_top_bucket_is_inclusive(self):
        out = stats.losing_streak_effect(frame([L, L, L, L, W]), max_streak=3)
        by = {r["after"]: r for _, r in out.iterrows()}
        assert by["After 3+ losses"]["games"] == 2      # after 3 and after 4

    def test_too_few_games_is_empty_not_an_error(self):
        assert stats.losing_streak_effect(frame([W])).empty
        assert stats.losing_streak_effect(pd.DataFrame()).empty


class TestSessions:
    def test_a_long_gap_starts_a_new_session(self):
        d = stats.label_sessions(frame([W, W, W], gaps_min=[10, 300]))
        assert list(d["game_in_session"]) == [1, 2, 1]
        assert d["session"].nunique() == 2

    def test_games_close_together_stay_in_one_session(self):
        d = stats.label_sessions(frame([W, W, W, W], gaps_min=[10, 15, 20]))
        assert list(d["game_in_session"]) == [1, 2, 3, 4]

    def test_the_gap_is_measured_between_starts_not_across_a_missing_duration(self):
        """`game_duration` is absent on remakes. Measuring start-to-start means
        one bad row can't silently split a session."""
        d = stats.label_sessions(frame([W, W], gaps_min=[10], durations_min=[30, 30]))
        assert d["session"].nunique() == 1

    def test_depth_buckets_cover_every_game(self):
        out = stats.session_depth_effect(frame([W] * 8, gaps_min=[10] * 7))
        assert out["games"].sum() == 8


class TestQuickRequeue:
    def test_only_games_following_a_loss_are_counted(self):
        """The question is whether requeueing *after losing* hurts. Including
        post-win games answers something else, and doubles the sample in a way
        that looks like better evidence."""
        out = stats.quick_requeue_effect(frame([W, W, W, W], gaps_min=[2, 2, 2]))
        assert out["quick"] == (0, 0) and out["break"] == (0, 0)

    def test_the_split_lands_on_the_threshold(self):
        # After each loss: one 2-minute requeue, one 60-minute break.
        out = stats.quick_requeue_effect(
            frame([L, W, L, W], gaps_min=[2, 60, 60]), minutes=10)
        assert out["quick"][0] == 1
        assert out["break"][0] == 1

    def test_a_long_game_is_not_mistaken_for_a_long_break(self):
        """Gap is measured from the END of the previous game. Measuring from
        its start would count a 45-minute game plus a 2-minute queue as a
        47-minute break — turning every slow loss into a 'took a break'."""
        out = stats.quick_requeue_effect(
            frame([L, W], gaps_min=[2], durations_min=[45, 30]), minutes=10)
        assert out["quick"][0] == 1, "a long game was counted as a break"
        assert out["break"][0] == 0

    def test_a_frame_without_durations_returns_empty_rather_than_guessing(self):
        d = frame([L, W, L, W])
        out = stats.quick_requeue_effect(d.drop(columns=["game_duration_min"]))
        assert out["quick"] == (0, 0)

    def test_it_reads_the_column_the_parser_actually_emits(self):
        """The real bug: the first version read `game_duration`, which no
        parsed row has. It returned zeroes against 1,546 real games and the
        page said 'no data'. This fails if the name drifts again."""
        row = stats.parse_match(make_match(), "me-puuid")
        assert "game_duration_min" in row
        out = stats.quick_requeue_effect(frame([L, W, L, W], gaps_min=[2, 60, 2]))
        assert out["quick"][0] + out["break"][0] > 0


class TestWorstHours:
    def test_hours_below_the_threshold_are_excluded(self):
        """24 buckets over a few thousand games leaves some nearly empty, and
        the worst-looking hour of a noisy set is usually the emptiest one."""
        out = stats.worst_hours(frame([W, L] * 5, gaps_min=[10] * 9), min_games=20)
        assert out.empty

    def test_worst_first(self):
        """25 games in hour 12 (bad) and 25 in hour 20 (good).

        Durations and gaps are one minute so the games actually stay inside
        one hour. The first version used 30-minute games five minutes apart,
        which spans seventeen hours — every bucket ended up below the
        threshold and the result was empty. The fixture was wrong, not the
        code, and it failed loudly rather than agreeing with a bug.
        """
        bad = frame([L] * 20 + [W] * 5, gaps_min=[1] * 24,
                    durations_min=[1] * 25, start_hour=12)
        good = frame([W] * 20 + [L] * 5, gaps_min=[1] * 24,
                     durations_min=[1] * 25, start_hour=20)
        out = stats.worst_hours(pd.concat([bad, good]), min_games=20)
        assert len(out) == 2, f"expected two qualifying hours, got {len(out)}"
        assert out.iloc[0]["win_rate"] < out.iloc[-1]["win_rate"]
        assert out.iloc[0]["hour"] == 12

    def test_empty_input_is_empty_output(self):
        assert stats.worst_hours(pd.DataFrame()).empty
