"""
Shared fixtures for the test suite.

Everything here is synthetic — no Riot API calls, no network, no API key
needed. That's deliberate: these tests cover the pure data-transformation
layer (`stats.py`, `rank_history.py`, `themes.py`), which is exactly the
part that can be verified without credentials or a live service, and also
the part where a silent logic bug would quietly produce wrong numbers on
the dashboard rather than raising an error.

The match fixtures mimic real match-v5 response shape closely enough for
`parse_match` to work on them, including the nested bits that have caused
trouble before (perks.styles, teams[].objectives, participantFrames).
"""
import sys
from pathlib import Path

import pandas as pd

# Import the app modules from the parent directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pytest
except ImportError:  # pragma: no cover
    # Fallback so the suite still runs with nothing installed but pandas —
    # `python tests/run_tests.py` uses this path. These are ordinary pytest
    # tests; this shim only supplies the `@pytest.fixture` decorator so the
    # module imports, and run_tests.py does the fixture resolution itself.
    class _PytestShim:
        @staticmethod
        def fixture(func=None, **_kwargs):
            def wrap(f):
                f._is_fixture = True
                return f

            return wrap(func) if func is not None else wrap

        class approx:  # noqa: N801 - mirrors pytest.approx's lowercase name
            def __init__(self, expected, abs=1e-6, rel=None):
                self.expected, self.abs = expected, abs

            def __eq__(self, other):
                return abs(other - self.expected) <= self.abs

    pytest = _PytestShim()


def make_participant(
    puuid="me-puuid",
    champion="Ahri",
    win=True,
    kills=5,
    deaths=2,
    assists=7,
    team_id=100,
    position="MIDDLE",
    **overrides,
):
    """One participant entry shaped like match-v5's."""
    p = {
        "puuid": puuid,
        "championName": champion,
        "championId": 103,
        "teamId": team_id,
        "teamPosition": position,
        "individualPosition": position,
        "win": win,
        "gameEndedInSurrender": False,
        "gameEndedInEarlySurrender": False,
        "teamEarlySurrendered": False,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "totalMinionsKilled": 150,
        "neutralMinionsKilled": 10,
        "visionScore": 25,
        "goldEarned": 12000,
        "totalDamageDealtToChampions": 20000,
        "firstBloodKill": False,
        "firstBloodAssist": False,
        "firstTowerKill": False,
        "firstTowerAssist": False,
        "summoner1Id": 4,
        "summoner2Id": 12,
        "doubleKills": 1,
        "tripleKills": 0,
        "quadraKills": 0,
        "pentaKills": 0,
        "wardsPlaced": 12,
        "wardsKilled": 3,
        "visionWardsBoughtInGame": 2,
        "timeCCingOthers": 30,
        "riotIdGameName": "Player",
        "perks": {
            "styles": [
                {"style": 8100, "selections": [{"perk": 8112}]},
                {"style": 8300, "selections": [{"perk": 8345}]},
            ]
        },
    }
    for i in range(7):
        p[f"item{i}"] = 1000 + i
    p.update(overrides)
    return p


def make_match(
    match_id="NA1_1",
    puuid="me-puuid",
    win=True,
    queue_id=420,
    duration_sec=1800,
    game_version="14.15.1",
    creation_ms=1_700_000_000_000,
    champion="Ahri",
    opponent_champion="Zed",
    **participant_overrides,
):
    """A full 10-player match-v5 response, with the tracked player on team
    100 in MIDDLE and a same-role opponent on team 200 (so the lane-opponent
    heuristic has something to find)."""
    me = make_participant(
        puuid=puuid, champion=champion, win=win, **participant_overrides
    )
    participants = [me]
    # Rest of my team.
    for i, pos in enumerate(["TOP", "JUNGLE", "BOTTOM", "UTILITY"]):
        participants.append(
            make_participant(
                puuid=f"ally-{i}",
                champion=f"Ally{i}",
                win=win,
                team_id=100,
                position=pos,
                riotIdGameName=f"Ally{i}",
            )
        )
    # Enemy team, with a MIDDLE laner as my direct opponent.
    for i, pos in enumerate(["MIDDLE", "TOP", "JUNGLE", "BOTTOM", "UTILITY"]):
        participants.append(
            make_participant(
                puuid=f"enemy-{i}",
                champion=opponent_champion if pos == "MIDDLE" else f"Enemy{i}",
                win=not win,
                team_id=200,
                position=pos,
                riotIdGameName=f"Enemy{i}",
            )
        )

    return {
        "metadata": {"matchId": match_id},
        "info": {
            "gameCreation": creation_ms,
            "gameDuration": duration_sec,
            "gameMode": "CLASSIC",
            "gameVersion": game_version,
            "queueId": queue_id,
            "participants": participants,
            "teams": [
                {
                    "teamId": 100,
                    "objectives": {
                        "champion": {"first": True},
                        "dragon": {"first": True},
                        "baron": {"first": False},
                        "tower": {"first": True},
                    },
                },
                {
                    "teamId": 200,
                    "objectives": {
                        "champion": {"first": False},
                        "dragon": {"first": False},
                        "baron": {"first": True},
                        "tower": {"first": False},
                    },
                },
            ],
        },
    }


def make_timeline(match_id="NA1_1", puuid="me-puuid", opp_puuid="enemy-0"):
    """Timeline with per-minute frames for two players, plus a couple of
    CHAMPION_KILL events (one where I'm the killer, one where I'm the
    victim) so heatmap parsing has something to find."""
    participants = [
        {"participantId": 1, "puuid": puuid},
        {"participantId": 2, "puuid": opp_puuid},
    ]
    frames = []
    for minute in range(0, 31):
        frames.append(
            {
                "timestamp": minute * 60000,
                "participantFrames": {
                    "1": {
                        "totalGold": 500 + minute * 300,
                        "minionsKilled": minute * 6,
                        "jungleMinionsKilled": minute,
                    },
                    "2": {
                        "totalGold": 500 + minute * 250,
                        "minionsKilled": minute * 5,
                        "jungleMinionsKilled": 0,
                    },
                },
                "events": [],
            }
        )
    frames[10]["events"] = [
        {
            "type": "CHAMPION_KILL",
            "killerId": 1,
            "victimId": 2,
            "assistingParticipantIds": [],
            "position": {"x": 5000, "y": 6000},
            "timestamp": 600000,
        }
    ]
    frames[20]["events"] = [
        {
            "type": "CHAMPION_KILL",
            "killerId": 2,
            "victimId": 1,
            "assistingParticipantIds": [],
            "position": {"x": 9000, "y": 3000},
            "timestamp": 1200000,
        }
    ]
    return {"metadata": {"matchId": match_id}, "info": {"participants": participants, "frames": frames}}


@pytest.fixture
def puuid():
    return "me-puuid"


@pytest.fixture
def sample_matches(puuid):
    """Eight games with a deliberate, hand-checkable shape:
    5 wins / 3 losses overall, the three most recent all losses (for streak
    tests), spread across champions, durations and patches."""
    specs = [
        # (champion, win, duration_sec, patch, queue_id)
        ("Ahri", True, 1100, "14.14.1", 420),    # <20 min
        ("Ahri", True, 1500, "14.14.1", 420),    # 20-30
        ("Ahri", True, 2100, "14.15.1", 420),    # 30-40
        ("Shaco", True, 2600, "14.15.1", 420),   # 40+
        ("Shaco", True, 1500, "14.15.1", 450),   # ARAM
        ("Briar", False, 1500, "14.15.1", 420),
        ("Briar", False, 2100, "14.15.1", 420),
        ("Ahri", False, 1500, "14.15.1", 420),
    ]
    matches = []
    for i, (champ, win, dur, patch, queue) in enumerate(specs):
        matches.append(
            make_match(
                match_id=f"NA1_{i}",
                puuid=puuid,
                win=win,
                champion=champ,
                queue_id=queue,
                duration_sec=dur,
                game_version=patch,
                creation_ms=1_700_000_000_000 + i * 3_600_000,
            )
        )
    return matches


@pytest.fixture
def sample_df(sample_matches, puuid):
    from stats import build_dataframe

    return build_dataframe(sample_matches, puuid)


@pytest.fixture
def sample_timelines(puuid):
    return {
        "NA1_0": make_timeline("NA1_0", puuid, "enemy-0"),
        "NA1_1": make_timeline("NA1_1", puuid, "enemy-0"),
    }


@pytest.fixture
def empty_df():
    return pd.DataFrame()
