# weather-live

The live data behind `weather.tallorderdigital.com` (page not built yet). Part of Tall Order Digital's Weather Intelligence project; the analysis, history and Power BI model live in the private `weather-intelligence` repo.

## What happens every hour

A GitHub Actions job (`.github/workflows/hourly.yml`) runs at about :17 past each hour and, for each city in `config/cities.json`:

1. **Saves the National Weather Service forecast** to `archive/forecasts/`, one row per forecast period, stamped with the time the NWS issued it. Every issue is kept and nothing is overwritten. Forecast accuracy (1- to 7-day) is measured from this archive.
2. **Saves the station's hourly readings** to `archive/observations/` (duplicates skipped).
3. **Writes `live/<city>.json`**: the latest reading, today's high and low so far, the 7-day forecast, the next 48 hours, and how yesterday, today so far and each forecast high rank against the city's official record. The live page will read these files. `live/status.json` shows how the last run went.

## What happens twice a day

`.github/workflows/daily.yml` (11:37 and 17:37 UTC) keeps `history/<city>.csv`: each city's official daily record (high, low, rain, snow) from NOAA via RCC-ACIS, using the ThreadEx "official city record" where one exists. The first run downloads each full record; after that it re-reads the last 21 days, because stations post late and correct values. No day is ever dropped. The hourly job ranks against these files.

## The Almanac (weather.tallorderdigital.com)

After each records update, the daily workflow's second job, **Publish Almanac**, rebuilds one page per city from `history/` and publishes them with GitHub Pages:

- `weather.tallorderdigital.com/almanac/` is the front page: a "find a place" search, a US map with one dot per place (colored by record lows set since 2000 compared with chance), and a sortable table of every place. Built to hold hundreds of places.
- `weather.tallorderdigital.com/almanac/<city>/` is the city's page: yesterday in context, the year so far, the record book, how the climate has changed, rain and snow.
- The site's front page sends visitors to the Almanac until the live page takes its place.

The pages are built fresh each run and are **not committed** (`site/` is ignored), so the repo doesn't fill up with copies.

Every sentence on a page is chosen from that city's numbers, and `almanac/build.py` checks each claim before writing a page (ranks recounted; each change claim checked two ways and against the last 40 years, because a station move can hide a trend). If a check fails for one city, that page is skipped and the others still publish. The run log says which.

**Record lows vs chance** (the map's colors): each calendar date has one record low; if the climate weren't changing, the years since 2000 would hold their fair share. To tell a real gap from luck, the build picks the same number of years at random from the place's record 20,000 times (whole years, so a cold snap that set several records stays together). Orange means fewer than 2.5% of random picks did as well.

The map outlines (`almanac/us_states.json`, US Census Bureau, public domain) were made once by `almanac/make_us_map.py`; `almanac/albers.py` places each dot with the same projection.

**Goshen and South Bend** are a pair: Goshen's long record is a volunteer station in town, South Bend's is the official city record about 27 miles away. Each page has a switch to the other. Each page uses only its own station.

Build locally (needs `pip install -r almanac/requirements.txt`): `python almanac/build.py --all`, then open `site/almanac/index.html`.

### Adding places

Almanac-only places live in `config/almanac_cities.json` (name, state, ACIS id of the official record, and the record site's latitude/longitude for the map dot). The hourly job doesn't follow them; only the daily job does. The first wave is the 50 biggest US metro areas that have an official city record (added Nov 1, 2026).

- **New places download over a few runs:** at most 15 full records per run, so 46 new places take about a day.
- **`history/status.json`** shows what happened to every place on the last run: updated, failed (with the error, for example a wrong ACIS id), or waiting for its first download.
- **Quality screen:** a place whose record looks wrong (latest day more than 10 days old, values beyond US all-time records, or lows above highs on more than 0.5% of days) is left off the site and shows as `REVIEW` in the run log. A place whose page would say something untrue shows as `SKIP`.

## How ranks work

Same rules as the Weather Intelligence methods: compare whole °F; rank = years strictly warmer + 1, so ties share a rank; always say how many years it's out of. Only the warmest or coolest fifth of the record gets ranked wording ("4th warmest October 28 high of 155 since 1872"); anything else reads "typical for October 28" (`scripts/ranks.py`). Today's values and forecasts are ranked only where today's station is the same as the record's (not yet Goshen or Los Olivos).

## Today so far

Stations send a precise report once an hour (tenths of a degree) and readings every 5 minutes rounded to whole °C. Today's high and low use the hourly reports; a 5-minute reading flags a peak that certainly happened between them. Santa Ynez (Los Olivos) sends only rounded readings, so its values are marked approximate.

Archive files are one CSV per day (`archive/<kind>/YYYY/MM/YYYY-MM-DD.csv`); past days are never changed. Observation files from Oct 30, 2026 add `ReportType`, `Precise` and `RawMessage` (the station's own report text); earlier files don't have them.

## Cities

Chicago, Phoenix, Miami, Seattle, Goshen IN, Los Olivos CA and South Bend IN (added Oct 31, 2026; its full record downloads on the first daily run after it was added). Forecasts are pulled for each observation station's exact location, so they can be checked against what that station records. Goshen and Los Olivos use airport stations for now; their long official records come from different, once-a-day stations.

## What starts the jobs

GitHub's own schedule skips many runs (on Oct 29-30, 2026 it ran 2 of about 11 hourly slots and then stopped), so a **Cloudflare Worker** starts the jobs on time:

- Worker `weather-live-trigger` in Shawn's Cloudflare account. Its code is copied in `cloudflare/worker.js` (edit it in the Cloudflare dashboard, then update this copy by PR).
- Cron triggers (UTC): `7 * * * *` starts **Hourly weather pull**; `37 11,15 * * *` starts **Daily official records**.
- It uses the secret `GITHUB_TOKEN`: a GitHub fine-grained token, "weather-live trigger", that can only start Actions in this repo. **It expires Oct 29, 2027**; a reminder is scheduled for Oct 15, 2027. To renew: regenerate the token on GitHub, then replace the secret in the Worker's Settings > Variables and secrets.
- GitHub's own schedules stay on as a backup. Extra runs are harmless.

## Run it by hand

On GitHub: **Actions → Hourly weather pull → Run workflow**.

Tests (no internet needed): `python -m unittest discover tests`

## Data credit

National Weather Service, [api.weather.gov](https://www.weather.gov/documentation/services-web-api); official daily records from NOAA via [RCC-ACIS](https://www.rcc-acis.org/). Public domain.
