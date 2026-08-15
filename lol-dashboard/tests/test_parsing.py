"""Match-v5 parsing: does a raw API response become the right DataFrame row?"""
import pandas as pd

from conftest import make_match, make_participant
from stats import (
    build_dataframe,
    game_length_bucket,
    match_scoreboard,
    parse_match,
    queue_category,
    queue_label,
)


def test_parse_match_core_fields(puuid):
    match = make_match(puuid=puuid, win=True, duration_sec=1800)
    row = parse_match(match, puuid)

    assert row["match_id"] == "NA1_1"
    assert row["champion"] == "Ahri"
    assert row["win"] is True
    assert row["kills"] == 5 and row["deaths"] == 2 and row["assists"] == 7
    assert row["kda"] == 6.0                      # (5+7)/2
    assert row["cs"] == 160                       # 150 lane + 10 jungle
    assert row["cs_per_min"] == 5.3               # 160 / 30 min
    assert row["game_duration_min"] == 30.0
    assert row["patch"] == "14.15"                # major.minor only, not the build
    assert row["queue_type"] == "Ranked Solo/Duo"
    assert row["queue_category"] == "Ranked"


def test_parse_match_returns_none_when_player_absent(puuid):
    """Defensive path: a match that doesn't contain the tracked player."""
    match = make_match(puuid="someone-else")
    assert parse_match(match, puuid) is None


def test_kda_handles_zero_deaths(puuid):
    """A deathless game must not divide by zero — deaths floor at 1."""
    match = make_match(puuid=puuid, deaths=0, kills=4, assists=6)
    row = parse_match(match, puuid)
    assert row["kda"] == 10.0


def test_lane_opponent_and_duo_detection(puuid):
    """Same-role-opposite-team heuristic finds the enemy mid; duo partner is
    None for a mid laner (it only applies to the bot-lane pairing)."""
    match = make_match(puuid=puuid, opponent_champion="Zed")
    row = parse_match(match, puuid)
    assert row["opponent_champion"] == "Zed"
    assert row["opponent_puuid"] == "enemy-0"
    assert row["duo_partner"] is None


def test_duo_partner_found_for_bot_lane(puuid):
    """An ADC should pair with the UTILITY player on their own team."""
    match = make_match(puuid=puuid, position="BOTTOM")
    row = parse_match(match, puuid)
    # Ally3 is the UTILITY player on team 100 in the fixture.
    assert row["duo_partner"] == "Ally3"


def test_teammates_exclude_self(puuid):
    row = parse_match(make_match(puuid=puuid), puuid)
    assert len(row["teammate_puuids"]) == 4
    assert puuid not in row["teammate_puuids"]
    assert len(row["teammate_names"]) == 4


def test_runes_and_summoner_combo_parsed(puuid):
    row = parse_match(make_match(puuid=puuid), puuid)
    assert row["keystone_id"] == 8112
    assert row["primary_style_id"] == 8100
    assert row["sub_style_id"] == 8300
    # Sorted tuple so Flash+Ignite and Ignite+Flash group together.
    assert row["summoner_combo"] == (4, 12)


def test_summoner_combo_is_order_independent(puuid):
    a = parse_match(make_match(puuid=puuid, summoner1Id=4, summoner2Id=12), puuid)
    b = parse_match(make_match(puuid=puuid, summoner1Id=12, summoner2Id=4), puuid)
    assert a["summoner_combo"] == b["summoner_combo"]


def test_team_objectives_read_from_correct_team(puuid):
    """Objectives must come from *my* team's entry, not the first one blindly."""
    row = parse_match(make_match(puuid=puuid), puuid)
    assert row["team_first_blood"] is True
    assert row["team_first_dragon"] is True
    assert row["team_first_baron"] is False     # team 200 got baron first
    assert row["team_first_tower"] is True


def test_build_is_order_independent(puuid):
    """Final build is a sorted tuple, so item slot order can't create two
    'different' builds out of the same six items."""
    row = parse_match(make_match(puuid=puuid), puuid)
    assert row["build"] == tuple(sorted(row["build"]))
    assert len(row["build"]) == 6               # item0-5, trinket excluded
    assert row["trinket"] == 1006


def test_kill_participation_and_damage_share(puuid):
    """Both are percentages of the team total, so they must be <= 100."""
    row = parse_match(make_match(puuid=puuid), puuid)
    assert 0 < row["kill_participation"] <= 100
    assert 0 < row["damage_share"] <= 100


def test_build_dataframe_sorts_chronologically(sample_df):
    assert list(sample_df["game_creation"]) == sorted(sample_df["game_creation"])
    assert len(sample_df) == 8


def test_build_dataframe_skips_unparseable(puuid):
    """Matches without the tracked player are dropped, not crashed on."""
    matches = [make_match("NA1_0", puuid=puuid), make_match("NA1_1", puuid="other")]
    df = build_dataframe(matches, puuid)
    assert len(df) == 1


def test_build_dataframe_empty_input(puuid):
    assert build_dataframe([], puuid).empty


def test_match_scoreboard_splits_teams(puuid):
    board = match_scoreboard(make_match(puuid=puuid), puuid)
    assert len(board) == 10
    assert board["is_me"].sum() == 1
    assert (board["side"] == "Your Team").sum() == 5
    assert (board["side"] == "Enemy Team").sum() == 5
    # Scoreboard keeps the trinket slot, unlike the main parse.
    assert len(board.iloc[0]["items"]) == 7


class TestQueueMapping:
    def test_known_queues(self):
        assert queue_label(420) == "Ranked Solo/Duo"
        assert queue_category(420) == "Ranked"
        assert queue_category(440) == "Ranked"      # Flex also counts as Ranked
        assert queue_category(450) == "ARAM"

    def test_unknown_queue_falls_back(self):
        assert "9999" in queue_label(9999)
        assert queue_category(9999) == "Other"

    def test_none_queue(self):
        assert queue_label(None) == "Unknown"
        assert queue_category(None) == "Other"


class TestGameLengthBucket:
    def test_boundaries(self):
        assert game_length_bucket(19.9) == "<20 min"
        assert game_length_bucket(20) == "20-30 min"
        assert game_length_bucket(29.9) == "20-30 min"
        assert game_length_bucket(30) == "30-40 min"
        assert game_length_bucket(40) == "40+ min"
        assert game_length_bucket(99) == "40+ min"
