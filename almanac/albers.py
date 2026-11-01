"""
The standard US map projection (Albers equal-area, with Alaska and Hawaii as
insets), so map dots and state outlines line up without any outside library.

Same math and settings as d3's geoAlbersUsa at scale 1300, which draws the US
in a 975 x 610 box. Used by make_us_map.py (state outlines, once) and build.py
(one dot per place, every run).
"""

import math

WIDTH, HEIGHT = 975, 610
SCALE = 1300
TRANSLATE = (487.5, 305)


def _conic_equal_area(parallels, rotate, center, k, translate):
    """d3.geoConicEqualArea with the given settings, as a function (lon, lat) -> (x, y)."""
    y0, y1 = (math.radians(p) for p in parallels)
    sy0 = math.sin(y0)
    n = (sy0 + math.sin(y1)) / 2
    c = 1 + sy0 * (2 * n - sy0)
    r0 = math.sqrt(c) / n

    def raw(lam, phi):
        r = math.sqrt(max(0.0, c - 2 * n * math.sin(phi))) / n
        lam *= n
        return r * math.sin(lam), r0 - r * math.cos(lam)

    cx, cy = raw(math.radians(center[0]), math.radians(center[1]))
    dx, dy = translate[0] - k * cx, translate[1] + k * cy

    def project(lon, lat):
        lam = math.radians(((lon + rotate + 180) % 360) - 180)
        x, y = raw(lam, math.radians(lat))
        return dx + k * x, dy - k * y

    return project


_k, (_tx, _ty) = SCALE, TRANSLATE
_lower48 = _conic_equal_area((29.5, 45.5), 96, (-0.6, 38.7), _k, (_tx, _ty))
_alaska = _conic_equal_area((55, 65), 154, (-2, 58.5), _k * 0.35, (_tx - 0.307 * _k, _ty + 0.201 * _k))
_hawaii = _conic_equal_area((8, 18), 157, (-3, 19.9), _k, (_tx - 0.205 * _k, _ty + 0.212 * _k))


def project(lon, lat, region=None):
    """(lon, lat) in degrees -> (x, y) on the 975 x 610 map.
    region: 'AK', 'HI' or None (lower 48). Without it, Alaska and Hawaii are
    recognized by location."""
    if region is None:
        if lat > 50 and lon < -129:
            region = "AK"
        elif 18 < lat < 23 and -161 < lon < -154:
            region = "HI"
    return {"AK": _alaska, "HI": _hawaii}.get(region, _lower48)(lon, lat)
