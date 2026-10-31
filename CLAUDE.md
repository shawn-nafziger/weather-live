# weather-live: notes for Claude

Live data for Shawn Nafziger's Weather Intelligence project. Read `shawn-nafziger/weather-intelligence/CLAUDE.md` for who Shawn is and how we work (plain language first, PRs only, one step at a time on his PC, outcomes he can check). The product plan and design playbook are in the "Weather intelligence" Claude Project.

## Rules
- **Code and config changes arrive as pull requests.** Only the two workflows commit straight to `main`: hourly touches `archive/` and `live/`; daily touches `history/`.
- **The daily workflow also publishes the Almanac** (`almanac/build.py --all` → GitHub Pages at `weather.tallorderdigital.com/almanac/`). Pages are built per run and never committed. Every sentence is chosen from the city's numbers and checked; a city whose check fails is skipped, not published. Wording rules and decisions: the "Weather intelligence" Project doc `almanac-dashboards-plan.md`.
- **`history/` is the official record**, refreshed for the last 21 days each run; days are replaced, never dropped.
- **The archive is permanent:** never edit, rewrite or delete files under `archive/`. Forecasts are kept per issue time. Fixes go forward only, with a note in the README.
- **Store metric, keep what the source said:** forecasts keep the NWS °F value and a °C copy; observations are °C as received.
- **Label honestly:** "so far" values come from hourly readings and can differ from the official next-morning high/low by a degree or two. Forecast-based statements say "forecast", "on pace for" or "would rank".
- **Ranks** (`scripts/ranks.py`): one official station series only, whole °F, "years strictly warmer + 1", always with the count ("of 155 since 1872"). Today and forecasts are ranked only where `rank_today` is true. "Typical" cutoff (warmest/coolest fifth) is Shawn's editorial call.
- Claude's cloud workspace **can't reach api.weather.gov or ACIS**; test with the offline fixtures in `tests/` and say so. The real test is a manual **Run workflow** on GitHub.
- No personal email in the NWS User-Agent; it identifies the site and this repo.
- All paths and cities come from the top of `scripts/nws_pull.py` and `config/cities.json`.

- **Runs are started by a Cloudflare Worker** (`cloudflare/worker.js`, crons `7 * * * *` and `37 11,15 * * *` UTC); GitHub's schedule is only a backup. The Worker's token expires Oct 29, 2027 (see README).

## Next
- Almanac: the ~300 official-record cities (records will need storage outside git at that size; see the plan doc).
- The live page itself (design playbook: `tallorderdigital-site/docs/design-system-and-dataviz-playbook.md`).
