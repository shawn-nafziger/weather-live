"""
One-time: turns US state boundaries (GeoJSON, longitude/latitude) into the
simplified SVG outlines the Almanac's front page draws (almanac/us_states.json).

    python almanac/make_us_map.py path/to/us-states.json

Source used: US Census Bureau state boundaries (public domain), as published in
the Leaflet choropleth example (PublicaMundi/MappingAPI, data/geojson/us-states.json).
Only needs re-running if the outlines should change.
"""

import json
import sys
from pathlib import Path

from albers import HEIGHT, WIDTH, project

HERE = Path(__file__).resolve().parent
MIN_STEP = 0.9      # drop points closer than this (map units) to the last kept one


def ring_path(ring, region):
    pts, last = [], None
    for lon, lat in ring:
        x, y = project(lon, lat, region)
        if last is None or abs(x - last[0]) + abs(y - last[1]) >= MIN_STEP:
            pts.append((x, y))
            last = (x, y)
    if len(pts) < 3:
        return ""
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z"


def main(src):
    gj = json.loads(Path(src).read_text(encoding="utf-8"))
    paths = {}
    for f in gj["features"]:
        name = f["properties"]["name"]
        if name == "Puerto Rico":
            continue
        region = {"Alaska": "AK", "Hawaii": "HI"}.get(name)
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        d = "".join(ring_path(poly[0], region) for poly in polys)
        if d:
            paths[name] = d
    out = {"width": WIDTH, "height": HEIGHT,
           "source": "US Census Bureau state boundaries (public domain)", "states": paths}
    (HERE / "us_states.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{len(paths)} states written to almanac/us_states.json "
          f"({(HERE / 'us_states.json').stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
