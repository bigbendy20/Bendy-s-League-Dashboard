"""Timeline-derived stats: gold/CS curves, lane differentials, heatmap."""
from conftest import make_timeline
from stats import (
    GOLD_CHECKPOINTS,
    MAP_SIZE,
    cs_at_minute,
    cs_diff_curve,
    cs_diff_summary,
    death_kill_positions,
    find_participant_id,
    gold_at_minute,
    gold_curve,
    gold_diff_curve,
    gold_diff_summary,
)


def test_find_participant_id(puuid):
    tl = make_timeline(puuid=puuid)
    assert find_participant_id(tl, puuid) == 1
    assert find_participant_id(tl, "enemy-0") == 2
    assert find_participant_id(tl, "nobody") is None


class TestGoldAtMinute:
    def test_reads_correct_frame(self, puuid):
        tl = make_timeline(puuid=puuid)
        # Fixture: totalGold = 500 + minute * 300
        assert gold_at_minute(tl, puuid, 10) == 3500
        assert gold_at_minute(tl, puuid, 20) == 6500

    def test_unknown_player_returns_none(self, puuid):
        assert gold_at_minute(make_timeline(puuid=puuid), "nobody", 10) is None

    def test_beyond_game_end_snaps_to_closest_frame(self, puuid):
        """Frames stop at 30; asking for 60 should clamp, not crash."""
        tl = make_timeline(puuid=puuid)
        assert gold_at_minute(tl, puuid, 60) == gold_at_minute(tl, puuid, 30)


class TestCsAtMinute:
    def test_sums_lane_and_jungle(self, puuid):
        """Real CS is minionsKilled + jungleMinionsKilled — a jungler's CS is
        almost entirely the second field, so missing it would badly undercount."""
        tl = make_timeline(puuid=puuid)
        # Fixture at minute 10: 60 lane + 10 jungle
        assert cs_at_minute(tl, puuid, 10) == 70

    def test_unknown_player(self, puuid):
        assert cs_at_minute(make_timeline(puuid=puuid), "nobody", 10) is None


class TestCurves:
    def test_gold_curve_has_a_column_per_checkpoint(self, sample_timelines, puuid):
        curve = gold_curve(sample_timelines, puuid)
        assert len(curve) == 2
        for m in GOLD_CHECKPOINTS:
            assert f"gold_{m}" in curve.columns

    def test_gold_diff_positive_when_ahead(self, sample_timelines, puuid):
        """Fixture gives me 300 gold/min vs the opponent's 250, so the diff
        must be positive and widen over time."""
        opp_map = {mid: "enemy-0" for mid in sample_timelines}
        summary = gold_diff_summary(gold_diff_curve(sample_timelines, opp_map, puuid))
        diffs = summary.dropna(subset=["avg_diff"])
        assert (diffs["avg_diff"] > 0).all()
        assert list(diffs["avg_diff"]) == sorted(diffs["avg_diff"])

    def test_cs_diff_positive_when_ahead(self, sample_timelines, puuid):
        opp_map = {mid: "enemy-0" for mid in sample_timelines}
        summary = cs_diff_summary(cs_diff_curve(sample_timelines, opp_map, puuid))
        assert (summary.dropna(subset=["avg_diff"])["avg_diff"] > 0).all()

    def test_diff_skips_games_without_a_known_opponent(self, sample_timelines, puuid):
        """A missing opponent puuid should drop that game, not produce a row
        of Nones or raise."""
        assert gold_diff_curve(sample_timelines, {}, puuid).empty
        assert cs_diff_curve(sample_timelines, {}, puuid).empty

    def test_summaries_handle_empty(self):
        import pandas as pd

        assert gold_diff_summary(pd.DataFrame()).empty
        assert cs_diff_summary(pd.DataFrame()).empty


class TestDeathKillPositions:
    def test_classifies_kills_and_deaths(self, sample_timelines, puuid):
        """Fixture has one kill and one death per timeline, two timelines."""
        pos = death_kill_positions(sample_timelines, puuid)
        assert len(pos) == 4
        assert (pos["kind"] == "kill").sum() == 2
        assert (pos["kind"] == "death").sum() == 2

    def test_coordinates_within_map_bounds(self, sample_timelines, puuid):
        pos = death_kill_positions(sample_timelines, puuid)
        assert pos["x"].between(0, MAP_SIZE).all()
        assert pos["y"].between(0, MAP_SIZE).all()

    def test_empty_input(self, puuid):
        assert death_kill_positions({}, puuid).empty
