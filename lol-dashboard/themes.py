"""
Visual identity for the dashboard.

Accent colors derive from your current ranked tier — a meaningful signal
that changes as you actually climb, instead of a random reshuffle every
session. Hero art is a single champion you pick once in Settings (see
HERO_CHAMPION in app.py) rather than auto-rotated. Typography is
standardized sitewide in app.py (not per-theme) for a consistent,
professional look.

This replaces the old one-champion-one-skin THEMES system from an earlier
round — that system was fun but worked against a consistent, "polished
tool" feel: 16 different display fonts and a new accent/art combo every
time you opened the app.
"""

def hex_to_rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"{r},{g},{b}"


# (accent, accent2) per ranked tier. These are my own approximation of each
# tier's well-known in-client emblem palette (dark gunmetal iron, copper
# bronze, steel silver, gold, teal platinum, green emerald, blue diamond,
# purple master, red grandmaster, cyan-gold challenger) — Riot doesn't
# publish official hex codes for these anywhere I could find via search, so
# treat these as tasteful approximations, not exact brand colors.
TIER_COLORS = {
    "IRON": ("#6E6B66", "#8B5A44"),
    "BRONZE": ("#A9784F", "#7A5230"),
    "SILVER": ("#9FB2BC", "#65767F"),
    "GOLD": ("#D4AF37", "#8A6C1F"),
    "PLATINUM": ("#3FBFAE", "#1F7A70"),
    "EMERALD": ("#2ECC71", "#1B7A43"),
    "DIAMOND": ("#4FA9F0", "#2A5F94"),
    "MASTER": ("#A855F7", "#6D28D9"),
    "GRANDMASTER": ("#EF4444", "#9F1616"),
    "CHALLENGER": ("#29D3C7", "#F4C542"),
}

# Used before rank data has loaded (onboarding form, first paint of a
# session) and for accounts with no ranked solo/duo data at all
# (unranked / off-season) — a clean neutral indigo/cyan pair rather than
# defaulting to any one tier's color.
DEFAULT_ACCENT = ("#6366F1", "#22D3EE")


def get_tier_colors(tier: str | None) -> tuple[str, str]:
    if not tier:
        return DEFAULT_ACCENT
    return TIER_COLORS.get(tier.upper(), DEFAULT_ACCENT)


# ==================== Readable text variants ====================
# The tier accents above are chosen to look right as *decoration* — borders,
# glows, chart lines — where contrast against the page barely matters. Used
# directly as TEXT they're a problem: the muted low tiers (Iron especially)
# are thin on the dark surface, and the bright saturated mid tiers (Gold,
# Emerald, Challenger...) wash out badly on the light one, measuring as low
# as 1.9:1 against a light card. So text gets a contrast-corrected variant
# while decoration keeps the raw color.
#
# Reference surfaces are the real ones accent text sits on in
# render_theme_css(): the translucent card over the near-black background in
# dark mode, and the light overlay in light mode.
DARK_SURFACE = "#181820"
LIGHT_SURFACE = "#F0F0F4"
# 4.5:1 is WCAG AA for normal text. Most accent text here (metric values,
# hero chips) is large and bold, where 3:1 would technically pass — aiming
# for 4.5 anyway costs nothing and leaves margin for the translucent
# surfaces, whose effective background shifts with the splash art behind it.
CONTRAST_TARGET = 4.5


def _to_rgb_tuple(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02X}" for c in rgb)


def _relative_luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _to_rgb_tuple(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two hex colors (1.0 to 21.0)."""
    lf, lb = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(hex_color: str, toward: tuple[int, int, int], amount: float) -> str:
    r, g, b = _to_rgb_tuple(hex_color)
    return _to_hex((
        r + (toward[0] - r) * amount,
        g + (toward[1] - g) * amount,
        b + (toward[2] - b) * amount,
    ))


def readable_on(hex_color: str, bg_hex: str, target: float = CONTRAST_TARGET) -> str:
    """Nudge `hex_color` toward white or black — whichever direction the
    background calls for — until it clears `target` contrast against it.

    Mixing toward pure white/black rather than doing proper HSL lightness
    math does desaturate slightly at large adjustments, but it preserves hue
    well enough that a Gold tier still reads gold, and it can't overshoot
    into a different color the way naive HSL clamping sometimes does.
    Returns the original untouched when it already passes, so most tiers in
    dark mode (the common case) are unaffected."""
    if contrast_ratio(hex_color, bg_hex) >= target:
        return hex_color
    # Dark background -> lighten the text; light background -> darken it.
    toward = (255, 255, 255) if _relative_luminance(bg_hex) < 0.5 else (0, 0, 0)
    candidate = hex_color
    for step in range(1, 51):  # up to 100% in 2% increments
        candidate = _mix(hex_color, toward, step * 0.02)
        if contrast_ratio(candidate, bg_hex) >= target:
            return candidate
    return candidate  # fully mixed; best available


def readable_accents(accent: str, accent2: str, dark_mode: bool) -> tuple[str, str]:
    """Text-safe versions of an accent pair for the current mode."""
    surface = DARK_SURFACE if dark_mode else LIGHT_SURFACE
    return readable_on(accent, surface), readable_on(accent2, surface)
