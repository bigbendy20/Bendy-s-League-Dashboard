"""
Live-game role inference and the tips built on it.

The hard part isn't the arithmetic, it's not overclaiming. spectator-v5
publishes no positions, so roles are guessed; live matchup samples are tiny,
so most "findings" are coin flips. These tests mostly pin the places where
the code is supposed to admit that.
"""
import datetime

import pandas as pd

import live_tips
import stats


def _history(rows):
    """rows: (champion, role, opponent, win, allies, enemies)."""
    base = datetime.datetime(2026, 1, 5, 12)
    return pd.DataFrame([{
        "queue_id": 420,
        "queue_category": "Ranked",
        "game_creation": pd.Timestamp(base + datetime.timedelta(hours=i)),
        "champion": champ,
        "role_label": role,
        "opponent_champion": opp,
        "win": win,
        "ally_champions": tuple(allies),
        "enemy_champions": tuple(enemies),
    } for i, (champ, role, opp, win, allies, enemies) in enumerate(rows)])


class TestRolePriors:
    def test_learns_from_your_own_games(self):
        df = _history([("Shaco", "Jungle", "Vi", True, (), ())] * 4)
        assert live_tips.champion_role_priors(df)["Shaco"] == {"Jungle": 4}

    def test_learns_from_lane_opponents_too(self):
        """The only reason this knows anything about champions you never
        play: your lane opponent was, by definition, in your role."""
        df = _history([("Shaco", "Jungle", "Vi", True, (), ())] * 4)
        assert live_tips.champion_role_priors(df)["Vi"] == {"Jungle": 4}

    def test_ignores_non_lane_roles(self):
        """ARAM rows carry role_label 'ARAM', which isn't a position."""
        df = _history([("Sona", "ARAM", None, True, (), ())])
        assert "Sona" not in live_tips.champion_role_priors(df)

    def test_empty_history_gives_empty_priors(self):
        assert live_tips.champion_role_priors(pd.DataFrame()) == {}


class TestRoleInference:
    PRIORS = {
        "Shaco": {"Jungle": 196, "Support": 6, "Mid": 6, "Top": 5},
        "Ahri": {"Mid": 16},
        "Thresh": {"Support": 9},
        "Jinx": {"Bot": 12},
        "Darius": {"Top": 7},
    }

    def _team(self, spec):
        return [{"puuid": f"p{i}", "champion": c, "spells": s}
                for i, (c, s) in enumerate(spec)]

    def test_smite_is_certain(self):
        """Verified against 599 real games: 397 with Smite, all jungle;
        202 without, none jungle."""
        team = self._team([("Ahri", (4, live_tips.SMITE_ID))])
        assert live_tips.infer_roles(team, self.PRIORS)["p0"] == ("Jungle", "certain")

    def test_smite_beats_a_strong_champion_prior(self):
        """Ahri is 16-0 mid in the priors, but she has Smite. The spell wins:
        it's the only signal here that's ever been 100% accurate."""
        team = self._team([("Ahri", (4, live_tips.SMITE_ID)), ("Shaco", (4, 14))])
        roles = live_tips.infer_roles(team, self.PRIORS)
        assert roles["p0"][0] == "Jungle"
        assert roles["p1"][0] != "Jungle", "jungle was already claimed"

    def test_full_team_gets_five_distinct_roles(self):
        team = self._team([
            ("Shaco", (4, live_tips.SMITE_ID)), ("Ahri", (4, 14)),
            ("Thresh", (4, 3)), ("Jinx", (4, 7)), ("Darius", (4, 12)),
        ])
        roles = live_tips.infer_roles(team, self.PRIORS)
        assigned = [r for r, _ in roles.values()]
        assert sorted(assigned) == sorted(live_tips.LANE_ORDER)
        assert len(live_tips.LANE_ORDER) == 5, "ROLE_ORDER also holds ARAM/Arena/Unknown"

    def test_leftover_assignment_fills_the_five_positions(self):
        """With no priors at all, everyone falls through to leftover
        assignment — a branch the five-strong-priors test never reaches."""
        team = self._team([(f"Unknown{i}", (4, 14)) for i in range(5)])
        assigned = [r for r, _ in live_tips.infer_roles(team, {}).values()]
        assert sorted(assigned) == sorted(live_tips.LANE_ORDER)

    def test_never_assigns_a_non_position_role(self):
        """The invariant, tested where it can actually break.

        Leftover roles are drawn from LANE_ORDER, not ROLE_ORDER — the latter
        also holds "ARAM", "Arena" and "Unknown". With exactly five players
        this is invisible, because ROLE_ORDER's first five entries *are* the
        lane roles, which is why the obvious test passed against both
        versions. A sixth player is what separates them: ROLE_ORDER hands
        them "ARAM", LANE_ORDER correctly runs out and returns None.

        Six-player teams shouldn't happen, but "shouldn't happen" is a
        statement about Riot's payload, not a guarantee, and the invariant is
        worth holding regardless.
        """
        team = self._team([(f"Unknown{i}", (4, 14)) for i in range(6)])
        assigned = [r for r, _ in live_tips.infer_roles(team, {}).values()]
        assert all(r is None or r in live_tips.LANE_ORDER for r in assigned)
        assert not {"ARAM", "Arena", "Unknown"} & set(assigned)

    def test_unknown_champion_is_marked_a_guess(self):
        """A champion with no history must not be reported confidently."""
        team = self._team([("Briar", (4, 14))])
        role, confidence = live_tips.infer_roles(team, self.PRIORS)["p0"]
        assert confidence == "guess"

    def test_thin_prior_does_not_count_as_evidence(self):
        """Seen once is an accident, not a pattern."""
        priors = {"Zed": {"Mid": 1}}
        _, confidence = live_tips.infer_roles(
            self._team([("Zed", (4, 14))]), priors)["p0"]
        assert confidence == "guess"

    def test_strongest_prior_claims_its_role_first(self):
        """Two champions want Mid; the one with more evidence gets it."""
        priors = {"Ahri": {"Mid": 40}, "Zed": {"Mid": 5}}
        roles = live_tips.infer_roles(
            self._team([("Zed", (4, 14)), ("Ahri", (4, 14))]), priors)
        assert roles["p1"][0] == "Mid"
        assert roles["p0"][0] != "Mid"


class TestChampionTip:
    HISTORY = _history(
        [("Shaco", "Jungle", "Vi", True, (), ())] * 40
        + [("Shaco", "Jungle", "Vi", False, (), ())] * 10
        + [("Ahri", "Mid", "Zed", False, (), ())] * 50
    )

    def test_reports_the_record(self):
        tip = live_tips.champion_tip(self.HISTORY, "Shaco", "Jungle")
        assert "80%" in tip["text"] and "50 games" in tip["text"]

    def test_a_real_edge_is_not_marked_weak(self):
        """80% over 50 games against 0% over 50 is not a coin flip. If this
        ever flips to weak, the significance test has broken."""
        baseline = (50, 0)
        tip = live_tips.champion_tip(self.HISTORY, "Shaco", "Jungle", baseline)
        assert tip["weak"] is False

    def test_unplayed_champion_says_so(self):
        tip = live_tips.champion_tip(self.HISTORY, "Teemo", "Top")
        assert "No history" in tip["text"]
        assert tip["games"] == 0

    def test_role_scoping_is_skipped_when_it_would_gut_the_sample(self):
        """Narrowing 50 games to 1 trades a real number for a noisy one.
        The tip should stay at champion scope and say 'overall'."""
        tip = live_tips.champion_tip(self.HISTORY, "Shaco", "Support")
        assert "overall" in tip["text"]
        assert tip["games"] == 50


class TestMatchupTip:
    def test_thin_matchup_refuses_to_conclude(self):
        history = _history([("Shaco", "Jungle", "Vi", True, (), ())] * 2)
        tip = live_tips.matchup_tip(history, "Shaco", "Vi", "Jungle")
        assert "not enough" in tip["text"]

    def test_singular_grammar_on_one_game(self):
        history = _history([("Shaco", "Jungle", "Vi", True, (), ())])
        assert "1 time —" in live_tips.matchup_tip(history, "Shaco", "Vi", "Jungle")["text"]

    def test_reports_a_usable_matchup(self):
        history = _history([("Shaco", "Jungle", "Vi", True, (), ())] * 8
                           + [("Shaco", "Jungle", "Vi", False, (), ())] * 2)
        tip = live_tips.matchup_tip(history, "Shaco", "Vi", "Jungle")
        assert "80%" in tip["text"] and "10 games" in tip["text"]

    def test_missing_opponent_returns_nothing(self):
        history = _history([("Shaco", "Jungle", "Vi", True, (), ())] * 8)
        assert live_tips.matchup_tip(history, "Shaco", None, "Jungle") is None


class TestBaselineIsTheComplement:
    """A subset compared against a superset containing it is biased toward
    finding nothing. This was a real bug: 188 Shaco games are a third of the
    history, so they dragged the 'overall' baseline toward themselves.
    """

    def test_split_returns_disjoint_halves(self):
        df = _history([("Shaco", "Jungle", "Vi", True, (), ())] * 3
                      + [("Ahri", "Mid", "Zed", False, (), ())] * 7)
        inside, outside = live_tips._split(df, df["champion"] == "Shaco")
        assert inside == (3, 3)
        assert outside == (7, 0)

    def test_complement_detects_an_edge_a_superset_baseline_hides(self):
        """The concrete case, in miniature: identical numbers, and only the
        complement comparison reaches significance."""
        wins_in, games_in = 108, 188
        wins_all, games_all = 298, 599
        wins_out, games_out = wins_all - wins_in, games_all - games_in
        assert not stats.separated(wins_in, games_in, wins_all, games_all)
        assert stats.separated(wins_in, games_in, wins_out, games_out)


class TestTeamTips:
    HISTORY = _history(
        [("Shaco", "Jungle", "Vi", False, (), ("Yasuo", "Lux"))] * 12
        + [("Shaco", "Jungle", "Vi", True, (), ("Garen", "Lux"))] * 12
    )

    def test_enemy_tip_fires_on_a_separable_gap(self):
        tips = live_tips.enemy_tips(self.HISTORY, ("Yasuo",), (24, 12))
        assert tips and "Yasuo" in tips[0]["text"]

    def test_enemy_tip_carries_the_confounding_caveat(self):
        tips = live_tips.enemy_tips(self.HISTORY, ("Yasuo",), (24, 12))
        assert "necessarily" in tips[0]["note"]

    def test_champion_present_in_every_game_is_not_a_signal(self):
        """Lux is on the enemy team in all 24 games, so the complement is
        empty and there is nothing to compare against."""
        assert live_tips.enemy_tips(self.HISTORY, ("Lux",), (24, 12)) == []

    def test_unseen_champion_produces_nothing(self):
        assert live_tips.enemy_tips(self.HISTORY, ("Teemo",), (24, 12)) == []


class TestBuildLiveTips:
    def test_orders_specific_before_diffuse(self):
        history = _history(
            [("Shaco", "Jungle", "Vi", True, ("Ahri",), ("Vi", "Yasuo"))] * 20
            + [("Shaco", "Jungle", "Nocturne", False, ("Ahri",), ("Nocturne",))] * 10
        )
        tips = live_tips.build_live_tips(
            history, "Shaco", "Jungle", "Vi", ("Ahri",), ("Vi", "Yasuo"))
        assert "Shaco jungle" in tips[0]["text"]
        assert "into Vi" in tips[1]["text"]

    def test_empty_history_yields_no_tips(self):
        assert live_tips.build_live_tips(
            pd.DataFrame(), "Shaco", "Jungle", "Vi", (), ()) == []

    def test_every_tip_carries_its_sample(self):
        history = _history([("Shaco", "Jungle", "Vi", True, (), ())] * 20)
        for tip in live_tips.build_live_tips(
                history, "Shaco", "Jungle", "Vi", (), ()):
            assert "games" in tip and "margin" in tip and "weak" in tip
