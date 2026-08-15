# LoL Performance Dashboard

Personal, local-only Streamlit dashboard for your League of Legends stats — ranked LP tracking, recent games feed, win rate by champion/role/queue/patch, performance trends with rolling averages, champion deep-dives (matchups, skins, builds), a champion comparison view, rule-based recommendations, and teammate synergy stats.

All config lives in `.env` — nothing is typed into the app itself, since this is single-user and not meant to be shared/hosted.

The app is organized as tabs across the top: **Home**, **Champions**, **Trends**, **Deep-Dive**, **Compare**, **Roles**, **Teammates**, and **Raw Data**. There's no sidebar — your account, the Refresh button, Settings and Display controls sit in a bar under the header, with the queue filter on the row below it.

## Setup

1. Double-click **`Setup.bat`**. First time only — it checks for Python (installs guidance if missing), installs everything in `requirements.txt`, and creates your local `.env` file.
   - If it says Python isn't found: it'll open the download page for you. Run the installer, and on the very first screen check **"Add python.exe to PATH"** before clicking Install. Then run `Setup.bat` again.
2. Double-click **`Run.bat`**. This opens the dashboard in your browser.
3. First launch, the app itself will ask for your Riot ID (`name#tag`) and a Riot API key — no text file editing required. Get a free key at https://developer.riotgames.com/ (sign in → **Generate API Key**). Dev keys expire every 24 hours; if a page shows a 401 error, there's a "paste new key" box right there to refresh it.

From then on, just double-click `Run.bat` to open it. Right-click it → **Show more options** (Windows 11) → **Send to → Desktop (create shortcut)** for a one-click launcher.

(Prefer the terminal? `python -m pip install -r requirements.txt` then `python -m streamlit run app.py` still works exactly the same way.)

## Sharing this with friends

The whole point of `Setup.bat` / `Run.bat` is that a friend never opens a terminal or edits a config file. To share it:

1. Zip up this whole `lol-dashboard` folder. Before zipping, delete these if present so your friend gets a clean slate instead of your personal data:
   - `.env` (your API key and Riot ID)
   - `data/` (your cached matches and rank history)
   - `highlight_replays/` (your matched replay files)
   (Leave `tests/` in — it's tiny, uses only synthetic data, and lets them confirm their install works via `Run tests.bat`.)
2. Send them the zip. They unzip it anywhere, double-click `Setup.bat` once, then `Run.bat`.
3. On first launch they'll be prompted for their own Riot ID and API key in the browser — filled in there, not in a file.

The one thing that can't be skipped: **each person needs their own Riot API key.** Keys are personal and non-transferable (tied to a Riot account, free, takes under a minute at developer.riotgames.com), so you can't generate one key and hand it around — that's a Riot policy, not a limitation of this app.

Everything each friend runs stays entirely local to their own computer — their matches, cache, and `.env` never touch yours or anyone else's.

## Visual identity

Earlier versions of this app had a 16-theme system where a random champion skin (with its own font, colors, and art) loaded every session. That was fun but fought against a clean, professional look — a horror font one session and a comic-book font the next doesn't read as polished. It's been replaced with something more deliberate:

- **Rank-tier accent color** — the site's accent color pair is derived from your current Ranked Solo/Duo tier (Iron through Challenger get their own color, each roughly matching that tier's well-known in-client palette — bronze copper, gold, platinum teal, diamond blue, master purple, and so on). It's a meaningful signal instead of a random reshuffle: the color actually reflects where your account stands right now, and updates the next time your rank changes. No ranked data yet (off-season, unranked, or before your first "Refresh data")? It falls back to a neutral indigo/cyan pair.
- **One hero champion, your pick** — the background splash and hero banner icon show a single champion you choose once from the **Settings** popover at the top of the page (sourced from champions in your match history), not something that auto-rotates. Change it any time; it's saved to `.env` and stays fixed until you change it again.
- **One professional type system, sitewide** — headers and hero titles use **Sora** (a clean geometric sans), body text and stat numbers use **Inter**, consistently across every page and every rank tier. No more per-theme display fonts.
- **Readable at every tier** — accent colors are contrast-corrected when used as text. The raw tier colors are tuned to look right as borders and glows, but several were unreadable as text: the muted low tiers were thin on dark, and the bright mid tiers washed out badly on light (Challenger measured 1.9:1 against a light card, well under the 4.5:1 accessibility threshold). Text now gets an automatically adjusted variant that clears 4.5:1 in both modes while keeping the hue, so Gold still reads gold. Decoration keeps the original color.

A separate **Dark mode** toggle (under **Display** in the top bar) is independent of the accent color — it flips the app's custom-styled surfaces (background, cards, headers, charts) between dark and light. Note: Streamlit's own native chrome (sidebar, buttons, built-in dataframe styling) is fixed to the dark base set in `.streamlit/config.toml` and can't be changed at runtime, so a few native widgets stay dark-styled regardless of the toggle — a limitation of Streamlit's theming, not a bug.

## Features

- **Magazine-style layout** — every page opens with a big hero banner (quick-glance stat chips under the title, rank tier in the eyebrow line) and its content organized into bordered "card" sections instead of one long uniform scroll, with the most important section on each page (Recommendations, Highlight Reel, Deep Match Analytics, etc.) visually leading via a bolder accent border. Charts pick up the current accent color and sit transparently on the card surface instead of drawing their own box.
- **Zero-terminal setup** — `Setup.bat` installs everything, and first launch prompts for Riot ID + API key right in the browser (see "Sharing this with friends" above) — nothing to hand-edit, so this is safe to hand to a friend.
- **In-app settings** — a Settings popover in the top bar to change your hero champion, how many matches get pulled (`MATCH_HISTORY_TARGET`), and your replay folder, without hand-editing `.env` — it writes changes back to the file for you.
- **Game review — win probability** — pick a game and see how win probability moved through it, with the two or three biggest swings called out as timestamps to actually rewatch (Trends, after loading deep match analytics). Inspired by [Coachless](https://coachless.gg/), with an honest difference: theirs is a model trained across a large multi-player dataset, while this is *empirical from your own games* — "when my team was this far ahead at this point, I went on to win X of Y times." Every reading carries its sample size, and thin buckets fall back to 50% instead of inventing a confident number.
- **Selection-bias controls** — win rates by build, runes and skill order are confounded: you finish a given build more often in games you were already winning, so part of what those numbers measure is "was I ahead," not "is this good." Those sections now say so, and Champion Deep-Dive has an **even games only** toggle (within 1,500 team gold at 15 minutes) so comparisons control for game state.
- **Work On This** — one focus area at a time on Home, drawn from your weakest profile dimension with a concrete thing to try, instead of a flat list of tips you'll skim. The other observations move into an expander. Returns nothing when your profile is too even to single anything out — picking one out of a flat spread would be reading noise.
- **Live game** — a card on Home that checks whether you're in a game right now and shows both lobbies with bans (Riot's spectator API). Checked on a button press rather than every page load, since it's an extra call against a rate-limited key and the answer is usually no.
- **Odds & ends** (Trends) — blue vs. red side win rate, surrender stats, Flash on D vs. F, and ping counts by type. All free from games already cached. Riot only began including ping counters on newer matches, so an older cache legitimately won't have them.
- **LP since last check** — "+18 LP since the previous refresh", counted with the same ordinal the climb tracker uses so a promotion reads as a gain rather than a 90-point drop.
- **Works on a narrow window** — the layout collapses to stacked cards on phones and half-width desktop windows instead of squeezing columns into slivers.
- **Roles page** — win rate by position, averages per role (KDA, CS/min, vision, kill participation), and a drill-down into the champions you play in each. Summoner's Rift only, since ARAM and Arena have no positions.
- **Data freshness** — the top bar shows how long ago the data was pulled ("updated 3 hours ago"), so you know when a refresh is due.
- **Player profile radar** — an eight-dimension "fingerprint" of how you play (aggression, farming, survivability, vision, teamplay, objectives, consistency, versatility) scored 0-100 and plotted as a radar chart, on Home and per-champion in the Deep-Dive. Inspired by Mobalytics' GPI, with one honest difference: they score against rank-matched peers using a trained model, which a single account's data can't support, so these are scored against fixed reference bands. It describes the *shape* of your playstyle, not how you rank against your peers. **Summoner's Rift only** — ARAM and Arena distort nearly every dimension (no recall means more deaths, constant teamfighting inflates assists and kill participation, almost no warding happens), so including them would measure how much ARAM you play rather than how you play. Farming is scored against each role's own CS range, so it's comparable whether you jungle, support or lane.
- **Skill max order** — win rate by which abilities you maxed and in what order (Q › E › W), on Champion Deep-Dive, from timelines already fetched for build order. Games affected by [Riot's open duplicate-skill-event bug](https://github.com/RiotGames/developer-relations/issues/1100) are excluded and counted rather than charted wrong.
- **Multikill tracker** — total double/triple/quadra/penta kills for the current queue filter (Home page), and penta/quadra games get their own Highlight Reel badge.
- **Matchup Extremes** — your "Nemesis" (lane opponent you lose to most) and "Free Win" (lane opponent you beat most), at 3+ games against them, on the Home page.
- **Full teammate synergy** — win rate with *any* frequently-recurring teammate, any role, on the Teammates page — not just the ADC+support bot-lane pairing the original duo stat covered.
- **Vision & CC stats** — average wards placed/killed, control wards bought, and CC score, general (Trends) and per-champion (Champion Deep-Dive) — counts only, not ward locations (see the death/kill heatmap note on why a location-based ward map isn't possible from Riot's public API).
- **Win rate by game length** — short/medium/long/very-long buckets on Home, a quick read on whether you're a snowballer or a scaler.
- **Climb goal tracker** — set a target tier/division in Settings; Home's Climb Goal card shows your current standing and, once there's enough tracked history, a rough days-to-goal estimate based on your last 7 days' climb rate.
- **Recent Games filter** — champion multiselect + win/loss filter above the Recent Games feed on Home.
- **Champion Mastery vs. win rate** — Champions page scatter plot correlating Riot's mastery points (a separate champion-mastery-v4 endpoint) against your win rate per champion, bubble-sized by games played. Answers "am I actually better on champions I've invested in, or just more comfortable?"
- **CS differential curve** — CS lead/deficit vs. your direct lane opponent at 10/15/20/25 min, alongside the existing gold-diff chart (Trends and Champion Deep-Dive). Same timeline fetch, no extra API calls — and often a cleaner read on lane phase than gold, which also swings on kills/plates/bounties.
- **Live streak flag** — a callout at the top of Home when you're on an active 3+ game win or loss streak, distinct from the retrospective post-loss win rate further down.
- **Personal bests** — career highs (most kills, highest KDA, most CS, most damage, highest vision, longest game) with the champion and date, on Home.
- **Pool concentration** — unique champions played and what share of games your top 3 account for, on Champions. One-tricking vs. spreading thin, presented as information rather than a verdict.
- **CSV export** — download the full computed match table (every column this dashboard tracks, one row per game) from the Raw Data page.
- **Deep-link to a champion** — `?champion=Ahri` opens Champion Deep-Dive pre-selected, and the URL stays in sync as you change the picker, so you can bookmark or share a specific breakdown.
- **Rune & summoner spell win rates** — keystone and summoner-spell-combo win rates, general (Trends) and per-champion (Champion Deep-Dive), from data already in every match — no extra API calls.
- **Death & kill heatmap** — map overlay of where you die and get kills, built from the same on-demand timeline loads as the gold curve/objectives (Trends and Champion Deep-Dive). Deaths only, not wards — Riot's public API doesn't expose ward placement locations for any player, for privacy/competitive-integrity reasons, so a ward heatmap isn't something any dashboard can build from official data.
- **Highlight reel** — Home page card feed auto-flagging standout games (best KDA win, biggest kill lead, highest damage share, roughest loss, etc.) from your current queue filter.
- **Replay finder** — right below the highlight reel, a button that scans your local League client's `Replays` folder (`Documents\League of Legends\Replays` by default, overridable in Settings) and copies any matching `.rofl` files for your standout games into `highlight_replays/`, renamed with the date/champion/reason. Matching is exact (by gameId embedded in the replay filename), not a timestamp guess. Important limitation: there's no Riot API to download replays remotely — this only finds replays your client already saved, and only within the ~2-week window Riot's replay servers keep a game available. It doesn't produce video; you'd still open the matched `.rofl` in the League client and record it yourself (OBS, etc.) to actually get footage for YouTube.
- **Home page snapshot** — sparkline strip (10-game rolling win %, 10-game rolling KDA, LP history) at the top, plus a **Recommendations** section: rule-based tips synthesized from your **Ranked Solo/Duo + Flex games only** (recent-form swings, post-loss tilt, best/worst hour or day, patch-over-patch win-rate shifts, a standout carry champion) — thresholded to avoid flagging noise from small sample sizes, and scoped to ranked regardless of the queue filter since normals/ARAM patterns are noisier and less meaningful.
- **Patch tracking** — every game records which patch it was played on (from match-v5's `gameVersion`); Win Rate by Patch charts appear on Home (all champions), Trends, Champion Deep-Dive (per champion), and Compare (overlaid for two champions).
- **Roles everywhere** — Riot reports positions as `TOP`/`JUNGLE`/`MIDDLE`/`BOTTOM`/`UTILITY`; those now display as Top/Jungle/Mid/Bot/Support, and laneless modes show **ARAM**/**Arena** rather than "Unknown" (an ARAM game isn't missing a position — it has none). Role badges on champion cards, a Role column in the champion table, a **role filter** on the Champions page, role on every game in the Recent Games feed and Highlight Reel, per-player roles in the match-details scoreboard, and a **By Role** split on Champion Deep-Dive for champions you play in more than one position (so a Shaco jungle/support blend doesn't hide behind one averaged number). Win Rate by Role now reads in lane order rather than by how often you play each one.
- **Champion sort control** — sort the Champions page by games played, win rate, or alphabetically.
- **Compare page** — pick two champions you've played and see win rate, KDA, CS/min, kill participation, and damage share side by side, plus overlaid rolling-KDA and win-rate-by-patch charts.
- **Progress bars on timeline loads** — the deep-match-analytics and per-champion timeline loaders now show a live "X of Y games fetched" progress bar instead of a single spinner, so long pulls give real feedback.
- **Ranked win rate as the headline** — the big number on Home is your official Ranked Solo/Duo record from Riot (whole season, not just the matches pulled, and not diluted by ARAM/normals). The queue-filtered win rate still appears in the Overview card, which is labelled with the active filter.
- **Ranked standing** — current tier/rank/LP, ranked W-L, and an LP trend line that builds itself the more you use the app (Riot's API only exposes current LP, not history — there's no way to backfill the past).
- **Queue filter** — a row of options under the top bar: All / Ranked / Normal / ARAM / etc.; every section below responds to it.
- **Recent Games feed** — last 20 games with champion, result, KDA, CS/min, vision, duration, and final items at a glance.
- **Champion cards** — win rate per champion with real champion icons, pulled live from Riot's Data Dragon CDN.
- **Trends** — KDA, CS/min, and vision score over time, with win/loss-colored points, a rolling average line, and a "recent vs. all-time" delta so you can see if you're trending up or down.
- **Kill-differential win rate** — general and per-champion: how often you win when you're up kills vs. down kills (kills − deaths), free to compute from data already loaded.
- **Kill participation / damage share** — average % of your team's kills and damage you're responsible for, plus trend charts over time.
- **First-blood/objective win rates** — win rate with vs. without first blood, first tower (personal), and team first dragon/baron/tower.
- **Tilt check** — win rate by hour of day, day of week, and after a win vs. after a loss.
- **Gold curve** — average gold at 10/15/20/25 min, wins vs. losses, general (last 50 games, on demand) and per-champion (bundled into the same timeline load as opening build).
- **Lane-opponent gold differential** — your gold lead/deficit vs. your direct lane opponent at each checkpoint (same timeline load).
- **Objective participation** — average dragons/barons/heralds/towers you personally helped take per game (same timeline load).
- **Season recap** — one-click shareable PNG summarizing games, win rate, longest win streak, top champion, best matchup, and current rank; downloadable from the main page.
- **Recent Games → Match details** — expand any game in the feed for a full 10-player scoreboard (both teams: champion, KDA, CS, items), not just your own line.
- **Champion deep-dive** — pick a champion you've played: splash-art banner, KDA trend, matchup win rates vs. lane opponents faced, most-played skins (with thumbnails), most common final item builds with win rate, a per-game purchase-order timeline, and win rate by opening build (first 3 core items rushed).
- **Duo partners** — win rate with recurring bot-lane pairings (heuristic — Riot doesn't expose premade party data). See also Full teammate synergy above for any role, not just bot lane.

## Tests

574 tests covering the data layer — match parsing, win-rate math, streaks, timeline curves, the recommendations engine, rank/climb-goal math, color contrast, local-time conversion, and the confidence maths behind the pattern tips. All synthetic fixtures: no API key, no network, no live game data needed.

Two ways to run them, both from this folder:

```
python tests\run_tests.py       # no install needed
pytest                          # if you have pytest, gives nicer output
```

The zero-install runner also runs `tools/check_bindings.py` (see "How the code is organized" below); `pytest` alone doesn't, so run it directly if you've changed how the UI modules get their state.

Or double-click **`Run tests.bat`**.

These cover the pure-logic modules (`stats.py`, `rank_history.py`, `themes.py`, `insights.py`) — the parts where a bug produces quietly *wrong numbers* rather than a visible error. The Streamlit UI layer and anything hitting Riot's API aren't covered; those still need a real run to verify.

## How the code is organized

`app.py` used to be ~2,900 lines. It's now the orchestrator only — config, caching, the Riot client, the control bar, the data fetch, and navigation — with the UI split out:

| File | What's in it |
| --- | --- |
| `app.py` | Startup, config, data fetch, control bar, navigation |
| `views.py` | One function per tab (Home, Champions, Trends, …) |
| `components.py` | Reusable UI pieces shared across pages |
| `layout.py` | Hero banners, section cards, tables |
| `theme_css.py` | All the CSS, as one pure function |
| `stats.py` | The analytical core — pure pandas, no Streamlit |
| `runtime.py` | Shares per-rerun state with the UI modules |

One thing worth knowing if you edit these: the UI modules don't import the shared runtime state (the match DataFrame, accent colours, Riot client, …) — `app.py` binds it into them on each rerun. That avoided threading two dozen arguments through forty functions, at the cost of those files referencing names that look undefined when read alone. `tools/check_bindings.py` closes that gap: it walks each module's AST and fails if anything references a name that isn't imported there, isn't a builtin, and isn't actually bound. It also checks that these modules contain *definitions only* — a stray call at module level would run on import, before Streamlit is set up. (That's not hypothetical: it's how the split first broke.) `tests/test_imports.py` backs it up by importing every module with the third-party packages stubbed, and `tests/test_startup.py` goes further still — it executes `app.py` from top to bottom with Streamlit and Riot stubbed, down both the configured and unconfigured startup paths, twice, because Streamlit reruns the script on every click while keeping modules loaded. That last one is the important one: the static checks can tell you a name exists somewhere, but only running the script in order tells you it exists *by the time it's used*. All of them run as part of `tests/run_tests.py`.

## Notes

- Match data is cached in `data/matches/`; ranked snapshots accumulate in `data/rank_history.json`. Both are gitignored.
- Champion mastery needs `riotwatcher` 3.3.0+ (the puuid-based mastery endpoints); `requirements.txt` enforces that floor. If the Mastery card says data is unavailable on an older install, `pip install -U riotwatcher` fixes it — and the rest of the dashboard works fine regardless, since the mastery call fails soft rather than breaking the load.
- Riot dev keys are rate-limited (100 req/2min) — the app paces uncached requests to stay under that; a full 1000-match first pull can take ~20 min uncached, but every load after that is instant from cache.
- Matchup/duo detection uses same-role-opposite-team (or bot-lane pairing) heuristics — not exact, since the public API doesn't expose premade groups.
- Build order uses Riot's match timeline API (separate call per match, fetched on demand — not for your whole history up front, since it doubles API calls). The single-game viewer fetches one timeline; the "opening build" win-rate section fetches timelines for every game on that champion when you click Load, which can take a while on a large champion pool.
- Purchase-order data shows the raw purchase log (doesn't reconcile item sells/undos) — matches how most third-party build-order timelines work.
- "Core item" filtering (for opening-build grouping) uses Data Dragon's live item tags (excludes Consumable/Trinket, requires 700+ gold), not a hardcoded item list, so it stays correct as items change patch to patch.
