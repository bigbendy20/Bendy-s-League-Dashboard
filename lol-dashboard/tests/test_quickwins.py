"""Side, pings, surrenders, Flash slot, and LP-since-last-check."""
import pandas as pd

import rank_history as rh
from conftest import make_match
from stats import (
    FLASH_SPELL_ID,
    PING_FIELDS,
    PING_LABELS,
    flash_slot_win_rate,
    parse_match,
    ping_summary,
    side_win_rate,
    surrender_summary,
)


class TestParsedQuickWinFields:
    def test_side_from_team_id(self, puuid):
        blue = parse_match(make_match(puuid=puuid, team_id=100), puuid)
        assert blue["side"] == "Blue"

    def test_flash_slot_detected_on_d(self, puuid):
        row = parse_match(make_match(puuid=puuid, summoner1Id=FLASH_SPELL_ID, summoner2Id=12), puuid)
        assert row["flash_slot"] == "D"

    def test_flash_slot_detected_on_f(self, puuid):
        row = parse_match(make_match(puuid=puuid, summoner1Id=12, summoner2Id=FLASH_SPELL_ID), puuid)
        assert row["flash_slot"] == "F"

    def test_no_flash_is_none(self, puuid):
        """Not every game takes Flash — that's None, not a default of "D"."""
        row = parse_match(make_match(puuid=puuid, summoner1Id=12, summoner2Id=14), puuid)
        assert row["flash_slot"] is None

    def test_surrender_flags(self, puuid):
        row = parse_match(make_match(puuid=puuid, gameEndedInSurrender=True), puuid)
        assert row["ended_in_surrender"] is True
        assert row["ended_in_early_surrender"] is False

    def test_ping_fields_parsed_and_totalled(self, puuid):
        row = parse_match(
            make_match(puuid=puuid, enemyMissingPings=7, onMyWayPings=3), puuid
        )
        assert row["ping_enemy_missing"] == 7
        assert row["ping_on_my_way"] == 3
        assert row["total_pings"] == 10

    def test_missing_ping_fields_default_to_zero(self, puuid):
        """Older matches predate these counters — must be 0, not NaN."""
        row = parse_match(make_match(puuid=puuid), puuid)
        assert row["total_pings"] == 0
        assert all(row[name] == 0 for name in PING_FIELDS)


class TestSideWinRate:
    def test_blue_always_listed_first(self):
        """Fixed order so the rows don't swap as the record changes."""
        df = pd.DataFrame(
            [{"side": "Red", "win": True}] * 5 + [{"side": "Blue", "win": False}] * 2
        )
        assert list(side_win_rate(df)["side"]) == ["Blue", "Red"]

    def test_rates(self):
        df = pd.DataFrame([
            {"side": "Blue", "win": True}, {"side": "Blue", "win": False},
            {"side": "Red", "win": True}, {"side": "Red", "win": True},
        ])
        result = side_win_rate(df).set_index("side")
        assert result.loc["Blue", "win_rate"] == 50.0
        assert result.loc["Red", "win_rate"] == 100.0

    def test_min_games(self):
        df = pd.DataFrame([{"side": "Blue", "win": True}])
        assert side_win_rate(df, min_games=3).empty

    def test_empty(self):
        assert side_win_rate(pd.DataFrame()).empty


class TestPingSummary:
    def _df(self, **counts):
        row = {name: 0 for name in PING_FIELDS}
        row.update(counts)
        return pd.DataFrame([row, row])

    def test_averages_and_totals(self):
        result = ping_summary(self._df(ping_enemy_missing=4)).set_index("ping")
        label = PING_LABELS["ping_enemy_missing"]
        assert result.loc[label, "per_game"] == 4.0
        assert result.loc[label, "total"] == 8

    def test_unused_ping_types_are_dropped(self):
        """13 rows of zeros would bury the two types you actually use."""
        result = ping_summary(self._df(ping_danger=2))
        assert list(result["ping"]) == [PING_LABELS["ping_danger"]]

    def test_sorted_by_total_desc(self):
        result = ping_summary(self._df(ping_danger=1, ping_on_my_way=5))
        assert list(result["total"]) == sorted(result["total"], reverse=True)

    def test_missing_columns_returns_empty(self):
        """An older cache without ping columns must yield nothing, not raise."""
        assert ping_summary(pd.DataFrame([{"win": True}])).empty

    def test_empty(self):
        assert ping_summary(pd.DataFrame()).empty


class TestSurrenderSummary:
    def _df(self, flags, wins=None):
        wins = wins if wins is not None else [True] * len(flags)
        return pd.DataFrame([
            {"ended_in_surrender": f, "ended_in_early_surrender": False, "win": w}
            for f, w in zip(flags, wins)
        ])

    def test_rate_and_split(self):
        """Deliberately shaped so "share of games that ended in FF" (25%)
        and "share of FF games you won" (100%) are different numbers — an
        even split would let the two be confused for each other."""
        result = self._df(
            [True, False, False, False],
            [True, True, False, True],
        )
        summary = surrender_summary(result)
        assert summary["surrender_rate"] == 25.0      # 1 of 4 games
        assert summary["surrendered_games"] == 1
        assert summary["wins_by_surrender"] == 1
        assert summary["losses_by_surrender"] == 0

    def test_no_surrenders(self):
        summary = surrender_summary(self._df([False, False]))
        assert summary["surrender_rate"] == 0.0
        assert summary["surrendered_games"] == 0

    def test_missing_column_and_empty(self):
        for source in (pd.DataFrame(), pd.DataFrame([{"win": True}])):
            summary = surrender_summary(source)
            assert summary["surrendered_games"] == 0


class TestFlashSlotWinRate:
    def test_d_before_f(self):
        df = pd.DataFrame(
            [{"flash_slot": "F", "win": True}] * 4 + [{"flash_slot": "D", "win": False}] * 4
        )
        assert list(flash_slot_win_rate(df, min_games=3)["flash_slot"]) == ["D", "F"]

    def test_games_without_flash_are_excluded(self):
        """Not every game takes Flash. Those rows must not become a third
        bucket, and must not be silently counted under D or F."""
        df = pd.DataFrame([
            {"flash_slot": "D", "win": True},
            {"flash_slot": None, "win": False},
            {"flash_slot": None, "win": False},
        ])
        result = flash_slot_win_rate(df)
        assert int(result["games"].sum()) == 1
        assert set(result["flash_slot"]) == {"D"}
        assert result.iloc[0]["win_rate"] == 100.0

    def test_missing_column_and_empty(self):
        assert flash_slot_win_rate(pd.DataFrame()).empty
        assert flash_slot_win_rate(pd.DataFrame([{"win": True}])).empty


class TestLpSinceLastCheck:
    def _hist(self, entries):
        return pd.DataFrame([
            {"timestamp": pd.Timestamp("2026-08-01") + pd.Timedelta(days=i),
             "tier": t, "rank": r, "league_points": lp}
            for i, (t, r, lp) in enumerate(entries)
        ])

    def test_gain(self):
        result = rh.lp_since_last_check(self._hist([("GOLD", "IV", 20), ("GOLD", "IV", 58)]))
        assert result["delta"] == 38
        assert result["promoted"] is False

    def test_loss_is_negative(self):
        result = rh.lp_since_last_check(self._hist([("GOLD", "IV", 60), ("GOLD", "IV", 40)]))
        assert result["delta"] == -20

    def test_promotion_reads_as_a_gain(self):
        """Raw LP would call this a 90-point loss; the ordinal gets it right."""
        result = rh.lp_since_last_check(self._hist([("GOLD", "IV", 90), ("GOLD", "III", 10)]))
        assert result["delta"] > 0
        assert result["promoted"] is True

    def test_none_before_a_second_snapshot(self):
        """First ever refresh has nothing to compare against — None, not 0,
        so the UI can stay silent rather than claiming "0 LP change"."""
        assert rh.lp_since_last_check(self._hist([("GOLD", "IV", 20)])) is None
        assert rh.lp_since_last_check(pd.DataFrame()) is None
        assert rh.lp_since_last_check(None) is None

    def test_unranked_snapshot_returns_none(self):
        assert rh.lp_since_last_check(self._hist([(None, None, None), ("GOLD", "IV", 5)])) is None
