"""
Riot's API only exposes your *current* rank/LP, not a per-game history of
LP gains and losses. To get a real trend line, we snapshot your rank every
time the dashboard fetches and append it to a local log — the trend builds
itself up the more you use the app. There's no way to backfill history from
before the log started; that's a Riot API limitation, not something we can
work around.
"""
import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
RANK_HISTORY_PATH = DATA_DIR / "rank_history.json"


def log_snapshot(league_entries: list[dict]) -> None:
    """Append a timestamped snapshot of each queue's current standing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history = _load_raw()
    timestamp = pd.Timestamp.utcnow().isoformat()

    for entry in league_entries:
        history.append(
            {
                "timestamp": timestamp,
                "queue_type": entry.get("queueType"),
                "tier": entry.get("tier"),
                "rank": entry.get("rank"),
                "league_points": entry.get("leaguePoints"),
                "wins": entry.get("wins"),
                "losses": entry.get("losses"),
            }
        )

    with open(RANK_HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)


def _load_raw() -> list[dict]:
    if not RANK_HISTORY_PATH.exists():
        return []
    with open(RANK_HISTORY_PATH, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def load_history(queue_type: str | None = None) -> pd.DataFrame:
    raw = _load_raw()
    if not raw:
        return pd.DataFrame(
            columns=["timestamp", "queue_type", "tier", "rank", "league_points", "wins", "losses"]
        )
    df = pd.DataFrame(raw)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if queue_type:
        df = df[df["queue_type"] == queue_type]
    return df.sort_values("timestamp").reset_index(drop=True)


# ==================== Climb goal tracking ====================
# Riot's API gives current tier/division/LP, not a continuous "rank score" —
# and raw LP alone isn't comparable across a promotion (Gold IV 90 LP ->
# Gold III 0 LP is a *gain*, even though the LP number drops). climb_value()
# converts tier+division+LP into one monotonically-increasing number so
# gains/losses can be measured across promotions, for the goal tracker.
TIER_ORDER = [
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
    "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
]
DIVISION_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3}
APEX_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}


def climb_value(tier: str | None, rank: str | None, league_points) -> float | None:
    """Not an official Riot metric — just a reasonable ordinal for trend
    math. Returns None for an unrecognized/missing tier (unranked)."""
    if not tier:
        return None
    tier = tier.upper()
    if tier not in TIER_ORDER:
        return None
    lp = league_points or 0
    if tier in APEX_TIERS:
        return TIER_ORDER.index(tier) * 400 + lp
    division = DIVISION_ORDER.get((rank or "IV").upper(), 0)
    return TIER_ORDER.index(tier) * 400 + division * 100 + lp


def lp_since_last_check(lp_hist: pd.DataFrame) -> dict | None:
    """Change since the previous recorded snapshot — "you've gained 18 LP
    since you last opened this".

    Uses climb_value rather than raw LP so a promotion reads as a gain
    instead of a ~90 point drop. Returns None when there's nothing to
    compare against yet (first ever refresh), rather than a misleading zero.
    """
    if lp_hist is None or len(lp_hist) < 2:
        return None
    ordered = lp_hist.sort_values("timestamp")
    prev, curr = ordered.iloc[-2], ordered.iloc[-1]
    before = climb_value(prev["tier"], prev["rank"], prev["league_points"])
    after = climb_value(curr["tier"], curr["rank"], curr["league_points"])
    if before is None or after is None:
        return None
    return {
        "delta": int(after - before),
        "since": prev["timestamp"],
        "promoted": (prev["tier"], prev["rank"]) != (curr["tier"], curr["rank"]),
        "tier": curr["tier"],
        "rank": curr["rank"],
    }


def lp_gain_rate(lp_hist: pd.DataFrame, window_days: int = 7) -> float | None:
    """Average climb-value gained per day over the trailing window, from
    snapshots already logged on every refresh. Coarse — one snapshot per
    refresh, not per game, since Riot's API has no per-game LP delta — but
    the best signal available without a paid third-party LP tracker.
    Returns None if there isn't enough tracked history yet to say anything."""
    if lp_hist.empty or len(lp_hist) < 2:
        return None
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=window_days)
    recent = lp_hist[lp_hist["timestamp"] >= cutoff]
    if len(recent) < 2:
        recent = lp_hist  # window too sparse — fall back to full tracked history
    recent = recent.copy()
    recent["climb_value"] = recent.apply(
        lambda r: climb_value(r["tier"], r["rank"], r["league_points"]), axis=1
    )
    recent = recent.dropna(subset=["climb_value"]).sort_values("timestamp")
    if len(recent) < 2:
        return None
    span_days = (recent["timestamp"].iloc[-1] - recent["timestamp"].iloc[0]).total_seconds() / 86400
    if span_days <= 0:
        return None
    value_change = recent["climb_value"].iloc[-1] - recent["climb_value"].iloc[0]
    return value_change / span_days
