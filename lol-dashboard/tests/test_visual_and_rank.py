"""Visual identity (tier colors, text contrast) and climb-goal rank math."""
import pandas as pd

import rank_history as rh
from themes import (
    CONTRAST_TARGET,
    DARK_SURFACE,
    DEFAULT_ACCENT,
    LIGHT_SURFACE,
    TIER_COLORS,
    contrast_ratio,
    get_tier_colors,
    hex_to_rgb,
    readable_accents,
    readable_on,
)

ALL_PAIRS = list(TIER_COLORS.items()) + [("DEFAULT", DEFAULT_ACCENT)]


class TestTierColors:
    def test_every_tier_has_a_valid_pair(self):
        for tier, (a1, a2) in TIER_COLORS.items():
            for color in (a1, a2):
                assert color.startswith("#") and len(color) == 7, f"{tier}: {color}"
                int(color[1:], 16)          # raises if not valid hex

    def test_lookup_is_case_insensitive(self):
        assert get_tier_colors("gold") == get_tier_colors("GOLD")

    def test_unranked_and_unknown_fall_back_to_default(self):
        assert get_tier_colors(None) == DEFAULT_ACCENT
        assert get_tier_colors("") == DEFAULT_ACCENT
        assert get_tier_colors("NOT_A_TIER") == DEFAULT_ACCENT

    def test_hex_to_rgb(self):
        assert hex_to_rgb("#FFFFFF") == "255,255,255"
        assert hex_to_rgb("000000") == "0,0,0"      # tolerates missing '#'


class TestContrast:
    def test_known_ratios(self):
        """Sanity-check the formula against values with known answers."""
        assert round(contrast_ratio("#FFFFFF", "#000000"), 1) == 21.0
        assert round(contrast_ratio("#FFFFFF", "#FFFFFF"), 1) == 1.0

    def test_symmetric(self):
        assert contrast_ratio("#123456", "#ABCDEF") == contrast_ratio("#ABCDEF", "#123456")

    def test_every_accent_is_readable_in_dark_mode(self):
        """The regression this guards: raw tier accents used as text. Iron
        measured 3.33:1 on the dark surface before the fix."""
        for tier, (a1, a2) in ALL_PAIRS:
            t1, t2 = readable_accents(a1, a2, dark_mode=True)
            for color in (t1, t2):
                ratio = contrast_ratio(color, DARK_SURFACE)
                assert ratio >= CONTRAST_TARGET, f"{tier} dark: {color} = {ratio:.2f}"

    def test_every_accent_is_readable_in_light_mode(self):
        """Light mode was the worse offender — Challenger measured 1.87:1."""
        for tier, (a1, a2) in ALL_PAIRS:
            t1, t2 = readable_accents(a1, a2, dark_mode=False)
            for color in (t1, t2):
                ratio = contrast_ratio(color, LIGHT_SURFACE)
                assert ratio >= CONTRAST_TARGET, f"{tier} light: {color} = {ratio:.2f}"

    def test_already_readable_colors_are_left_alone(self):
        """No pointless mangling of colors that already pass."""
        assert readable_on("#FFFFFF", DARK_SURFACE) == "#FFFFFF"
        assert readable_on("#000000", LIGHT_SURFACE) == "#000000"

    def test_adjustment_preserves_hue_direction(self):
        """Gold must still read as gold after darkening for light mode —
        red channel should stay the dominant one."""
        adjusted = readable_on("#D4AF37", LIGHT_SURFACE)
        r, g, b = (int(adjusted[i:i + 2], 16) for i in (1, 3, 5))
        assert r > b and g > b

    def test_returns_valid_hex(self):
        for _, (a1, a2) in ALL_PAIRS:
            for dark in (True, False):
                for color in readable_accents(a1, a2, dark):
                    assert len(color) == 7 and color.startswith("#")
                    int(color[1:], 16)


class TestClimbValue:
    def test_promotion_counts_as_a_gain(self):
        """The whole reason this ordinal exists: LP resets to 0 on promotion,
        so raw LP would read a promotion as a 90-point loss."""
        before = rh.climb_value("GOLD", "IV", 90)
        after = rh.climb_value("GOLD", "III", 0)
        assert after > before

    def test_tiers_are_ordered(self):
        values = [rh.climb_value(t, "IV", 0) for t in rh.TIER_ORDER]
        assert values == sorted(values)

    def test_divisions_are_ordered_within_a_tier(self):
        values = [rh.climb_value("GOLD", d, 0) for d in ["IV", "III", "II", "I"]]
        assert values == sorted(values)

    def test_lp_increases_value_within_a_division(self):
        assert rh.climb_value("GOLD", "IV", 50) > rh.climb_value("GOLD", "IV", 10)

    def test_apex_tiers_ignore_division(self):
        """Master+ has no divisions; LP is the only differentiator."""
        assert rh.climb_value("MASTER", None, 100) == rh.climb_value("MASTER", "I", 100)
        assert rh.climb_value("CHALLENGER", None, 500) > rh.climb_value("CHALLENGER", None, 100)

    def test_unranked_and_garbage_return_none(self):
        assert rh.climb_value(None, None, 0) is None
        assert rh.climb_value("NOT_A_TIER", "IV", 0) is None

    def test_case_insensitive(self):
        assert rh.climb_value("gold", "iv", 20) == rh.climb_value("GOLD", "IV", 20)

    def test_missing_lp_treated_as_zero(self):
        assert rh.climb_value("GOLD", "IV", None) == rh.climb_value("GOLD", "IV", 0)


class TestLpGainRate:
    def _hist(self, entries):
        return pd.DataFrame(entries)

    def test_positive_rate_when_climbing(self):
        now = pd.Timestamp.utcnow()
        hist = self._hist([
            {"timestamp": now - pd.Timedelta(days=4), "tier": "SILVER", "rank": "I", "league_points": 40},
            {"timestamp": now, "tier": "GOLD", "rank": "IV", "league_points": 20},
        ])
        assert rh.lp_gain_rate(hist) > 0

    def test_negative_rate_when_falling(self):
        now = pd.Timestamp.utcnow()
        hist = self._hist([
            {"timestamp": now - pd.Timedelta(days=4), "tier": "GOLD", "rank": "IV", "league_points": 60},
            {"timestamp": now, "tier": "SILVER", "rank": "I", "league_points": 30},
        ])
        assert rh.lp_gain_rate(hist) < 0

    def test_none_when_not_enough_history(self):
        """No fabricated estimate from a single data point."""
        assert rh.lp_gain_rate(pd.DataFrame()) is None
        assert rh.lp_gain_rate(self._hist([
            {"timestamp": pd.Timestamp.utcnow(), "tier": "GOLD", "rank": "IV", "league_points": 0}
        ])) is None

    def test_falls_back_to_full_history_when_window_is_sparse(self):
        """Snapshots older than the window shouldn't yield None just because
        nothing landed inside it — the fallback keeps an estimate available."""
        now = pd.Timestamp.utcnow()
        hist = self._hist([
            {"timestamp": now - pd.Timedelta(days=90), "tier": "SILVER", "rank": "IV", "league_points": 0},
            {"timestamp": now - pd.Timedelta(days=60), "tier": "GOLD", "rank": "IV", "league_points": 0},
        ])
        assert rh.lp_gain_rate(hist, window_days=7) is not None

    def test_unranked_rows_do_not_crash_it(self):
        now = pd.Timestamp.utcnow()
        hist = self._hist([
            {"timestamp": now - pd.Timedelta(days=2), "tier": None, "rank": None, "league_points": None},
            {"timestamp": now, "tier": "GOLD", "rank": "IV", "league_points": 20},
        ])
        assert rh.lp_gain_rate(hist) is None      # only one usable point
