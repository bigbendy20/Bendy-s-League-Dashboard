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


def ff_frame(rows):
    """Games with explicit surrender flags.

    `rows` is (win, ended_in_surrender, early) triples. Built through
    `parse_match` so the column names are the ones the parser really emits —
    the surrender card that shipped before this one read
    `ended_in_surrender` when nothing produced it, and returned zeroes to
    everyone for months.
    """
    import datetime

    out, when = [], datetime.datetime(2026, 3, 1, 18, 0)
    for i, (win, ff, early) in enumerate(rows):
        match = make_match(match_id=f"NA1_{i}", puuid="p1", win=win,
                           creation_ms=int(when.timestamp() * 1000))
        for p in match["info"]["participants"]:
            p["gameEndedInSurrender"] = ff
            p["gameEndedInEarlySurrender"] = early
        out.append(stats.parse_match(match, "p1"))
        when += datetime.timedelta(minutes=60)
    return pd.DataFrame(out)


class TestSurrenderParsing:
    def test_the_parser_emits_the_columns_the_card_reads(self):
        """The bug this whole feature uncovered: `surrender_summary` had read
        `ended_in_surrender` since the day it was written, and `parse_match`
        never produced it. The guard for a missing column returned zeroes, so
        the card showed 0.0% forever and its unit test passed because the
        fixture built the column by hand."""
        row = stats.parse_match(make_match(), "me-puuid")
        for column in ("ended_in_surrender", "ended_in_early_surrender",
                       "we_surrendered", "enemy_surrendered"):
            assert column in row, column

    def test_surrender_is_attributed_by_the_result(self):
        """`gameEndedInSurrender` is true for BOTH teams — it says a vote
        passed, not whose. Losing plus surrendered is you; winning plus
        surrendered is them. Treating the raw flag as "you gave up" would
        double every count."""
        lost_to_ff = stats.parse_match(
            _with_flags(make_match(win=False), ff=True), "me-puuid")
        won_by_ff = stats.parse_match(
            _with_flags(make_match(win=True), ff=True), "me-puuid")
        assert lost_to_ff["we_surrendered"] and not lost_to_ff["enemy_surrendered"]
        assert won_by_ff["enemy_surrendered"] and not won_by_ff["we_surrendered"]

    def test_a_fought_out_game_is_neither(self):
        row = stats.parse_match(make_match(win=False), "me-puuid")
        assert not row["we_surrendered"] and not row["enemy_surrendered"]


def _with_flags(match, ff=False, early=False):
    for p in match["info"]["participants"]:
        p["gameEndedInSurrender"] = ff
        p["gameEndedInEarlySurrender"] = early
    return match


class TestSurrenderBreakdown:
    def test_rates_are_shares_of_losses_and_wins_separately(self):
        """Two losses (one FF'd) and two wins (one enemy FF) gives 50% on both
        sides. Deliberately different denominators — dividing either by total
        games would give 25% and quietly answer a different question."""
        out = stats.surrender_breakdown(ff_frame([
            (False, True, False), (False, False, False),
            (True, True, False), (True, False, False),
        ]))
        assert out["ff_loss_rate"] == 50.0
        assert out["enemy_ff_win_rate"] == 50.0
        assert out["ff_losses"] == 1 and out["enemy_ff_wins"] == 1

    def test_remakes_are_excluded_from_the_rates_but_counted(self):
        """A three-minute AFK remake is not someone giving up, and letting it
        into the denominator would make every player look more stubborn."""
        out = stats.surrender_breakdown(ff_frame([
            (False, True, False), (False, True, True), (True, False, False),
        ]))
        assert out["remakes"] == 1
        assert out["losses"] == 1, "the remake was counted as a real loss"
        assert out["ff_loss_rate"] == 100.0

    def test_missing_columns_give_an_empty_summary_not_a_wrong_one(self):
        """Rows stored before surrenders were parsed genuinely lack these
        columns — the store keeps the parsed row, so old rows carry only the
        fields that existed when they were written. Dropping the columns is
        what reproduces that; `frame()` builds through `parse_match`, which
        now emits them, so it can't."""
        old_rows = frame([W, L]).drop(columns=["we_surrendered", "enemy_surrendered"])
        out = stats.surrender_breakdown(old_rows)
        assert out["losses"] == 0 and out["ff_loss_rate"] is None

    def test_game_lengths_are_reported_per_kind_of_loss(self):
        d = ff_frame([(False, True, False), (False, False, False)])
        d.loc[0, "game_duration_min"] = 18
        d.loc[1, "game_duration_min"] = 36
        out = stats.surrender_breakdown(d)
        assert out["ff_loss_minutes"] == 18
        assert out["fought_loss_minutes"] == 36


class TestSurrenderAfterLosses:
    def test_the_denominator_is_losses_not_games(self):
        """"40% of your losses after two losses were forfeits" is a sentence
        about giving up. Dividing by all games instead would make it a
        sentence about winning, and the number would move with form."""
        out = stats.surrender_after_losses(ff_frame([
            (True, False, False),    # win (no preceding game counted)
            (False, True, False),    # loss after a win, FF'd
            (False, False, False),   # loss after 1 loss, fought
        ]))
        by = {r["after"]: r for _, r in out.iterrows()}
        assert by["After a win"]["losses"] == 1
        assert by["After a win"]["ff_rate"] == 100.0

    def test_wins_are_not_in_the_table_at_all(self):
        out = stats.surrender_after_losses(ff_frame([(True, False, False)] * 6))
        assert out.empty

    def test_remakes_do_not_break_the_streak(self):
        """A remake isn't a loss and shouldn't reset a losing run — treating
        it as a win would hide exactly the pattern this measures."""
        out = stats.surrender_after_losses(ff_frame([
            (False, False, False), (False, False, True), (False, True, False),
        ]))
        assert "After 1 loss" in set(out["after"])


class TestSurrenderByChampion:
    def test_only_champions_with_enough_losses_appear(self):
        d = ff_frame([(False, True, False)] * 3)
        assert stats.surrender_by_champion(d, min_losses=8).empty

    def test_rate_is_forfeits_over_that_champions_losses(self):
        d = ff_frame([(False, True, False)] * 5 + [(False, False, False)] * 5)
        out = stats.surrender_by_champion(d, min_losses=8)
        assert out.iloc[0]["losses"] == 10
        assert out.iloc[0]["ff_rate"] == 50.0
