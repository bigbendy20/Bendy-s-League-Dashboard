"""Recommendations engine, build grouping, and regression guards."""
import pandas as pd

import insights
from stats import (
    build_win_rate,
    opening_build,
    opening_build_win_rate,
    performance_by_hour,
    performance_by_weekday,
    skin_usage,
    win_rate_after_result,
)


class TestRecommendations:
    def test_returns_list_of_toned_tips(self, sample_df):
        tips = insights.generate_recommendations(sample_df)
        assert isinstance(tips, list)
        for tip in tips:
            assert set(tip) >= {"tone", "text"}
            assert tip["tone"] in {"positive", "warning", "neutral"}
            assert tip["text"].strip()

    def test_stays_quiet_on_small_samples(self):
        """Below the minimum sample size it must say nothing rather than
        report noise as a pattern."""
        df = pd.DataFrame([
            {"game_creation": pd.Timestamp("2026-08-01") + pd.Timedelta(hours=i),
             "win": i % 2 == 0, "champion": "Ahri", "patch": "14.15"}
            for i in range(insights.MIN_GAMES_FOR_SIGNAL - 1)
        ])
        assert insights.generate_recommendations(df) == []

    def test_empty_input(self, empty_df):
        assert insights.generate_recommendations(empty_df) == []

    def test_flags_a_real_recent_slump(self):
        """20 wins then 10 losses should trip the recent-form warning."""
        rows = []
        for i in range(30):
            rows.append({
                "game_creation": pd.Timestamp("2026-08-01") + pd.Timedelta(hours=i),
                "win": i < 20,
                "champion": "Ahri",
                "patch": "14.15",
            })
        tips = insights.generate_recommendations(pd.DataFrame(rows))
        assert any(t["tone"] == "warning" for t in tips)


class TestOpeningBuild:
    ITEMS = {
        3006: {"tags": ["Boots"], "gold": {"total": 1100}},
        3031: {"tags": ["CriticalStrike"], "gold": {"total": 3400}},
        3087: {"tags": ["CriticalStrike"], "gold": {"total": 2600}},
        2003: {"tags": ["Consumable"], "gold": {"total": 50}},     # potion
        3340: {"tags": ["Trinket"], "gold": {"total": 0}},         # ward
    }

    def test_filters_out_consumables_and_trinkets(self):
        events = [
            {"item_id": 2003}, {"item_id": 3340}, {"item_id": 3006},
            {"item_id": 3087}, {"item_id": 3031},
        ]
        assert opening_build(events, self.ITEMS, n=3) == (3006, 3087, 3031)

    def test_preserves_purchase_order(self):
        events = [{"item_id": 3031}, {"item_id": 3087}]
        assert opening_build(events, self.ITEMS, n=2) == (3031, 3087)

    def test_no_core_items_yields_empty_tuple(self):
        assert opening_build([{"item_id": 2003}], self.ITEMS) == ()


class TestOpeningBuildWinRate:
    def test_unloaded_games_are_excluded(self):
        """Only two of these four games have opening data; counts must
        reflect that rather than silently including the un-loaded ones.

        Worth knowing what this does and doesn't prove: `.map()` over a dict
        yields NaN for missing keys, and `bool(float('nan'))` is True, so the
        original truthiness-based filter did let those rows through — but
        pandas' `groupby` drops NaN keys by default, so the *grouped output*
        came out the same either way. This test pins the observable behavior;
        `test_only_tuples_count` below is the one that actually pins the
        explicit isinstance check."""
        df = pd.DataFrame([
            {"match_id": "m1", "champion": "Ahri", "win": True},
            {"match_id": "m2", "champion": "Ahri", "win": False},
            {"match_id": "m3", "champion": "Ahri", "win": True},
            {"match_id": "m4", "champion": "Ahri", "win": True},
        ])
        openings = {"m1": (3006, 3031), "m2": (3006, 3031)}   # m3/m4 not loaded
        result = opening_build_win_rate(df, openings, "Ahri")
        assert len(result) == 1
        assert result.iloc[0]["games"] == 2

    def test_only_tuples_count(self):
        """The filter requires an actual tuple, not merely something truthy.
        A list would group as an unhashable key and blow up in pandas, so
        rejecting non-tuples up front is what keeps this from raising."""
        df = pd.DataFrame([
            {"match_id": "m1", "champion": "Ahri", "win": True},
            {"match_id": "m2", "champion": "Ahri", "win": False},
        ])
        result = opening_build_win_rate(df, {"m1": [3006], "m2": [3006]}, "Ahri")
        assert result.empty

    def test_empty_openings_returns_empty_frame(self):
        df = pd.DataFrame([{"match_id": "m1", "champion": "Ahri", "win": True}])
        assert opening_build_win_rate(df, {}, "Ahri").empty

    def test_scoped_to_the_requested_champion(self):
        df = pd.DataFrame([
            {"match_id": "m1", "champion": "Ahri", "win": True},
            {"match_id": "m2", "champion": "Shaco", "win": False},
        ])
        openings = {"m1": (3006,), "m2": (3006,)}
        result = opening_build_win_rate(df, openings, "Ahri")
        assert result.iloc[0]["games"] == 1


class TestBuildAndSkinUsage:
    def test_build_win_rate_groups_identical_builds(self, sample_df):
        """Every fixture game ends with the same six items, so Ahri's four
        games should collapse into a single build row."""
        builds = build_win_rate(sample_df, "Ahri")
        assert len(builds) == 1
        assert builds.iloc[0]["games"] == 4

    def test_skin_usage(self, sample_df):
        skins = skin_usage(sample_df, "Ahri")
        assert skins.iloc[0]["skin_id"] == 0
        assert skins.iloc[0]["games"] == 4

    def test_unplayed_champion_returns_empty(self, sample_df):
        assert build_win_rate(sample_df, "Teemo").empty
        assert skin_usage(sample_df, "Teemo").empty


class TestTimeBasedStats:
    def test_by_hour_within_valid_range(self, sample_df):
        result = performance_by_hour(sample_df)
        assert result["hour"].between(0, 23).all()

    def test_by_weekday(self, sample_df):
        assert not performance_by_weekday(sample_df).empty

    def test_after_result_labels(self, sample_df):
        result = win_rate_after_result(sample_df)
        assert set(result["prev_result"]) <= {"After a Win", "After a Loss"}

    def test_after_result_drops_the_first_game(self, sample_df):
        """The first game has no previous game to compare against, so the
        total here should be one less than the number of games."""
        result = win_rate_after_result(sample_df)
        assert result["games"].sum() == len(sample_df) - 1

    def test_after_result_needs_two_games(self):
        one = pd.DataFrame([{"game_creation": pd.Timestamp("2026-08-01"), "win": True}])
        assert win_rate_after_result(one).empty

    def test_empty_inputs(self, empty_df):
        assert performance_by_hour(empty_df).empty
        assert performance_by_weekday(empty_df).empty
        assert win_rate_after_result(empty_df).empty
