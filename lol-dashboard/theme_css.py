"""
Themed CSS for the whole app — one big f-string.

Pure: everything it needs arrives as an argument, so this module
holds no runtime state and imports nothing from the app.
"""


# The one display font used sitewide. Lives here rather than in app.py
# because this module is meant to be self-contained: everything the CSS
# needs is either an argument or defined right here.
HEADER_FONT = "Sora"


def render_theme_css(
    accent: str, accent2: str, accent_rgb: str, accent2_rgb: str,
    hero_url: str, dark_mode: bool, accent_text: str, accent2_text: str,
) -> str:
    font = HEADER_FONT
    font_param = font.replace(" ", "+")
    if dark_mode:
        bg_color = "#0A0A0F"
        overlay = "linear-gradient(rgba(6,6,10,0.6), rgba(6,6,10,0.92))"
        text_color = "#F4F4F7"
        text_dim = "#9C9CB0"
        card_bg = "rgba(24,24,32,0.6)"
        card_bg_soft = "rgba(24,24,32,0.4)"
        card_bg_row = "rgba(24,24,32,0.55)"
        border_base = "0.25"
        banner_bg = "rgba(20,20,28,0.22)"
    else:
        bg_color = "#F0F0F4"
        overlay = "linear-gradient(rgba(240,240,244,0.82), rgba(240,240,244,0.96))"
        text_color = "#15151C"
        text_dim = "#54546A"
        card_bg = "rgba(255,255,255,0.72)"
        card_bg_soft = "rgba(255,255,255,0.55)"
        card_bg_row = "rgba(255,255,255,0.68)"
        border_base = "0.35"
        banner_bg = "rgba(255,255,255,0.28)"
    return f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family={font_param}:wght@400;700&family=Inter:wght@400;500;600&display=swap');

    html, body {{ font-family: 'Inter', sans-serif; }}
    h1, h2, h3 {{
        font-family: '{font}', sans-serif !important;
        letter-spacing: 0.01em;
        font-weight: 600;
    }}

    .stApp {{
        background-color: {bg_color};
        background-image: {overlay}, url('{hero_url}');
        background-position: center, center 18%;
        background-size: cover, cover;
        background-repeat: no-repeat, no-repeat;
        background-attachment: fixed;
    }}

    h1 {{
        color: {text_color};
        font-weight: 700;
        border-bottom: 1px solid rgba({accent_rgb},0.4);
        padding-bottom: 0.5rem;
    }}
    h2, h3, p, span, label, .stMarkdown {{ color: {text_color}; }}
    .stCaption, [data-testid="stCaptionContainer"] {{ color: {text_dim} !important; }}

    [data-testid="stMetric"] {{
        background: {card_bg};
        backdrop-filter: blur(14px);
        border: 1px solid rgba({accent_rgb},{border_base});
        border-radius: 14px;
        padding: 12px 10px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }}
    [data-testid="stMetricValue"] {{ color: {accent_text}; font-family: 'Inter', sans-serif; font-weight: 700; }}
    [data-testid="stMetricLabel"] {{ color: {text_dim}; }}

    .champ-card {{
        background: {card_bg};
        backdrop-filter: blur(14px);
        border: 1px solid rgba({accent_rgb},0.22);
        border-radius: 14px;
        padding: 10px 6px;
        text-align: center;
        margin-bottom: 10px;
    }}
    .champ-card img {{ border-radius: 8px; border: 1.5px solid rgba({accent_rgb},0.5); }}
    .champ-card .wr-good {{ color: #2DD4BF; font-weight: 600; }}
    .champ-card .wr-bad {{ color: #FB7185; font-weight: 600; }}
    .champ-card .champ-name {{ color: {text_color}; font-weight: 600; }}
    .champ-card .role-badge, .role-badge {{
        display: inline-block;
        background: rgba({accent_rgb},0.18);
        border: 1px solid rgba({accent_rgb},0.35);
        color: {accent_text};
        border-radius: 999px;
        padding: 1px 9px;
        font-size: 0.7rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        margin: 3px 0;
    }}

    .game-row {{
        display: flex;
        align-items: center;
        gap: 14px;
        background: {card_bg_row};
        backdrop-filter: blur(10px);
        border: 1px solid rgba({accent_rgb},0.16);
        border-radius: 10px;
        padding: 8px 14px;
        margin-bottom: 4px;
    }}
    .game-row img.champ-icon {{ border-radius: 6px; border: 1.5px solid rgba({accent_rgb},0.5); }}
    .game-row .col {{ color: {text_dim}; }}
    .game-row .col b {{ color: {text_color}; }}
    .game-row img.item-icon {{ border-radius: 4px; margin-right: 2px; border: 1px solid #26262E; }}

    .build-row {{
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 8px;
    }}
    .build-row img.item-icon {{ border-radius: 4px; border: 1px solid rgba({accent_rgb},0.4); }}

    .scoreboard-row {{
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 5px;
        padding: 4px 6px;
        border-radius: 6px;
    }}
    .scoreboard-row.me {{ background: rgba({accent_rgb},0.14); border: 1px solid rgba({accent_rgb},0.35); }}
    .scoreboard-row .name {{ color: {text_color}; min-width: 90px; font-size: 0.85rem; }}
    .scoreboard-row .kda {{ color: {text_dim}; min-width: 70px; font-size: 0.85rem; }}
    .scoreboard-row .cs {{ color: {text_dim}; min-width: 55px; font-size: 0.85rem; }}

    .tip-row {{
        display: flex;
        align-items: flex-start;
        gap: 10px;
        background: {card_bg_soft};
        border-radius: 10px;
        padding: 10px 14px;
        margin-bottom: 6px;
        border-left: 3px solid rgba({accent_rgb},0.6);
    }}
    .tip-row span.icon {{ font-size: 1.1rem; line-height: 1.3; }}
    .tip-row span.text {{ color: {text_color}; font-size: 0.92rem; }}

    /* Sample size + confidence margin, set quieter than the claim itself. */
    .tip-row span.tip-meta {{
        display: block;
        margin-top: 3px;
        color: {text_dim};
        font-size: 0.75rem;
        letter-spacing: 0.02em;
    }}
    /* Tips whose underlying comparison isn't separable from noise. Dimmed
       and de-accented rather than hidden — the point is to show that the app
       looked and found the evidence thin, not to pretend it found nothing. */
    .tip-row.tip-weak {{
        border-left-color: {text_dim};
        opacity: 0.72;
    }}
    .tip-row.tip-weak span.text {{ color: {text_dim}; }}

    [data-testid="stExpander"] {{
        background: {card_bg_soft};
        border: 1px solid rgba({accent_rgb},0.16);
        border-radius: 10px;
    }}

    /* ---- Magazine hero banner (bigger, with optional stat chips) ---- */
    .hero-banner {{
        position: relative;
        display: flex;
        align-items: center;
        gap: 28px;
        flex-wrap: wrap;
        border-radius: 24px;
        padding: 40px 44px;
        margin-bottom: 26px;
        background: {banner_bg};
        backdrop-filter: blur(6px);
        border: 1px solid rgba({accent_rgb},0.3);
        box-shadow: 0 8px 40px rgba(0,0,0,0.2), 0 0 50px rgba({accent2_rgb},0.12);
    }}
    .hero-banner img.hero-icon {{
        flex-shrink: 0;
        width: 92px;
        height: 92px;
        border-radius: 50%;
        border: 3px solid rgba({accent_rgb},0.6);
        box-shadow: 0 0 20px rgba({accent_rgb},0.4), 0 0 34px rgba({accent2_rgb},0.25);
        object-fit: cover;
    }}
    .hero-banner-body {{ flex: 1; min-width: 240px; }}
    .hero-banner-eyebrow {{
        color: {accent2_text};
        font-family: 'Inter', sans-serif;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        margin-bottom: 4px;
    }}
    .hero-banner-title {{
        font-family: '{font}', sans-serif;
        font-size: 2.9rem;
        line-height: 1.08;
        font-weight: 700;
        letter-spacing: 0.01em;
        margin: 0;
        background: linear-gradient(90deg, {accent_text}, {accent2_text});
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        color: {text_color};
        filter: drop-shadow(0 3px 10px rgba(0,0,0,0.55));
    }}
    .hero-banner-subtitle {{
        color: {text_color};
        opacity: 0.85;
        font-size: 1.05rem;
        margin: 8px 0 0 0;
        font-family: 'Inter', sans-serif;
        text-shadow: 0 1px 5px rgba(0,0,0,0.5);
    }}
    .hero-stat-row {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }}
    .hero-stat-chip {{
        background: {card_bg_soft};
        backdrop-filter: blur(8px);
        border: 1px solid rgba({accent_rgb},0.28);
        border-radius: 12px;
        padding: 8px 18px;
        text-align: left;
    }}
    .hero-stat-chip .chip-label {{
        color: {text_dim};
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }}
    .hero-stat-chip .chip-value {{
        color: {accent_text};
        font-size: 1.35rem;
        font-weight: 700;
        font-family: 'Inter', sans-serif;
    }}

    /* ---- Magazine section cards (st.container(border=True, key=...)) ---- */
    [class*="st-key-card-"] {{
        background: {card_bg} !important;
        backdrop-filter: blur(14px);
        border: 1px solid rgba({accent_rgb},{border_base}) !important;
        border-radius: 18px !important;
        padding: 6px 6px !important;
        box-shadow: 0 4px 24px rgba(0,0,0,0.18);
        transition: box-shadow 0.18s ease;
        margin-bottom: 20px;
    }}
    [class*="st-key-card-"]:hover {{
        box-shadow: 0 8px 34px rgba(0,0,0,0.26), 0 0 24px rgba({accent_rgb},0.12);
    }}
    [class*="st-key-card-feat-"] {{
        border-top: 3px solid {accent} !important;
    }}
    .card-title {{
        display: flex;
        align-items: center;
        gap: 10px;
        font-family: '{font}', sans-serif;
        font-size: 1.4rem;
        font-weight: 600;
        color: {text_color};
        margin: 4px 0 2px 0;
    }}
    .card-title .card-icon {{ font-size: 1.2rem; }}
    .card-subtitle {{ color: {text_dim}; font-size: 0.86rem; margin-bottom: 14px; }}

    /* ---- Top control bar (replaces the old sidebar) ---- */
    .control-account {{
        display: flex;
        flex-direction: column;
        justify-content: center;
        height: 100%;
        min-height: 38px;
        color: {text_color};
        font-family: 'Inter', sans-serif;
        font-size: 1.02rem;
        line-height: 1.25;
    }}
    .control-account .control-meta {{
        color: {text_dim};
        font-size: 0.76rem;
        letter-spacing: 0.02em;
    }}
    /* The top nav bar itself — tint it to match the app's surfaces rather
       than leaving Streamlit's default header styling floating above a
       themed page. */
    header[data-testid="stHeader"] {{
        background: {card_bg} !important;
        backdrop-filter: blur(14px);
        border-bottom: 1px solid rgba({accent_rgb},{border_base});
    }}

    /* ---- Narrow windows / mobile ---- */
    /* Streamlit's columns don't collapse on their own below a certain width;
       they just squeeze. These rules stop the hero, control bar and game
       rows from becoming unreadable slivers on a phone or a half-width
       desktop window. */
    @media (max-width: 900px) {{
        .hero-banner {{
            padding: 24px 20px;
            gap: 16px;
            border-radius: 18px;
        }}
        .hero-banner img.hero-icon {{ width: 60px; height: 60px; }}
        .hero-banner-title {{ font-size: 1.9rem; }}
        .hero-banner-subtitle {{ font-size: 0.95rem; }}
        .hero-stat-chip {{ padding: 6px 12px; }}
        .hero-stat-chip .chip-value {{ font-size: 1.1rem; }}

        /* Let a match row wrap instead of overflowing off-screen. */
        .game-row {{ flex-wrap: wrap; gap: 8px; padding: 8px 10px; }}
        .game-row .col {{ min-width: 0 !important; }}

        [class*="st-key-card-"] {{ padding: 2px !important; border-radius: 14px !important; }}
        .card-title {{ font-size: 1.15rem; }}
        .control-account {{ min-height: 0; margin-bottom: 6px; }}

        /* Streamlit's horizontal blocks keep their row layout and shrink each
           column to nothing; force them to stack instead. */
        [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
        [data-testid="stHorizontalBlock"] > div {{ min-width: 46% !important; }}
    }}
    @media (max-width: 640px) {{
        .hero-banner {{ padding: 18px 14px; }}
        .hero-banner-title {{ font-size: 1.5rem; }}
        .scoreboard-row .name {{ min-width: 70px; }}
        /* Below this width side-by-side is hopeless — go single column. */
        [data-testid="stHorizontalBlock"] > div {{ min-width: 100% !important; }}
    }}
    </style>
    """

