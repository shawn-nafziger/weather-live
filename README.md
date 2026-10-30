# weather-live

The live data behind `weather.tallorderdigital.com` (page not built yet). Part of Tall Order Digital's Weather Intelligence project; the analysis, history and Power BI model live in the private `weather-intelligence` repo.

## What happens every hour

A GitHub Actions job (`.github/workflows/hourly.yml`) runs at about :17 past each hour and, for each city in `config/cities.json`:

1. **Saves the National Weather Service forecast** to `archive/forecasts/`, one row per forecast period, stamped with the time the NWS issued it. Every issue is kept and nothing is overwritten. Forecast accuracy (1- to 7-day) is measured from this archive.
2. **Saves the station's hourly readings** to `archive/observations/` (duplicates skipped).
3. **Writes `live/<city>.json`**: the latest reading, today's high and low so far, the 7-day forecast, the next 48 hours, and how yesterday, today so far and each forecast high rank against the city's official record. The live page will read these files. `live/status.json` shows how the last run went.

## What happens twice a day

`.github/workflows/daily.yml` (11:37 and 17:37 UTC) keeps `history/<city>.csv`: each city's official daily record (high, low, rain, snow) from NOAA via RCC-ACIS, using the ThreadEx "official city record" where one exists. The first run downloads each full record; after that it re-reads the last 21 days, because stations post late and correct values. No day is ever dropped. The hourly job ranks against these files.

## How ranks work

Same rules as the Weather Intelligence methods: compare whole °F; rank = years strictly warmer + 1, so ties share a rank; always say how many years it's out of. Only the warmest or coolest fifth of the record gets ranked wording ("4th warmest October 28 high of 155 since 1872"); anything else reads "typical for October 28" (`scripts/ranks.py`). Today's values and forecasts are ranked only where today's station is the same as the record's (not yet Goshen or Los Olivos).

## Today so far

Stations send a precise report once an hour (tenths of a degree) and readings every 5 minutes rounded to whole °C. Today's high and low use the hourly reports; a 5-minute reading flags a peak that certainly happened between them. Santa Ynez (Los Olivos) sends only rounded readings, so its values are marked approximate.

Archive files are one CSV per day (`archive/<kind>/YYYY/MM/YYYY-MM-DD.csv`); past days are never changed. Observation files from Oct 30, 2026 add `ReportType`, `Precise` and `RawMessage` (the station's own report text); earlier files don't have them.

## Cities

Chicago, Phoenix, Miami, Seattle, Goshen IN and Los Olivos CA. Forecasts are pulled for each observation station's exact location, so they can be checked against what that station records. Goshen and Los Olivos use airport stations for now; their long official records come from different, once-a-day stations.

## Run it by hand

On GitHub: **Actions → Hourly weather pull → Run workflow**.

Tests (no internet needed): `python -m unittest discover tests`

## Data credit

National Weather Service, [api.weather.gov](https://www.weather.gov/documentation/services-web-api); official daily records from NOAA via [RCC-ACIS](https://www.rcc-acis.org/). Public domain.
