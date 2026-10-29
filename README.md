# weather-live

The live data behind `weather.tallorderdigital.com` (page not built yet). Part of Tall Order Digital's Weather Intelligence project; the analysis, history and Power BI model live in the private `weather-intelligence` repo.

## What happens every hour

A GitHub Actions job (`.github/workflows/hourly.yml`) runs at about :17 past each hour and, for each city in `config/cities.json`:

1. **Saves the National Weather Service forecast** to `archive/forecasts/`, one row per forecast period, stamped with the time the NWS issued it. Every issue is kept and nothing is overwritten. Forecast accuracy (1- to 7-day) is measured from this archive.
2. **Saves the station's hourly readings** to `archive/observations/` (duplicates skipped).
3. **Writes `live/<city>.json`**: the latest reading, today's high and low so far, the 7-day forecast and the next 48 hours. The live page will read these files. `live/status.json` shows how the last run went.

Archive files are one CSV per day (`archive/<kind>/YYYY/MM/YYYY-MM-DD.csv`); past days are never changed.

## Cities

Chicago, Phoenix, Miami, Seattle, Goshen IN and Los Olivos CA. Forecasts are pulled for each observation station's exact location, so they can be checked against what that station records. Goshen and Los Olivos use airport stations for now; their long official records come from different, once-a-day stations.

## Run it by hand

On GitHub: **Actions → Hourly weather pull → Run workflow**.

Tests (no internet needed): `python -m unittest discover tests`

## Data credit

National Weather Service, [api.weather.gov](https://www.weather.gov/documentation/services-web-api). Public domain.
