"""
Match local League client replay files (.rofl) to standout games from your
match history, so you can find "which saved replay is this good game"
without digging through the Replays folder by hand.

Important limitation: there's no Riot API to download replays remotely.
This only organizes replay files the League client has *already* saved
locally — which only happens if you watched/kept the replay in-client, and
only within the roughly two-week window Riot's replay servers keep a game
available at all. Nothing here fetches anything from the network.

Replay filenames follow Riot's own convention: "{REGION}-{gameId}.rofl"
(e.g. "NA1-4972838291.rofl"). The gameId is the same number embedded in
match-v5's matchId ("NA1_4972838291"), so filename matching is exact —
not a fuzzy timestamp guess.
"""
import os
import re
import shutil
from pathlib import Path


def default_replay_folder() -> Path:
    """League's replay folder location isn't configurable in most client
    versions — this is it, on Windows."""
    return Path.home() / "Documents" / "League of Legends" / "Replays"


def get_replay_folder() -> Path:
    override = os.getenv("REPLAY_FOLDER", "").strip()
    return Path(override) if override else default_replay_folder()


def list_local_replays(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob("*.rofl"))


def _game_id_from_match_id(match_id: str) -> str | None:
    # match-v5 matchId is "{platform}_{gameId}", e.g. "NA1_4972838291".
    parts = match_id.split("_", 1)
    return parts[1] if len(parts) == 2 else None


def _game_id_from_filename(path: Path) -> str | None:
    # "NA1-4972838291.rofl" -> "4972838291". Falls back to any long digit
    # run in the filename in case naming ever differs across client versions.
    stem = path.stem
    parts = stem.split("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[1]
    digits = re.findall(r"\d{6,}", stem)
    return digits[0] if digits else None


def match_replays_to_games(games: list[dict], replay_folder: Path) -> list[dict]:
    """`games` is a list of dicts, each needing at least a "match_id" key.
    Returns the same list with a "replay_path" (Path or None) added to each."""
    replay_by_game_id = {}
    for path in list_local_replays(replay_folder):
        gid = _game_id_from_filename(path)
        if gid:
            replay_by_game_id[gid] = path

    results = []
    for game in games:
        match_id = game.get("match_id")
        game_id = _game_id_from_match_id(match_id) if match_id else None
        results.append({**game, "replay_path": replay_by_game_id.get(game_id) if game_id else None})
    return results


def copy_matched_replays(matched: list[dict], dest_folder: Path) -> list[dict]:
    """Copies every matched replay into `dest_folder`, renamed to something
    descriptive (date + champion + reason) instead of a bare gameId. Returns
    the same list with a "copied_to" path added (None if there was nothing
    to copy or the copy failed)."""
    dest_folder.mkdir(parents=True, exist_ok=True)
    results = []
    for game in matched:
        entry = dict(game)
        replay_path = game.get("replay_path")
        if replay_path is None:
            entry["copied_to"] = None
            results.append(entry)
            continue
        safe_label = re.sub(r"[^A-Za-z0-9]+", "_", game.get("label", "game")).strip("_")
        safe_champ = re.sub(r"[^A-Za-z0-9]+", "_", game.get("champion", "") or "").strip("_")
        date_str = game.get("date_str", "")
        filename = "_".join(p for p in [date_str, safe_champ, safe_label] if p) + ".rofl"
        dest_path = dest_folder / filename
        try:
            shutil.copy2(replay_path, dest_path)
            entry["copied_to"] = dest_path
        except Exception:
            entry["copied_to"] = None
        results.append(entry)
    return results
