"""Role labelling, per-role win rates, and primary-role detection."""
import pandas as pd

from conftest import make_match
from stats import (
    ROLE_LABELS,
    ROLE_ORDER,
    build_dataframe,
    match_scoreboard,
    parse_match,
    primary_roles,
    relative_time,
    rift_only,
    role_label,
    role_split_for_champion,
    win_rate_by_role,
)


def role_df(rows):
    """[(champion, role_label, win)] -> DataFrame shaped like parsed matches."""
    return pd.DataFrame([
        {"champion": c, "role_label": r, "win": w} for c, r, w in rows
    ])


class TestRoleLabel:
    def test_maps_every_api_position(self):
        assert role_label("TOP") == "Top"
        assert role_label("JUNGLE") == "Jungle"
        assert role_label("MIDDLE") == "Mid"
        assert role_label("BOTTOM") == "Bot"
        assert role_label("UTILITY") == "Support"

    def test_case_insensitive(self):
        assert role_label("middle") == "Mid"
        assert role_label("Utility") == "Support"

    def test_blank_and_unknown_become_unknown(self):
        """ARAM and some older matches report no position — that must be a
        readable label, not an empty cell or a crash."""
        for value in ("", None, "AFK", "NONE"):
            assert role_label(value) == "Unknown"

    def test_every_label_is_in_the_display_order(self):
        """A label missing from ROLE_ORDER would silently sort last."""
        for label in list(ROLE_LABELS.values()) + ["Unknown"]:
            assert label in ROLE_ORDER


class TestParsedRole:
    def test_parse_match_adds_both_raw_and_label(self, puuid):
        row = parse_match(make_match(puuid=puuid, position="MIDDLE"), puuid)
        assert row["role"] == "MIDDLE"          # raw kept for stable grouping
        assert row["role_label"] == "Mid"       # friendly for display

    def test_scoreboard_rows_get_labels_too(self, puuid):
        board = match_scoreboard(make_match(puuid=puuid), puuid)
        assert "role_label" in board.columns
        assert set(board["role_label"]) <= set(ROLE_ORDER)

    def test_dataframe_has_role_label(self, sample_df):
        """The fixture is all mid-lane except one ARAM game, which must
        report ARAM rather than the position it nominally had."""
        assert "role_label" in sample_df.columns
        by_label = sample_df["role_label"].value_counts().to_dict()
        assert by_label == {"Mid": 7, "ARAM": 1}

    def test_aram_reports_the_mode_not_a_position(self, puuid):
        """ARAM has no lanes, so "ARAM" is the honest answer — not the
        position Riot happens to leave in the payload, and not "Unknown",
        which would imply missing data rather than an inapplicable field."""
        aram = parse_match(make_match(puuid=puuid, queue_id=450, position="MIDDLE"), puuid)
        assert aram["role_label"] == "ARAM"
        rift = parse_match(make_match(puuid=puuid, queue_id=420, position="MIDDLE"), puuid)
        assert rift["role_label"] == "Mid"

    def test_role_label_queue_override_beats_position(self):
        assert role_label("MIDDLE", "ARAM") == "ARAM"
        assert role_label("", "ARAM") == "ARAM"
        assert role_label("MIDDLE", "Ranked") == "Mid"
        assert role_label("", "Ranked") == "Unknown"


class TestWinRateByRole:
    def test_lane_order_not_frequency_order(self):
        """Support appears most often here, but Top must still come first."""
        rows = [("A", "Support", True)] * 5 + [("B", "Top", True)] * 2 + [("C", "Mid", False)] * 3
        result = win_rate_by_role(role_df(rows))
        assert list(result["role_label"]) == ["Top", "Mid", "Support"]

    def test_win_rates_are_correct(self):
        rows = [("A", "Jungle", True), ("A", "Jungle", True),
                ("A", "Jungle", False), ("A", "Jungle", False)]
        result = win_rate_by_role(role_df(rows))
        assert result.iloc[0]["win_rate"] == 50.0
        assert result.iloc[0]["games"] == 4

    def test_unknown_sorts_last(self):
        rows = [("A", "Unknown", True), ("B", "Top", True)]
        assert list(win_rate_by_role(role_df(rows))["role_label"]) == ["Top", "Unknown"]

    def test_min_games_filter(self):
        rows = [("A", "Top", True)] + [("B", "Mid", True)] * 3
        assert list(win_rate_by_role(role_df(rows), min_games=3)["role_label"]) == ["Mid"]

    def test_empty(self):
        assert win_rate_by_role(pd.DataFrame()).empty


class TestPrimaryRoles:
    def test_picks_the_modal_role(self):
        """Shaco played mostly jungle but sometimes support resolves to
        Jungle — champions genuinely get played in several positions."""
        rows = [("Shaco", "Jungle", True)] * 7 + [("Shaco", "Support", False)] * 2
        assert primary_roles(role_df(rows))["Shaco"] == "Jungle"

    def test_handles_multiple_champions(self):
        rows = [("Shaco", "Jungle", True), ("Lux", "Support", True), ("Ahri", "Mid", False)]
        assert primary_roles(role_df(rows)) == {
            "Shaco": "Jungle", "Lux": "Support", "Ahri": "Mid",
        }

    def test_empty_and_missing_column(self):
        assert primary_roles(pd.DataFrame()) == {}
        assert primary_roles(pd.DataFrame([{"champion": "Ahri"}])) == {}


class TestRoleSplitForChampion:
    def test_splits_one_champion_across_roles(self):
        rows = ([("Pantheon", "Top", True)] * 3
                + [("Pantheon", "Support", False)] * 2
                + [("Ahri", "Mid", True)] * 4)
        result = role_split_for_champion(role_df(rows), "Pantheon")
        assert list(result["role_label"]) == ["Top", "Support"]   # lane order
        assert result[result["role_label"] == "Top"].iloc[0]["win_rate"] == 100.0
        assert result[result["role_label"] == "Support"].iloc[0]["win_rate"] == 0.0

    def test_excludes_other_champions(self):
        rows = [("Pantheon", "Top", True), ("Ahri", "Mid", True)]
        assert result_games(role_split_for_champion(role_df(rows), "Pantheon")) == 1

    def test_single_role_champion(self):
        rows = [("Ahri", "Mid", True)] * 5
        result = role_split_for_champion(role_df(rows), "Ahri")
        assert len(result) == 1 and result.iloc[0]["games"] == 5

    def test_unplayed_champion(self):
        assert role_split_for_champion(role_df([("Ahri", "Mid", True)]), "Teemo").empty


def result_games(df):
    return int(df["games"].sum())


class TestRiftOnly:
    def test_drops_laneless_modes(self):
        df = pd.DataFrame([
            {"queue_category": "Ranked"}, {"queue_category": "Normal"},
            {"queue_category": "ARAM"}, {"queue_category": "Arena"},
        ])
        assert set(rift_only(df)["queue_category"]) == {"Ranked", "Normal"}

    def test_keeps_everything_when_column_missing(self):
        """Older cached rows without the column shouldn't silently vanish."""
        df = pd.DataFrame([{"champion": "Ahri"}])
        assert len(rift_only(df)) == 1

    def test_empty(self):
        assert rift_only(pd.DataFrame()).empty

    def test_all_laneless_returns_empty(self):
        df = pd.DataFrame([{"queue_category": "ARAM"}, {"queue_category": "Arena"}])
        assert rift_only(df).empty


class TestRelativeTime:
    def _now(self):
        return pd.Timestamp("2026-08-09 12:00:00")

    def test_seconds_read_as_just_now(self):
        """Minute-level precision would imply the cache is more live than
        it is, so anything under a minute is just "just now"."""
        assert relative_time(self._now() - pd.Timedelta(seconds=30), self._now()) == "just now"

    def test_minutes_hours_days(self):
        now = self._now()
        assert relative_time(now - pd.Timedelta(minutes=1), now) == "1 minute ago"
        assert relative_time(now - pd.Timedelta(minutes=45), now) == "45 minutes ago"
        assert relative_time(now - pd.Timedelta(hours=1), now) == "1 hour ago"
        assert relative_time(now - pd.Timedelta(hours=5), now) == "5 hours ago"
        assert relative_time(now - pd.Timedelta(days=1), now) == "1 day ago"
        assert relative_time(now - pd.Timedelta(days=9), now) == "9 days ago"

    def test_singular_vs_plural(self):
        now = self._now()
        assert "1 hour ago" == relative_time(now - pd.Timedelta(hours=1), now)
        assert "2 hours ago" == relative_time(now - pd.Timedelta(hours=2), now)

    def test_missing_timestamp(self):
        """Before the first fetch there's no timestamp — must not print
        "None" or raise."""
        assert relative_time(None) == "unknown"

    def test_future_timestamp_does_not_go_negative(self):
        """Clock skew shouldn't produce "in -3 minutes"."""
        now = self._now()
        assert relative_time(now + pd.Timedelta(hours=2), now) == "just now"

    def test_handles_timezone_mismatch(self):
        """A naive stored timestamp compared against an aware "now" would
        raise if not normalised first."""
        aware = pd.Timestamp("2026-08-09 12:00:00", tz="UTC")
        naive = pd.Timestamp("2026-08-09 10:00:00")
        assert relative_time(naive, aware) == "2 hours ago"
