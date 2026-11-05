"""Outils géographiques en Python pur (GeoJSON en WGS84 : [longitude, latitude]).

Pas de dépendance (ni shapely ni pyproj) : les communes déploient FACILITY sur des serveurs simples.
Les conversions Lambert 72 / Lambert 2008 → WGS84 sont précises au mètre près, ce qui suffit
largement pour des objets d'entretien.
"""

import json
import math

SURFACES = ("Polygon", "MultiPolygon")
LIGNES = ("LineString", "MultiLineString")
POINTS = ("Point", "MultiPoint")


def _parse(text):
    if isinstance(text, dict):
        geom = text
    else:
        try:
            geom = json.loads(text) if text else None
        except (TypeError, ValueError):
            return None
    if isinstance(geom, dict) and geom.get("type") == "Feature":
        geom = geom.get("geometry")
    if not isinstance(geom, dict) or not geom.get("coordinates"):
        return None
    return geom


def loads(text):
    """Surface (Polygon / MultiPolygon) ou None : sert aux contours des # lieux."""
    geom = _parse(text)
    return geom if geom and geom.get("type") in SURFACES else None


def loads_any(text):
    """Point, ligne ou surface valide, sinon None."""
    geom = _parse(text)
    if not geom or geom.get("type") not in SURFACES + LIGNES + POINTS:
        return None
    try:
        if geom["type"] == "Point":
            ok = len(geom["coordinates"]) >= 2
        elif geom["type"] == "LineString":
            ok = len(geom["coordinates"]) >= 2
        elif geom["type"] == "Polygon":
            ok = len(geom["coordinates"][0]) >= 4
        else:
            ok = len(geom["coordinates"]) >= 1
    except (TypeError, IndexError, KeyError):
        ok = False
    return geom if ok else None


def kind(geom):
    """« point », « ligne » ou « surface »."""
    if not geom:
        return False
    t = geom.get("type")
    return "surface" if t in SURFACES else "ligne" if t in LIGNES else "point" if t in POINTS else False


def polygons(geom):
    if not geom or geom.get("type") not in SURFACES:
        return []
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def lines(geom):
    if not geom or geom.get("type") not in LIGNES:
        return []
    return [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]


def _in_ring(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def contains(geom, lng, lat):
    for poly in polygons(geom):
        if poly and _in_ring(lng, lat, poly[0]) and not any(_in_ring(lng, lat, h) for h in poly[1:]):
            return True
    return False


def area_m2(geom):
    total = 0.0
    for poly in polygons(geom):
        for k, ring in enumerate(poly):
            if len(ring) < 3:
                continue
            lat0 = math.radians(ring[0][1])
            pts = [(math.radians(p[0]) * 6371008.8 * math.cos(lat0), math.radians(p[1]) * 6371008.8) for p in ring]
            s = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=False))) / 2
            total += s if k == 0 else -s
    return max(total, 0.0)


def length_m(geom):
    total = 0.0
    for line in lines(geom):
        for a, b in zip(line, line[1:], strict=False):
            total += distance_m(a[1], a[0], b[1], b[0]) or 0.0
    return total


def measure(geom):
    """Longueur (m) d'une ligne, surface (m²) d'un polygone, 0 pour un point."""
    k = kind(geom)
    return area_m2(geom) if k == "surface" else length_m(geom) if k == "ligne" else 0.0


def centroid(geom):
    """(lng, lat) : centre de gravité d'une surface, milieu d'une ligne, le point lui-même."""
    k = kind(geom)
    if k == "point":
        c = geom["coordinates"] if geom["type"] == "Point" else geom["coordinates"][0]
        return (c[0], c[1])
    if k == "ligne":
        segs = [(a, b, distance_m(a[1], a[0], b[1], b[0]) or 0.0) for line in lines(geom) for a, b in zip(line, line[1:], strict=False)]
        half = sum(s[2] for s in segs) / 2
        for a, b, d in segs:
            if d >= half and d:
                t = half / d
                return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            half -= d
        first = lines(geom)[0][0]
        return (first[0], first[1])
    best, best_a = None, -1.0
    for poly in polygons(geom):
        ring = poly[0] if poly else []
        a = cx = cy = 0.0
        for (x1, y1, *_r1), (x2, y2, *_r2) in zip(ring, ring[1:] + ring[:1], strict=False):
            c = x1 * y2 - x2 * y1
            a += c
            cx += (x1 + x2) * c
            cy += (y1 + y2) * c
        if a and abs(a) > best_a:
            best_a, best = abs(a), (cx / (3 * a), cy / (3 * a))
        elif best is None and ring:
            best = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    return best


def bbox(geom):
    pts = list(iter_coords(geom))
    if not pts:
        return None
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def intersects_bbox(geom, box):
    b = bbox(geom)
    return bool(b) and not (b[2] < box[0] or b[0] > box[2] or b[3] < box[1] or b[1] > box[3])


def iter_coords(geom):
    if not geom:
        return
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        yield c
    elif t in ("MultiPoint", "LineString"):
        yield from c
    elif t in ("MultiLineString", "Polygon"):
        for part in c:
            yield from part
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield from ring


def map_coords(geom, fn):
    """Nouvelle géométrie dont chaque [x, y] passe par fn(x, y) → (x', y')."""
    def pt(p):
        x, y = fn(p[0], p[1])
        return [round(x, 7), round(y, 7)]
    t, c = geom["type"], geom["coordinates"]
    if t == "Point":
        out = pt(c)
    elif t in ("MultiPoint", "LineString"):
        out = [pt(p) for p in c]
    elif t in ("MultiLineString", "Polygon"):
        out = [[pt(p) for p in part] for part in c]
    else:
        out = [[[pt(p) for p in ring] for ring in poly] for poly in c]
    return {"type": t, "coordinates": out}


def distance_m(lat1, lng1, lat2, lng2):
    if not (lat1 and lng1 and lat2 and lng2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def bbox_around(lat, lng, metres):
    dlat = metres / 111320.0
    dlng = metres / (111320.0 * math.cos(math.radians(lat)))
    return lng - dlng, lat - dlat, lng + dlng, lat + dlat


# ---------------------------------------------------------------- projections belges
def _dms(d, m=0, s=0.0):
    return math.radians(d + m / 60 + s / 3600)


# a, f, lat1, lat2, lat0, lon0, x0, y0
_LAMBERT = {
    31370: (6378388.0, 1 / 297.0, _dms(51, 10, 0.00204), _dms(49, 50, 0.00204), _dms(90), _dms(4, 22, 2.952),
            150000.013, 5400088.438),
    3812: (6378137.0, 1 / 298.257222101, _dms(49, 50), _dms(51, 10), _dms(50, 47, 52.134), _dms(4, 21, 33.177),
           649328.0, 665262.0),
}


def _lcc_inverse(x, y, a, f, lat1, lat2, lat0, lon0, x0, y0):
    e = math.sqrt(2 * f - f * f)

    def m(phi):
        return math.cos(phi) / math.sqrt(1 - (e * math.sin(phi)) ** 2)

    def t(phi):
        es = e * math.sin(phi)
        return math.tan(math.pi / 4 - phi / 2) / ((1 - es) / (1 + es)) ** (e / 2)

    n = (math.log(m(lat1)) - math.log(m(lat2))) / (math.log(t(lat1)) - math.log(t(lat2)))
    big_f = m(lat1) / (n * t(lat1) ** n)
    r0 = a * big_f * (t(lat0) ** n if lat0 < math.pi / 2 - 1e-12 else 0.0)
    dx, dy = x - x0, r0 - (y - y0)
    r = math.copysign(math.hypot(dx, dy), n)
    tt = (r / (a * big_f)) ** (1 / n)
    theta = math.atan2(dx, dy)
    phi = math.pi / 2 - 2 * math.atan(tt)
    for _i in range(15):
        es = e * math.sin(phi)
        phi = math.pi / 2 - 2 * math.atan(tt * ((1 - es) / (1 + es)) ** (e / 2))
    return phi, theta / n + lon0


def _bd72_to_wgs84(phi, lam):
    """Datum BD72 (Hayford) → WGS84 par Helmert à 7 paramètres (EPSG:15929, rotation de repère)."""
    a, f = 6378388.0, 1 / 297.0
    e2 = 2 * f - f * f
    nn = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    x = nn * math.cos(phi) * math.cos(lam)
    y = nn * math.cos(phi) * math.sin(lam)
    z = nn * (1 - e2) * math.sin(phi)
    tx, ty, tz = -106.8686, 52.2978, -103.7239
    sec = math.pi / (180 * 3600)
    rx, ry, rz = -0.3366 * sec, 0.457 * sec, -1.8422 * sec
    s = 1 - 1.2747e-6
    x2 = tx + s * (x + rz * y - ry * z)
    y2 = ty + s * (-rz * x + y + rx * z)
    z2 = tz + s * (ry * x - rx * y + z)
    a2, f2 = 6378137.0, 1 / 298.257223563
    e22 = 2 * f2 - f2 * f2
    p = math.hypot(x2, y2)
    phi2 = math.atan2(z2, p * (1 - e22))
    for _i in range(10):
        n2 = a2 / math.sqrt(1 - e22 * math.sin(phi2) ** 2)
        phi2 = math.atan2(z2 + e22 * n2 * math.sin(phi2), p)
    return phi2, math.atan2(y2, x2)


def to_wgs84(x, y, epsg):
    """(lng, lat) WGS84 depuis Lambert 72 (31370), Lambert 2008 (3812) ou Web Mercator (3857)."""
    if epsg == 3857:
        lng = x / 6378137.0 * 180 / math.pi
        lat = math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)
        return lng, lat
    phi, lam = _lcc_inverse(x, y, *_LAMBERT[epsg])
    if epsg == 31370:
        phi, lam = _bd72_to_wgs84(phi, lam)
    return math.degrees(lam), math.degrees(phi)


def guess_epsg(x, y):
    """Devine le système d'après les valeurs : degrés, Lambert 72, Lambert 2008 ou Web Mercator."""
    if -180 <= x <= 180 and -90 <= y <= 90:
        return 4326
    if 0 < x < 400000 and 0 < y < 400000:
        return 31370
    if 400000 <= x < 900000 and 400000 <= y < 900000:
        return 3812
    if 200000 < abs(x) < 2000000 and 5500000 < y < 8000000:
        return 3857
    return None


def epsg_from_text(text):
    """EPSG lu dans un .prj, un crs GeoJSON ou une définition de GeoPackage."""
    t = (text or "").lower()
    for code in (31370, 3812, 3857, 4326, 4258):
        if str(code) in t:
            return 4326 if code == 4258 else code
    if "belge_lambert_1972" in t or "belge 1972" in t or "bd72" in t:
        return 31370
    if "lambert_2008" in t or "lambert 2008" in t:
        return 3812
    if "pseudo-mercator" in t or "web_mercator" in t:
        return 3857
    if "wgs_1984" in t or "wgs 84" in t or "crs84" in t or "etrs" in t or "geogcs" in t:
        return 4326
    return None


def reproject(geom, epsg=None):
    """Ramène une géométrie en WGS84 ; le système est deviné si epsg est inconnu."""
    if not geom:
        return None
    if not epsg:
        first = next(iter_coords(geom), None)
        epsg = guess_epsg(first[0], first[1]) if first else 4326
    if epsg in (4326, None):
        return geom
    if epsg not in (31370, 3812, 3857):
        return None
    return map_coords(geom, lambda x, y: to_wgs84(x, y, epsg))
