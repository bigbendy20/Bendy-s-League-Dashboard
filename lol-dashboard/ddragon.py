"""
Helpers for Riot's Data Dragon CDN — free, keyless static asset host for
champion icons/splash art. `championName` from the match-v5 API already
matches Data Dragon's internal champion id (e.g. "MonkeyKing" for Wukong,
"KSante", "Kaisa"), so no name-mapping table is needed.
"""
import requests

FALLBACK_VERSION = "14.15.1"  # used only if the version lookup below fails


def get_latest_version() -> str:
    try:
        resp = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5)
        resp.raise_for_status()
        versions = resp.json()
        return versions[0]
    except Exception:
        return FALLBACK_VERSION


def champion_icon_url(champion: str, version: str) -> str:
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion}.png"


def champion_splash_url(champion: str, skin_num: int = 0) -> str:
    # Splash art isn't versioned in the CDN path.
    return f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{champion}_{skin_num}.jpg"


def site_icon_url(icon_id, champion: str, version: str) -> str:
    """The circle at the top of the site: your League profile icon if Riot
    gave us one, champion art otherwise.

    A tiny decision, but it lived inline in app.py where no test could reach
    it — mutating it to always take the fallback branch broke nothing the
    suite could see. `icon_id is None` rather than a truth test on purpose:
    icon id 0 is a real, valid default icon, and `if icon_id` would silently
    discard it.
    """
    if icon_id is None:
        return champion_icon_url(champion, version)
    return profile_icon_url(icon_id, version)


def hero_icon_url(champ: str | None, site_url: str, version: str) -> str:
    """Which icon a hero banner shows.

    Pages about a champion (the deep-dive) show that champion; site-level
    pages show you. Same story as above — this was a conditional expression
    inside a Streamlit renderer, so inverting it was invisible.
    """
    return champion_icon_url(champ, version) if champ else site_url


def profile_icon_url(icon_id: int, version: str) -> str:
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/{icon_id}.png"


def item_icon_url(item_id: int, version: str) -> str:
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{item_id}.png"


def get_items(version: str) -> dict[int, dict]:
    """Full item catalog (id -> {name, tags, gold, ...}) from Data Dragon.
    Used to tell "core" items (Legendary/Mythic-tier build pieces) apart
    from consumables/trinkets/wards when summarizing build order, without
    relying on a hand-maintained, easily-stale item ID list."""
    try:
        resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/item.json", timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        return {int(item_id): item for item_id, item in data["data"].items()}
    except Exception:
        return {}


def is_core_item(item_id: int, items_data: dict[int, dict]) -> bool:
    """Best-effort filter: not a consumable/trinket/boots-enchant, and
    reasonably expensive (real build pieces, not wards/potions)."""
    item = items_data.get(item_id)
    if not item:
        return False
    tags = set(item.get("tags", []))
    if tags & {"Consumable", "Trinket"}:
        return False
    total_gold = item.get("gold", {}).get("total", 0)
    return total_gold >= 700


def get_champion_skins(champion: str, version: str) -> dict[int, str]:
    """Skin num -> skin name for one champion, via Data Dragon's per-champion
    detail file. `skinId` in match-v5 participant data is the same as this
    "num" (0 = default/classic skin)."""
    try:
        resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion/{champion}.json",
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        champ_data = data["data"][champion]
        return {skin["num"]: skin["name"] for skin in champ_data.get("skins", [])}
    except Exception:
        return {}


def rune_icon_url(icon_path: str) -> str:
    # Rune/style icon paths from runesReforged.json (e.g.
    # "perk-images/Styles/7201_Precision.png") are relative to /cdn/img/,
    # NOT versioned like most other Data Dragon assets.
    return f"https://ddragon.leagueoflegends.com/cdn/img/{icon_path}"


def get_runes(version: str) -> dict:
    """Rune/keystone lookup from Data Dragon's runesReforged.json. Returns
    {"styles": {style_id: {name, icon}}, "perks": {perk_id: {name, icon,
    style_id}}} — match-v5's `perks.styles[0].style` is the primary style id
    and `perks.styles[0].selections[0].perk` is the keystone perk id."""
    try:
        resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/runesReforged.json",
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        styles = {}
        perks = {}
        for style in data:
            style_id = style["id"]
            styles[style_id] = {"name": style["name"], "icon": style["icon"]}
            for slot in style.get("slots", []):
                for rune in slot.get("runes", []):
                    perks[rune["id"]] = {
                        "name": rune["name"],
                        "icon": rune["icon"],
                        "style_id": style_id,
                    }
        return {"styles": styles, "perks": perks}
    except Exception:
        return {"styles": {}, "perks": {}}


def get_summoner_spells(version: str) -> dict[int, dict]:
    """Summoner spell id -> {name, icon} via Data Dragon's summoner.json.
    The file is keyed by internal spell name (e.g. "SummonerFlash"); each
    entry's numeric `key` is what match-v5's summoner1Id/summoner2Id use."""
    try:
        resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/summoner.json",
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for spell in data["data"].values():
            result[int(spell["key"])] = {
                "name": spell["name"],
                "icon": f"https://ddragon.leagueoflegends.com/cdn/{version}/img/spell/{spell['image']['full']}",
            }
        return result
    except Exception:
        return {}


def get_champions(version: str) -> dict[int, dict]:
    """Numeric champion key -> {"id": "Ahri", "name": "Ahri"}.

    Needed because champion-mastery-v4 identifies champions by their numeric
    `championId`, while match-v5 (and everything else in this project) uses
    the string `championName` / Data Dragon id. This is the bridge between
    the two. Data Dragon's champion.json is keyed by the string id, with the
    numeric key stored as a *string* under "key" — hence the int() cast."""
    try:
        resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json",
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            int(champ["key"]): {"id": champ["id"], "name": champ["name"]}
            for champ in data["data"].values()
        }
    except Exception:
        return {}


def map_image_url(version: str, map_id: int = 11) -> str:
    """Minimap background image (11 = Summoner's Rift) — used behind the
    death/kill position heatmap."""
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/map/map{map_id}.png"
