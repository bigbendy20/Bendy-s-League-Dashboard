"""
Renders a shareable "season recap" PNG summarizing headline stats — built
with PIL (already a Streamlit dependency) so there's no extra heavy
library involved. Runs client-side (on your machine, inside the app),
not something fetched from a server.
"""
import io
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont

CARD_W, CARD_H = 900, 1200
TEXT_PRIMARY = (244, 244, 247)
TEXT_MUTED = (156, 156, 176)


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _fetch_image(url: str, size: int) -> Image.Image | None:
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        return img.resize((size, size))
    except Exception:
        return None


def build_recap_image(data: dict, accent_hex: str) -> bytes:
    accent = _hex_to_rgb(accent_hex)
    bg_top, bg_bottom = (10, 10, 15), (20, 12, 26)

    img = Image.new("RGB", (CARD_W, CARD_H), bg_top)
    draw = ImageDraw.Draw(img)
    for y in range(CARD_H):
        t = y / CARD_H
        draw.line(
            [(0, y), (CARD_W, y)],
            fill=tuple(int(bg_top[i] + (bg_bottom[i] - bg_top[i]) * t) for i in range(3)),
        )

    title_font = _load_font(46, bold=True)
    section_font = _load_font(24, bold=True)
    label_font = _load_font(20)
    value_font = _load_font(32, bold=True)
    small_font = _load_font(16)

    x = 50
    y = 50
    draw.text((x, y), data["name"], font=title_font, fill=TEXT_PRIMARY)
    y += 58
    draw.text((x, y), f"Season Recap — {data['scope_label']}", font=label_font, fill=accent)
    y += 44
    draw.line([(x, y), (CARD_W - x, y)], fill=accent, width=2)
    y += 36

    def stat_row(label, value, yy):
        draw.text((x, yy), label, font=label_font, fill=TEXT_MUTED)
        draw.text((x, yy + 24), str(value), font=value_font, fill=TEXT_PRIMARY)
        return yy + 82

    y = stat_row("Games Played", data["games"], y)
    y = stat_row("Win Rate", f"{data['win_rate']}%", y)
    y = stat_row("Longest Win Streak", f"{data['longest_win_streak']} games", y)
    y = stat_row("Average KDA", data["avg_kda"], y)
    if data.get("rank"):
        y = stat_row("Current Rank", data["rank"], y)

    y += 14
    draw.line([(x, y), (CARD_W - x, y)], fill=accent, width=1)
    y += 30
    draw.text((x, y), "TOP CHAMPION", font=section_font, fill=accent)
    y += 40

    icon = _fetch_image(data["top_champion_icon_url"], 84) if data.get("top_champion_icon_url") else None
    if icon:
        img.paste(icon, (x, y), icon)
    text_x = x + (100 if icon else 0)
    draw.text((text_x, y + 10), data["top_champion"], font=value_font, fill=TEXT_PRIMARY)
    draw.text(
        (text_x, y + 50),
        f"{data['top_champion_win_rate']}% win rate · {data['top_champion_games']} games",
        font=label_font, fill=TEXT_MUTED,
    )
    y += 110

    if data.get("best_matchup"):
        draw.line([(x, y), (CARD_W - x, y)], fill=accent, width=1)
        y += 30
        draw.text((x, y), "BEST MATCHUP", font=section_font, fill=accent)
        y += 40
        draw.text((x, y), data["best_matchup"], font=value_font, fill=TEXT_PRIMARY)
        y += 60

    draw.text(
        (x, CARD_H - 50),
        f"Generated {datetime.now().strftime('%b %d, %Y')} — {data.get('app_title', 'League Board')}",
        font=small_font, fill=TEXT_MUTED,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
