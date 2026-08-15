"""Empirical win probability, selection-bias helpers, and primary focus."""
import pandas as pd

from conftest import make_timeline
from stats import (
    EVEN_GOLD_THRESHOLD,
    FOCUS_GUIDANCE,
    PERFORMANCE_DIMENSIONS,
    WP_MIN_SAMPLE,
    biggest_swings,
    build_win_probability_table,
    participant_teams,
    primary_focus,
    team_gold_diff_series,
    was_even_at,
    win_probability_at,
    win_probability_curve,
)


def make_match_for_teams(puuid="me-puuid", opp_puuid="enemy-0"):
    """Minimal match JSON carrying just the puuid -> teamId mapping."""
    return {"info": {"participants": [
        {"puuid": puuid, "teamId": 100},
        {"puuid": opp_puuid, "teamId": 200},
    ]}}


def linear_series(slope, minutes=30):
    """[(minute, gold_diff)] climbing (or falling) steadily."""
    return [(m, m * slope) for m in range(minutes + 1)]


class TestParticipantTeams:
    def test_joins_on_puuid_not_id_convention(self, puuid):
        """Deliberately does not assume participants 1-5 are team 100 — the
        mapping comes from the match JSON via puuid."""
        tl = make_timeline(puuid=puuid, opp_puuid="enemy-0")
        teams = participant_teams(tl, make_match_for_teams(puuid))
        assert teams == {1: 100, 2: 200}

    def test_unknown_puuids_are_skipped(self, puuid):
        tl = make_timeline(puuid=puuid, opp_puuid="someone-not-in-match")
        teams = participant_teams(tl, make_match_for_teams(puuid))
        assert teams == {1: 100}

    def test_empty_match(self, puuid):
        assert participant_teams(make_timeline(puuid=puuid), {}) == {}


class TestTeamGoldDiffSeries:
    def test_positive_when_my_team_leads(self, puuid):
        """Fixture gives participant 1 (team 100) 300 gold/min and
        participant 2 (team 200) 250, so team 100 pulls ahead."""
        tl = make_timeline(puuid=puuid)
        teams = participant_teams(tl, make_match_for_teams(puuid))
        series = team_gold_diff_series(tl, teams, my_team=100)
        assert len(series) > 0
        assert series[-1][1] > 0
        assert [d for _, d in series] == sorted(d for _, d in series)

    def test_sign_flips_for_the_other_team(self, puuid):
        tl = make_timeline(puuid=puuid)
        teams = participant_teams(tl, make_match_for_teams(puuid))
        mine = team_gold_diff_series(tl, teams, my_team=100)
        theirs = team_gold_diff_series(tl, teams, my_team=200)
        assert mine[-1][1] == -theirs[-1][1]

    def test_empty_team_map(self, puuid):
        assert team_gold_diff_series(make_timeline(puuid=puuid), {}, 100) == []


class TestWinProbabilityTable:
    def test_counts_wins_and_games(self):
        table = build_win_probability_table(
            {"m1": linear_series(200), "m2": linear_series(200)},
            {"m1": True, "m2": False},
        )
        assert table
        for entry in table.values():
            assert entry["games"] >= entry["wins"]

    def test_one_vote_per_game_per_bucket(self):
        """A 40-minute game shouldn't get to vote 40 times for the same
        state — otherwise long games would dominate the table."""
        table = build_win_probability_table({"m1": [(m, 0) for m in range(40)]}, {"m1": True})
        assert all(e["games"] == 1 for e in table.values())

    def test_ignores_games_without_a_known_result(self):
        assert build_win_probability_table({"m1": linear_series(200)}, {}) == {}

    def test_empty(self):
        assert build_win_probability_table({}, {}) == {}


class TestWinProbabilityAt:
    def _table(self, games, wins):
        return {(0, 0): {"games": games, "wins": wins}}

    def test_reads_a_well_sampled_bucket(self):
        prob, n = win_probability_at(self._table(10, 7), minute=0, gold_diff=0)
        assert prob == 70.0 and n == 10

    def test_thin_bucket_falls_back_to_fifty(self):
        """An honest shrug rather than a confident number from 2 games."""
        prob, n = win_probability_at(self._table(WP_MIN_SAMPLE - 1, 0), minute=0, gold_diff=0)
        assert prob == 50.0
        assert n == WP_MIN_SAMPLE - 1        # still reports the real sample size

    def test_missing_bucket(self):
        prob, n = win_probability_at({}, minute=99, gold_diff=99999)
        assert prob == 50.0 and n == 0

    def test_more_gold_never_scores_worse_in_a_consistent_table(self):
        table = {
            (0, -2000): {"games": 10, "wins": 2},
            (0, 0): {"games": 10, "wins": 5},
            (0, 2000): {"games": 10, "wins": 9},
        }
        behind = win_probability_at(table, 0, -1500)[0]
        even = win_probability_at(table, 0, 500)[0]
        ahead = win_probability_at(table, 0, 2500)[0]
        assert behind < even < ahead


class TestWinProbabilityCurve:
    def test_one_row_per_frame(self):
        table = build_win_probability_table(
            {f"m{i}": linear_series(200) for i in range(10)},
            {f"m{i}": i % 2 == 0 for i in range(10)},
        )
        curve = win_probability_curve(linear_series(200), table)
        assert len(curve) == 31
        assert set(curve.columns) == {"minute", "gold_diff", "win_prob", "samples"}
        assert curve["win_prob"].between(0, 100).all()

    def test_empty_series(self):
        assert win_probability_curve([], {}).empty


class TestBiggestSwings:
    def _curve(self, probs):
        return pd.DataFrame([
            {"minute": i, "gold_diff": 0, "win_prob": p, "samples": 10}
            for i, p in enumerate(probs)
        ])

    def test_finds_the_largest_move(self):
        probs = [50] * 5 + [20] * 10          # a big drop around minute 5
        swings = biggest_swings(self._curve(probs), top_n=1)
        assert len(swings) == 1
        assert swings[0]["direction"] == "loss"
        assert swings[0]["delta"] < 0

    def test_direction_is_labelled_correctly(self):
        swings = biggest_swings(self._curve([20] * 5 + [80] * 10), top_n=1)
        assert swings[0]["direction"] == "gain" and swings[0]["delta"] > 0

    def test_results_do_not_overlap(self):
        """Three adjacent minutes of one teamfight shouldn't fill all slots."""
        probs = [50] * 4 + [10] * 4 + [50] * 4 + [90] * 6
        swings = biggest_swings(self._curve(probs), top_n=3)
        for i, a in enumerate(swings):
            for b in swings[i + 1:]:
                assert a["end_minute"] < b["start_minute"] or a["start_minute"] > b["end_minute"]

    def test_ignores_windows_with_no_sample_backing(self):
        """A swing between two guessed 50% points isn't a real swing."""
        curve = pd.DataFrame([
            {"minute": i, "gold_diff": 0, "win_prob": p, "samples": 0}
            for i, p in enumerate([50] * 5 + [10] * 5)
        ])
        assert biggest_swings(curve) == []

    def test_flat_game_has_no_swings(self):
        assert biggest_swings(self._curve([50] * 20)) == []

    def test_short_or_empty_curve(self):
        assert biggest_swings(pd.DataFrame()) == []
        assert biggest_swings(self._curve([50, 60])) == []


class TestWasEvenAt:
    def test_close_game_is_even(self):
        assert was_even_at([(m, 200) for m in range(20)]) is True

    def test_blowout_is_not_even(self):
        assert was_even_at([(m, m * 500) for m in range(20)]) is False

    def test_threshold_boundary_is_inclusive(self):
        series = [(m, EVEN_GOLD_THRESHOLD) for m in range(20)]
        assert was_even_at(series) is True

    def test_game_ending_before_the_checkpoint_returns_none(self):
        """None, not False — "we don't know" is different from "not even"."""
        assert was_even_at([(m, 0) for m in range(8)]) is None

    def test_empty(self):
        assert was_even_at([]) is None

    def test_negative_deficit_also_counts_as_even(self):
        assert was_even_at([(m, -500) for m in range(20)]) is True


class TestPrimaryFocus:
    def _scores(self, **overrides):
        scores = {d: 60.0 for d in PERFORMANCE_DIMENSIONS}
        scores.update(overrides)
        return scores

    def test_picks_the_weakest_dimension(self, sample_df):
        focus = primary_focus(self._scores(Vision=10.0), sample_df)
        assert focus is not None
        assert focus["dimension"] == "Vision"
        assert focus["score"] == 10.0
        assert focus["observation"] and focus["suggestion"]

    def test_returns_none_on_a_flat_profile(self, sample_df):
        """Singling one out of a flat spread would be inventing a signal."""
        assert primary_focus(self._scores(), sample_df) is None

    def test_skips_versatility_unless_extreme(self, sample_df):
        """A narrow champion pool is a legitimate choice, not a weakness."""
        focus = primary_focus(self._scores(Versatility=30.0, Farming=35.0), sample_df)
        assert focus is not None and focus["dimension"] == "Farming"

    def test_allows_versatility_when_drastically_low(self, sample_df):
        focus = primary_focus(self._scores(Versatility=5.0), sample_df)
        assert focus is not None and focus["dimension"] == "Versatility"

    def test_farming_no_longer_needs_a_role_caveat(self, sample_df):
        """Farming is now scored against role-specific CS bands, so the
        "ignore this if you jungle" disclaimer is obsolete — a disclaimer
        left in place after its cause is fixed just teaches people to
        discount a number that's now correct."""
        focus = primary_focus(self._scores(Farming=10.0), sample_df)
        assert focus["dimension"] == "Farming"
        assert focus["caveat"] is None

    def test_reports_sample_size(self, sample_df):
        focus = primary_focus(self._scores(Vision=10.0), sample_df)
        assert focus["games"] == len(sample_df)

    def test_empty_and_all_zero(self, empty_df):
        assert primary_focus({}, empty_df) is None
        assert primary_focus({d: 0.0 for d in PERFORMANCE_DIMENSIONS}, empty_df) is None

    def test_every_dimension_has_guidance(self):
        """A dimension with no guidance entry would be silently unusable as
        a focus, so the two must stay in sync."""
        for dim in PERFORMANCE_DIMENSIONS:
            assert dim in FOCUS_GUIDANCE, f"missing guidance for {dim}"
            assert len(FOCUS_GUIDANCE[dim]) >= 2
