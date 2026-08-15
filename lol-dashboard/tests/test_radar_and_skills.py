"""Performance radar scoring and skill-order parsing."""
import pandas as pd

from stats import (
    MAX_SKILL_POINTS,
    PERFORMANCE_DIMENSIONS,
    parse_skill_level_ups,
    performance_radar,
    radar_highlights,
    skill_max_order,
    skill_order_win_rate,
)


def make_skill_timeline(events, puuid="me-puuid", participant_id=1):
    """Timeline containing only SKILL_LEVEL_UP events. `events` is a list of
    (timestamp, skill_slot) — or (timestamp, slot, participant_id) to place
    an event on someone else."""
    frames = []
    for item in events:
        ts, slot = item[0], item[1]
        pid = item[2] if len(item) > 2 else participant_id
        frames.append({
            "timestamp": ts,
            "participantFrames": {},
            "events": [{
                "type": "SKILL_LEVEL_UP",
                "participantId": pid,
                "skillSlot": slot,
                "timestamp": ts,
                "levelUpType": "NORMAL",
            }],
        })
    return {
        "metadata": {"matchId": "NA1_1"},
        "info": {"participants": [{"participantId": participant_id, "puuid": puuid}], "frames": frames},
    }


class TestPerformanceRadar:
    def test_all_dimensions_present_and_in_range(self, sample_df):
        scores = performance_radar(sample_df)
        assert set(scores) == set(PERFORMANCE_DIMENSIONS)
        for name, value in scores.items():
            assert 0 <= value <= 100, f"{name} out of range: {value}"

    def test_empty_returns_zeros_not_nan(self, empty_df):
        scores = performance_radar(empty_df)
        assert set(scores) == set(PERFORMANCE_DIMENSIONS)
        assert all(v == 0.0 for v in scores.values())

    def test_single_game_does_not_crash(self, sample_df):
        """Consistency divides by a standard deviation, which is undefined
        for one row — must not produce NaN."""
        one = sample_df.iloc[:1]
        scores = performance_radar(one)
        assert all(0 <= v <= 100 for v in scores.values())

    def test_aggression_responds_to_kill_participation(self, sample_df):
        passive = sample_df.copy()
        passive["kill_participation"] = 30.0
        active = sample_df.copy()
        active["kill_participation"] = 75.0
        assert performance_radar(active)["Aggression"] > performance_radar(passive)["Aggression"]

    def test_survivability_is_inverted(self, sample_df):
        """More deaths must score *lower*, not higher."""
        few = sample_df.copy()
        few["deaths"] = 2
        many = sample_df.copy()
        many["deaths"] = 12
        assert performance_radar(few)["Survivability"] > performance_radar(many)["Survivability"]

    def test_aram_does_not_count_as_an_extra_role(self, sample_df):
        """ARAM isn't a sixth position. A one-lane player who also plays
        ARAM should not score as more role-versatile than one who doesn't."""
        one_lane = sample_df.copy()
        one_lane["role_label"] = "Mid"
        one_lane["queue_category"] = "Ranked"

        plus_aram = one_lane.copy()
        plus_aram.loc[plus_aram.index[:3], "role_label"] = "ARAM"
        plus_aram.loc[plus_aram.index[:3], "queue_category"] = "ARAM"

        assert (performance_radar(plus_aram)["Versatility"]
                == performance_radar(one_lane)["Versatility"])

    def test_objectives_ignores_modes_without_objectives(self, sample_df):
        """ARAM has no dragons, barons or heralds, so those games must not
        drag the Objectives score down for never taking objectives that
        weren't on the map."""
        rift = sample_df.copy()
        rift["queue_category"] = "Ranked"
        for col in ("team_first_dragon", "team_first_baron", "team_first_tower"):
            rift[col] = True

        with_aram = pd.concat([rift, rift.assign(
            queue_category="ARAM", team_first_dragon=False,
            team_first_baron=False, team_first_tower=False,
        )], ignore_index=True)

        assert (performance_radar(with_aram)["Objectives"]
                == performance_radar(rift)["Objectives"])

    def test_all_laneless_history_returns_no_profile(self, sample_df):
        """An all-ARAM history can't be described by Rift reference bands at
        all — not just Objectives. Every dimension comes back zero so the UI
        can say "no Summoner's Rift games" rather than implying you scored
        badly. (Earlier this only neutralised Objectives, which left the
        other seven dimensions quietly wrong.)"""
        aram_only = sample_df.copy()
        aram_only["queue_category"] = "ARAM"
        scores = performance_radar(aram_only)
        assert all(v == 0.0 for v in scores.values())

    def test_aram_games_do_not_shift_any_dimension(self, sample_df):
        """The root fix: adding ARAM games to a Rift history must not move
        the profile at all, on any axis."""
        rift = sample_df.copy()
        rift["queue_category"] = "Ranked"

        noisy = rift.copy()
        noisy["queue_category"] = "ARAM"
        noisy["deaths"] = 20          # ARAM-style stats that would distort
        noisy["assists"] = 30
        noisy["vision_score"] = 0
        combined = pd.concat([rift, noisy], ignore_index=True)

        assert performance_radar(combined) == performance_radar(rift)

    def test_farming_is_scored_against_role_bands(self, sample_df):
        """A support on 1.5 CS/min is farming normally; a mid laner on 1.5
        is not. Under one shared band both scored near zero, which made the
        dimension useless for supports and junglers."""
        base = sample_df.copy()
        base["queue_category"] = "Ranked"
        base["cs_per_min"] = 1.5

        support = base.copy()
        support["role_label"] = "Support"
        mid = base.copy()
        mid["role_label"] = "Mid"

        assert performance_radar(support)["Farming"] > performance_radar(mid)["Farming"]

    def test_farming_blends_across_multiple_roles(self, sample_df):
        """A two-role player is scored against both bands, weighted by how
        many games they played in each — not judged entirely by one."""
        base = sample_df.copy()
        base["queue_category"] = "Ranked"
        base["cs_per_min"] = 1.5

        all_mid = base.copy()
        all_mid["role_label"] = "Mid"
        all_support = base.copy()
        all_support["role_label"] = "Support"

        # Deliberately lopsided (7 Mid / 1 Support). An even split would
        # score the same whether or not games-per-role weighting is applied,
        # so it couldn't detect the weighting being dropped.
        mixed = base.copy()
        mixed["role_label"] = "Mid"
        mixed.loc[mixed.index[:1], "role_label"] = "Support"

        blended = performance_radar(mixed)["Farming"]
        mid_score = performance_radar(all_mid)["Farming"]
        support_score = performance_radar(all_support)["Farming"]
        assert mid_score < blended < support_score

        # Measure *where* between the two ends the blend lands, rather than
        # comparing against the midpoint — at 7:1 the weighted answer sits
        # about 1/8 of the way (0.125) and an unweighted mean would sit at
        # 0.5, but the two happened to round close enough that a midpoint
        # comparison couldn't tell them apart.
        position = (blended - mid_score) / (support_score - mid_score)
        assert position < 0.3, f"blend sat at {position:.2f} — looks unweighted"

    def test_farming_still_rewards_more_cs_within_a_role(self, sample_df):
        base = sample_df.copy()
        base["queue_category"] = "Ranked"
        base["role_label"] = "Mid"
        low, high = base.copy(), base.copy()
        low["cs_per_min"] = 4.0
        high["cs_per_min"] = 8.5
        assert performance_radar(high)["Farming"] > performance_radar(low)["Farming"]

    def test_versatility_rewards_a_wider_pool(self, sample_df):
        narrow = sample_df.copy()
        narrow["champion"] = "Ahri"
        wide = sample_df.copy()
        wide["champion"] = [f"Champ{i}" for i in range(len(wide))]
        assert performance_radar(wide)["Versatility"] > performance_radar(narrow)["Versatility"]

    def test_scores_are_clamped_at_the_extremes(self, sample_df):
        """Absurd inputs must clamp to the 0-100 band, not overflow it."""
        extreme = sample_df.copy()
        extreme["cs_per_min"] = 999
        extreme["kill_participation"] = 100
        extreme["vision_score"] = 999
        assert all(0 <= v <= 100 for v in performance_radar(extreme).values())

    def test_uses_objective_data_when_supplied(self, sample_df):
        low = pd.DataFrame([{"dragons": 0, "barons": 0, "heralds": 0, "towers": 0}])
        high = pd.DataFrame([{"dragons": 3, "barons": 1, "heralds": 1, "towers": 4}])
        assert (performance_radar(sample_df, high)["Objectives"]
                > performance_radar(sample_df, low)["Objectives"])


class TestRadarHighlights:
    def test_picks_extremes(self):
        scores = {d: 50.0 for d in PERFORMANCE_DIMENSIONS}
        scores["Vision"] = 90.0
        scores["Farming"] = 10.0
        assert radar_highlights(scores) == ("Vision", "Farming")

    def test_all_zero_returns_none(self):
        assert radar_highlights({d: 0.0 for d in PERFORMANCE_DIMENSIONS}) == (None, None)

    def test_empty(self):
        assert radar_highlights({}) == (None, None)


class TestParseSkillLevelUps:
    def test_returns_slots_in_chronological_order(self, puuid):
        tl = make_skill_timeline([(1000, 1), (3000, 2), (2000, 3)], puuid=puuid)
        assert parse_skill_level_ups(tl, puuid) == [1, 3, 2]

    def test_deduplicates_the_known_riot_bug(self, puuid):
        """Riot issue #1100: identical participantId/skillSlot/timestamp
        events appear twice. They must collapse to one, not be counted twice."""
        tl = make_skill_timeline([(1000, 1), (1000, 1), (2000, 2)], puuid=puuid)
        assert parse_skill_level_ups(tl, puuid) == [1, 2]

    def test_rejects_impossible_totals(self, puuid):
        """Beyond de-duplication, a game reporting more skill points than the
        game allows is corrupt — return None so the caller can exclude it
        rather than chart a wrong order. (The per-slot caps are what actually
        reject this; 5+5+5+3 = 18 means an over-total can't happen without
        also breaking a per-slot cap.)"""
        events = [(i * 1000, 1 + (i % 3)) for i in range(MAX_SKILL_POINTS + 5)]
        assert parse_skill_level_ups(make_skill_timeline(events, puuid=puuid), puuid) is None

    def test_accepts_a_full_legal_game(self, puuid):
        """The upper bound must be *inclusive* — a maxed-out 18-point game is
        legal and must not be thrown away by an off-by-one."""
        events = []
        ts = 0
        for slot, count in [(1, 5), (2, 5), (3, 5), (4, 3)]:
            for _ in range(count):
                ts += 1000
                events.append((ts, slot))
        result = parse_skill_level_ups(make_skill_timeline(events, puuid=puuid), puuid)
        assert result is not None
        assert len(result) == MAX_SKILL_POINTS

    def test_rejects_too_many_points_in_one_basic_skill(self, puuid):
        events = [(i * 1000, 1) for i in range(6)]     # 6 points in Q, max is 5
        assert parse_skill_level_ups(make_skill_timeline(events, puuid=puuid), puuid) is None

    def test_rejects_too_many_ult_points(self, puuid):
        events = [(i * 1000, 4) for i in range(4)]     # 4 ult points, max is 3
        assert parse_skill_level_ups(make_skill_timeline(events, puuid=puuid), puuid) is None

    def test_ignores_other_participants(self, puuid):
        tl = make_skill_timeline([(1000, 1), (2000, 2, 7), (3000, 3)], puuid=puuid)
        assert parse_skill_level_ups(tl, puuid) == [1, 3]

    def test_unknown_player_returns_none(self, puuid):
        assert parse_skill_level_ups(make_skill_timeline([(1000, 1)], puuid=puuid), "nobody") is None

    def test_no_events_returns_none(self, puuid):
        assert parse_skill_level_ups(make_skill_timeline([], puuid=puuid), puuid) is None

    def test_ignores_invalid_skill_slots(self, puuid):
        tl = make_skill_timeline([(1000, 1), (2000, 9)], puuid=puuid)
        assert parse_skill_level_ups(tl, puuid) == [1]


class TestSkillMaxOrder:
    def test_order_follows_which_hit_five_first(self):
        slots = [1] * 5 + [3] * 5 + [2] * 5
        assert skill_max_order(slots) == ("Q", "E", "W")

    def test_ult_points_do_not_appear_in_max_order(self):
        slots = [1] * 5 + [4, 4, 4] + [2] * 5
        assert skill_max_order(slots) == ("Q", "W")

    def test_short_game_falls_back_to_points_invested(self):
        """A game that ended before anything was maxed still has a priority,
        so it contributes rather than being dropped."""
        assert skill_max_order([1, 1, 1, 2]) == ("Q", "W")

    def test_empty_and_none(self):
        assert skill_max_order([]) == ()
        assert skill_max_order(None) == ()


class TestSkillOrderWinRate:
    def test_groups_by_path(self):
        df = pd.DataFrame([
            {"match_id": "m1", "champion": "Ahri", "win": True},
            {"match_id": "m2", "champion": "Ahri", "win": True},
            {"match_id": "m3", "champion": "Ahri", "win": False},
        ])
        orders = {"m1": ("Q", "E", "W"), "m2": ("Q", "E", "W"), "m3": ("W", "Q", "E")}
        result = skill_order_win_rate(df, orders, "Ahri")
        top = result.iloc[0]
        assert top["games"] == 2 and top["win_rate"] == 100.0

    def test_excludes_games_without_data(self):
        df = pd.DataFrame([
            {"match_id": "m1", "champion": "Ahri", "win": True},
            {"match_id": "m2", "champion": "Ahri", "win": False},
        ])
        result = skill_order_win_rate(df, {"m1": ("Q", "W", "E")}, "Ahri")
        assert result["games"].sum() == 1

    def test_only_tuples_count(self):
        """Same guard as opening builds — a list must not slip through and
        blow up the groupby with an unhashable key."""
        df = pd.DataFrame([{"match_id": "m1", "champion": "Ahri", "win": True}])
        assert skill_order_win_rate(df, {"m1": ["Q", "W"]}, "Ahri").empty

    def test_scoped_to_champion(self):
        df = pd.DataFrame([
            {"match_id": "m1", "champion": "Ahri", "win": True},
            {"match_id": "m2", "champion": "Shaco", "win": False},
        ])
        orders = {"m1": ("Q", "W", "E"), "m2": ("Q", "W", "E")}
        assert skill_order_win_rate(df, orders, "Ahri").iloc[0]["games"] == 1

    def test_empty_orders(self):
        df = pd.DataFrame([{"match_id": "m1", "champion": "Ahri", "win": True}])
        assert skill_order_win_rate(df, {}, "Ahri").empty
