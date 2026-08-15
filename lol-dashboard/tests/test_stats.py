"""Win-rate math, aggregations and derived stats."""
import pandas as pd

from stats import (
    GAME_LENGTH_ORDER,
    OBJECTIVE_COLUMNS,
    averages,
    champion_pool_concentration,
    current_streak,
    longest_streak,
    multikill_summary,
    nemesis_and_free_win,
    objective_participation_rates,
    overall_win_rate,
    personal_bests,
    teammate_synergy,
    vision_summary,
    win_rate_by,
    win_rate_by_length,
    win_rate_by_patch,
)


class TestOverallWinRate:
    def test_counts_and_percentage(self, sample_df):
        games, wins, wr = overall_win_rate(sample_df)
        assert (games, wins) == (8, 5)
        assert wr == 62.5

    def test_empty(self, empty_df):
        assert overall_win_rate(empty_df) == (0, 0, 0.0)

    def test_all_wins_and_all_losses(self, sample_df):
        wins_only = sample_df[sample_df["win"]]
        losses_only = sample_df[~sample_df["win"]]
        assert overall_win_rate(wins_only)[2] == 100.0
        assert overall_win_rate(losses_only)[2] == 0.0


class TestWinRateBy:
    def test_groups_by_champion(self, sample_df):
        wr = win_rate_by(sample_df, "champion")
        ahri = wr[wr["champion"] == "Ahri"].iloc[0]
        assert ahri["games"] == 4
        assert ahri["wins"] == 3
        assert ahri["win_rate"] == 75.0

    def test_min_games_filter(self, sample_df):
        """Small samples get excluded, which is the whole point of the
        threshold — Shaco and Briar have 2 games each."""
        wr = win_rate_by(sample_df, "champion", min_games=3)
        assert set(wr["champion"]) == {"Ahri"}

    def test_sorted_by_games_desc(self, sample_df):
        wr = win_rate_by(sample_df, "champion")
        assert list(wr["games"]) == sorted(wr["games"], reverse=True)

    def test_missing_column_returns_empty_not_error(self, sample_df):
        result = win_rate_by(sample_df, "not_a_real_column")
        assert result.empty

    def test_empty_df(self, empty_df):
        assert win_rate_by(empty_df, "champion").empty


class TestWinRateByPatch:
    def test_chronological_not_alphabetical(self):
        """The bug this guards: string-sorting puts '14.15' before '14.9'."""
        df = pd.DataFrame(
            [{"patch": p, "win": w} for p, w in
             [("14.9", True), ("14.9", True), ("14.15", False), ("14.15", True),
              ("14.10", True), ("14.10", False)]]
        )
        assert list(win_rate_by_patch(df, min_games=2)["patch"]) == ["14.9", "14.10", "14.15"]

    def test_malformed_patch_does_not_crash(self):
        df = pd.DataFrame([{"patch": "weird", "win": True}, {"patch": "weird", "win": False}])
        assert not win_rate_by_patch(df, min_games=1).empty


class TestWinRateByLength:
    def test_buckets_in_logical_order(self, sample_df):
        result = win_rate_by_length(sample_df, min_games=1)
        order = [b for b in GAME_LENGTH_ORDER if b in set(result["game_length_bucket"])]
        assert list(result["game_length_bucket"]) == order

    def test_empty(self, empty_df):
        assert win_rate_by_length(empty_df).empty


class TestStreaks:
    def test_current_streak_counts_back_from_latest(self, sample_df):
        """Fixture ends with three straight losses."""
        assert current_streak(sample_df) == ("loss", 3)

    def test_current_streak_win(self, sample_df):
        """Drop the trailing losses and the active streak flips to wins."""
        trimmed = sample_df.sort_values("game_creation").iloc[:5]
        assert current_streak(trimmed) == ("win", 5)

    def test_current_streak_single_game(self, sample_df):
        one = sample_df.sort_values("game_creation").iloc[:1]
        assert current_streak(one) == ("win", 1)

    def test_current_streak_empty(self, empty_df):
        assert current_streak(empty_df) == (None, 0)

    def test_longest_streak_is_historical_best(self, sample_df):
        """Distinct from current_streak: the fixture's best win run is 5,
        even though the *active* streak is a 3-game loss run."""
        assert longest_streak(sample_df, want_win=True) == 5
        assert longest_streak(sample_df, want_win=False) == 3


class TestAverages:
    def test_keys_and_values(self, sample_df):
        avgs = averages(sample_df)
        assert set(avgs) >= {"kda", "cs_per_min", "vision_score",
                             "kill_participation", "damage_share", "cc_score"}
        assert avgs["vision_score"] == 25.0
        assert avgs["cc_score"] == 30.0

    def test_empty_returns_zeros_not_nan(self, empty_df):
        """NaN would render as 'nan' in the UI; zeros are the honest empty state."""
        avgs = averages(empty_df)
        assert all(v == 0 or v == 0.0 for v in avgs.values())


class TestVisionSummary:
    def test_averages(self, sample_df):
        vis = vision_summary(sample_df)
        assert vis["wards_placed"] == 12.0
        assert vis["wards_killed"] == 3.0
        assert vis["vision_wards_bought"] == 2.0

    def test_empty(self, empty_df):
        assert all(v == 0.0 for v in vision_summary(empty_df).values())


class TestObjectiveParticipationRates:
    def test_percentage_of_games_with_at_least_one(self):
        """A participation *rate*, not an average count — three of four
        games with a dragon is 75%, regardless of how many dragons."""
        df = pd.DataFrame([
            {"dragons": 3, "barons": 0, "heralds": 1, "towers": 2},
            {"dragons": 1, "barons": 1, "heralds": 0, "towers": 0},
            {"dragons": 2, "barons": 0, "heralds": 0, "towers": 1},
            {"dragons": 0, "barons": 0, "heralds": 0, "towers": 0},
        ])
        rates = objective_participation_rates(df)
        assert rates["dragons"] == 75.0
        assert rates["barons"] == 25.0
        assert rates["heralds"] == 25.0
        assert rates["towers"] == 50.0

    def test_multiple_objectives_still_count_once(self):
        """Ten dragons in one game is 100%, not 1000%."""
        df = pd.DataFrame([{"dragons": 10, "barons": 0, "heralds": 0, "towers": 0}])
        assert objective_participation_rates(df)["dragons"] == 100.0

    def test_all_rates_are_valid_percentages(self):
        df = pd.DataFrame([{"dragons": 1, "barons": 2, "heralds": 0, "towers": 5}])
        assert all(0 <= v <= 100 for v in objective_participation_rates(df).values())

    def test_empty_and_none(self, empty_df):
        for source in (empty_df, None):
            rates = objective_participation_rates(source)
            assert set(rates) == set(OBJECTIVE_COLUMNS)
            assert all(v == 0.0 for v in rates.values())

    def test_missing_column_defaults_to_zero(self):
        rates = objective_participation_rates(pd.DataFrame([{"dragons": 1}]))
        assert rates["dragons"] == 100.0
        assert rates["barons"] == 0.0


class TestMultikills:
    def test_totals(self, sample_df):
        mk = multikill_summary(sample_df)
        assert mk["Double Kills"] == 8      # fixture gives every game one
        assert mk["Penta Kills"] == 0

    def test_empty(self, empty_df):
        assert sum(multikill_summary(empty_df).values()) == 0


class TestNemesisAndFreeWin:
    def test_identifies_both_ends(self):
        rows = []
        # Lose 4/5 to Zed, win 4/5 vs Yasuo.
        for i in range(5):
            rows.append({"opponent_champion": "Zed", "win": i == 0})
            rows.append({"opponent_champion": "Yasuo", "win": i != 0})
        df = pd.DataFrame(rows)
        nemesis, free_win = nemesis_and_free_win(df, min_games=3)
        assert nemesis["opponent_champion"] == "Zed"
        assert free_win["opponent_champion"] == "Yasuo"
        assert nemesis["win_rate"] < free_win["win_rate"]

    def test_respects_min_games(self):
        df = pd.DataFrame([{"opponent_champion": "Zed", "win": False}])
        assert nemesis_and_free_win(df, min_games=3) == (None, None)

    def test_missing_column(self, empty_df):
        assert nemesis_and_free_win(empty_df) == (None, None)


class TestTeammateSynergy:
    def test_groups_by_puuid_not_name(self):
        """Someone who changed their display name must stay one row."""
        df = pd.DataFrame([
            {"win": True, "teammate_puuids": ("p1",), "teammate_names": ("OldName",)},
            {"win": True, "teammate_puuids": ("p1",), "teammate_names": ("NewName",)},
            {"win": False, "teammate_puuids": ("p1",), "teammate_names": ("NewName",)},
        ])
        result = teammate_synergy(df, min_games=3)
        assert len(result) == 1
        assert result.iloc[0]["games"] == 3
        assert result.iloc[0]["win_rate"] == 66.7

    def test_min_games_filter(self):
        df = pd.DataFrame([{"win": True, "teammate_puuids": ("p1",), "teammate_names": ("A",)}])
        assert teammate_synergy(df, min_games=3).empty

    def test_empty(self, empty_df):
        assert teammate_synergy(empty_df).empty


class TestPersonalBests:
    def test_returns_one_entry_per_stat(self, sample_df):
        bests = personal_bests(sample_df)
        labels = [b["label"] for b in bests]
        assert "Most Kills" in labels and "Longest Game" in labels
        assert len(labels) == len(set(labels))          # no duplicate categories

    def test_longest_game_picks_the_max(self, sample_df):
        longest = next(b for b in personal_bests(sample_df) if b["label"] == "Longest Game")
        assert longest["value"] == f"{sample_df['game_duration_min'].max()} min"

    def test_empty_returns_empty_list(self, empty_df):
        assert personal_bests(empty_df) == []


class TestPoolConcentration:
    def test_counts(self, sample_df):
        pool = champion_pool_concentration(sample_df, top_n=3)
        assert pool["unique_champions"] == 3
        assert pool["total_games"] == 8
        assert pool["top_n_share"] == 100.0     # only 3 champs exist

    def test_partial_share(self):
        df = pd.DataFrame({"champion": ["A"] * 5 + ["B", "C", "D", "E", "F"]})
        pool = champion_pool_concentration(df, top_n=1)
        assert pool["unique_champions"] == 6
        assert pool["top_n_share"] == 50.0

    def test_empty(self, empty_df):
        pool = champion_pool_concentration(empty_df)
        assert pool["unique_champions"] == 0 and pool["total_games"] == 0
