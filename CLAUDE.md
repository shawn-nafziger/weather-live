# weather-live: notes for Claude

Live data for Shawn Nafziger's Weather Intelligence project. Read `shawn-nafziger/weather-intelligence/CLAUDE.md` for who Shawn is and how we work (plain language first, PRs only, one step at a time on his PC, outcomes he can check). The product plan and design playbook are in the "Weather intelligence" Claude Project.

## Rules
- **Code and config changes arrive as pull requests.** The only thing that commits straight to `main` is the hourly workflow, and it touches only `archive/` and `live/`.
- **The archive is permanent:** never edit, rewrite or delete files under `archive/`. Forecasts are kept per issue time. Fixes go forward only, with a note in the README.
- **Store metric, keep what the source said:** forecasts keep the NWS °F value and a °C copy; observations are °C as received.
- **Label honestly:** "so far" values come from hourly readings and can differ from the official next-morning high/low by a degree or two. Forecast-based statements say "forecast", "on pace for" or "would rank".
- **Ranks** (when added): from one official station series only, "years strictly warmer + 1", always with the count ("of 155 since 1872").
- Claude's cloud workspace **can't reach api.weather.gov**; test with the offline fixtures in `tests/` and say so. The real test is a manual **Run workflow** on GitHub.
- No personal email in the NWS User-Agent; it identifies the site and this repo.
- All paths and cities come from the top of `scripts/nws_pull.py` and `config/cities.json`.

## Next
- Daily official station update + same-date ranks (move script 12 from Shawn's PC to this workflow).
- The live page itself (design playbook: `tallorderdigital-site/docs/design-system-and-dataviz-playbook.md`).
