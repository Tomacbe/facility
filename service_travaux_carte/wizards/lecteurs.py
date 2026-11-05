"""Lecture des fichiers exportés de QGIS (GeoJSON, GeoPackage, Shapefile zippé) sans dépendance.

Chaque lecteur rend une liste de couches : {"name", "features": [{"id", "props", "geom"}]} avec des
géométries GeoJSON déjà ramenées en WGS84.
"""

import io
import json
import os
import sqlite3
import struct
import tempfile
import zipfile

from ..models import geo


class LectureErreur(Exception):
    pass


# ---------------------------------------------------------------- WKB (GeoPackage)
def _wkb(buf, off=0):
    """Géométrie WKB (ISO ou EWKB, 2D/3D/M) → (geojson, nouvel offset)."""
    bo = "<" if buf[off] == 1 else ">"
    off += 1
    (code,) = struct.unpack_from(bo + "I", buf, off)
    off += 4
    has_z = bool(code & 0x80000000)
    has_m = bool(code & 0x40000000)
    if code & 0x20000000:  # SRID EWKB
        off += 4
    code &= 0x0FFFFFFF
    if code >= 3000:
        has_z = has_m = True
        code -= 3000
    elif code >= 2000:
        has_m = True
        code -= 2000
    elif code >= 1000:
        has_z = True
        code -= 1000
    dim = 2 + has_z + has_m

    def pts(n):
        nonlocal off
        out = []
        for _i in range(n):
            v = struct.unpack_from(bo + "d" * dim, buf, off)
            off += 8 * dim
            out.append([v[0], v[1]])
        return out

    def count():
        nonlocal off
        (n,) = struct.unpack_from(bo + "I", buf, off)
        off += 4
        return n

    if code == 1:
        return {"type": "Point", "coordinates": pts(1)[0]}, off
    if code == 2:
        return {"type": "LineString", "coordinates": pts(count())}, off
    if code == 3:
        return {"type": "Polygon", "coordinates": [pts(count()) for _r in range(count())]}, off
    if code in (4, 5, 6, 7):
        parts = []
        for _i in range(count()):
            g, off = _wkb(buf, off)
            parts.append(g)
        if code == 4:
            return {"type": "MultiPoint", "coordinates": [g["coordinates"] for g in parts]}, off
        if code == 5:
            return {"type": "MultiLineString", "coordinates": [g["coordinates"] for g in parts]}, off
        if code == 6:
            return {"type": "MultiPolygon", "coordinates": [g["coordinates"] for g in parts]}, off
        # collection : on garde la première géométrie utile
        return (parts[0] if parts else None), off
    raise LectureErreur("Géométrie non gérée (code WKB %s)." % code)


def _gpkg_geom(blob):
    if not blob or blob[:2] != b"GP":
        return None
    flags = blob[3]
    env = (flags >> 1) & 0x07
    if flags & 0x10:  # géométrie vide
        return None
    off = 8 + [0, 32, 48, 48, 64][env] if env < 5 else 8
    return _wkb(bytes(blob), off)[0]


def lire_gpkg(data):
    with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        con = sqlite3.connect(path)
        try:
            cols = con.execute("SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns").fetchall()
        except sqlite3.DatabaseError as err:
            raise LectureErreur("Ce fichier n'est pas un GeoPackage lisible (%s)." % err) from err
        srs = {}
        try:
            for sid, org, code, definition in con.execute(
                    "SELECT srs_id, organization, organization_coordsys_id, definition FROM gpkg_spatial_ref_sys"):
                srs[sid] = code if (org or "").upper() == "EPSG" else geo.epsg_from_text(definition)
        except sqlite3.DatabaseError:
            pass
        couches = []
        for table, gcol, sid in cols:
            epsg = srs.get(sid) or (sid if sid in (4326, 31370, 3812, 3857) else None)
            cur = con.execute('SELECT * FROM "%s"' % table.replace('"', '""'))
            names = [d[0] for d in cur.description]
            feats = []
            for row in cur:
                rec = dict(zip(names, row, strict=False))
                g = _gpkg_geom(rec.pop(gcol, None))
                g = geo.reproject(g, epsg if epsg != 4326 else None) if g else None
                if not g:
                    continue
                fid = rec.get("fid") or rec.get("id") or rec.get("ogc_fid")
                props = {k: v for k, v in rec.items() if isinstance(v, (str, int, float)) or v is None}
                feats.append({"id": str(fid) if fid is not None else None, "props": props, "geom": g})
            couches.append({"name": table, "features": feats})
        return couches
    finally:
        con.close() if "con" in locals() else None
        os.unlink(path)


# ---------------------------------------------------------------- Shapefile
def _dbf(data, encoding):
    n, hlen, rlen = struct.unpack_from("<IHH", data, 4)
    fields, off = [], 32
    while off < hlen - 1 and data[off] != 0x0D:
        name = data[off:off + 11].split(b"\0")[0].decode("latin-1")
        ftype = chr(data[off + 11])
        flen = data[off + 16]
        fields.append((name, ftype, flen))
        off += 32
    rows, off = [], hlen
    for _i in range(n):
        rec = data[off:off + rlen]
        off += rlen
        if not rec or rec[:1] == b"*":
            rows.append(None)
            continue
        p, row = 1, {}
        for name, ftype, flen in fields:
            raw = rec[p:p + flen]
            p += flen
            try:
                txt = raw.decode(encoding).strip()
            except UnicodeDecodeError:
                txt = raw.decode("latin-1").strip()
            if ftype in "NF":
                try:
                    v = float(txt) if txt else None
                    row[name] = int(v) if v is not None and v.is_integer() and "." not in txt else v
                except ValueError:
                    row[name] = None
            elif ftype == "L":
                row[name] = txt[:1].upper() in "TY" if txt else None
            else:
                row[name] = txt or None
        rows.append(row)
    return rows


def _ring_area(ring):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=False)) / 2


def _shp(data):
    out, off = [], 100
    while off + 8 <= len(data):
        (_num, clen) = struct.unpack_from(">ii", data, off)
        off += 8
        rec = data[off:off + clen * 2]
        off += clen * 2
        (st,) = struct.unpack_from("<i", rec, 0) if len(rec) >= 4 else (0,)
        base = st % 10 if st not in (0,) else 0
        if st == 0:
            out.append(None)
        elif base == 1:  # point (1, 11, 21)
            x, y = struct.unpack_from("<dd", rec, 4)
            out.append({"type": "Point", "coordinates": [x, y]})
        elif base == 8:  # multipoint
            (npts,) = struct.unpack_from("<i", rec, 36)
            pts = [list(struct.unpack_from("<dd", rec, 40 + 16 * i)) for i in range(npts)]
            out.append({"type": "MultiPoint", "coordinates": pts})
        elif base in (3, 5):  # polyligne, polygone
            nparts, npts = struct.unpack_from("<ii", rec, 36)
            parts = list(struct.unpack_from("<%di" % nparts, rec, 44))
            p0 = 44 + 4 * nparts
            pts = [list(struct.unpack_from("<dd", rec, p0 + 16 * i)) for i in range(npts)]
            rings = [pts[parts[i]:(parts[i + 1] if i + 1 < nparts else npts)] for i in range(nparts)]
            if base == 3:
                out.append({"type": "LineString", "coordinates": rings[0]} if len(rings) == 1
                           else {"type": "MultiLineString", "coordinates": rings})
            else:
                polys = []
                for r in rings:  # extérieur dans le sens horaire (aire < 0), trous en sens inverse
                    if _ring_area(r) <= 0 or not polys:
                        polys.append([r])
                    else:
                        polys[-1].append(r)
                out.append({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                           else {"type": "MultiPolygon", "coordinates": polys})
        else:
            out.append(None)
    return out


def lire_shapefile_zip(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise LectureErreur("Archive zip illisible.") from err
    names = z.namelist()
    couches = []
    for shp in [n for n in names if n.lower().endswith(".shp")]:
        stem = shp[:-4]

        def part(ext, stem=stem):
            for n in names:
                if n.lower() == (stem + ext).lower():
                    return z.read(n)
            return None
        dbf = part(".dbf")
        prj = part(".prj")
        cpg = part(".cpg")
        encoding = (cpg.decode("ascii", "ignore").strip() if cpg else "") or "utf-8"
        if encoding.upper() in ("UTF8", "UTF-8"):
            encoding = "utf-8"
        epsg = geo.epsg_from_text(prj.decode("latin-1")) if prj else None
        geoms = _shp(z.read(shp))
        try:
            rows = _dbf(dbf, encoding) if dbf else [{} for _g in geoms]
        except LookupError:
            rows = _dbf(dbf, "latin-1")
        feats = []
        for i, g in enumerate(geoms):
            row = rows[i] if i < len(rows) else {}
            if not g or row is None:
                continue
            g = geo.reproject(g, epsg if epsg != 4326 else None)
            if g:
                feats.append({"id": str(i + 1), "props": row or {}, "geom": g})
        couches.append({"name": os.path.basename(stem), "features": feats})
    if not couches:
        raise LectureErreur("Aucun fichier .shp dans l'archive.")
    return couches


# ---------------------------------------------------------------- GeoJSON
def features_geojson(data, name="couche"):
    """FeatureCollection (texte, octets ou dict) → couche ; le système (crs) est lu ou deviné."""
    if isinstance(data, (bytes, str)):
        try:
            data = json.loads(data)
        except ValueError as err:
            raise LectureErreur("GeoJSON illisible.") from err
    if not isinstance(data, dict):
        raise LectureErreur("GeoJSON illisible.")
    if data.get("type") == "Feature":
        feats = [data]
    elif data.get("type") == "FeatureCollection":
        feats = data.get("features") or []
    else:
        feats = [{"type": "Feature", "geometry": data, "properties": {}}]
    crs = data.get("crs")
    epsg = geo.epsg_from_text(json.dumps(crs)) if crs else None
    out = []
    for i, f in enumerate(feats):
        g = (f or {}).get("geometry")
        if not g or not g.get("coordinates"):
            continue
        if g.get("type") == "GeometryCollection":
            continue
        g = geo.reproject({"type": g["type"], "coordinates": g["coordinates"]}, epsg if epsg != 4326 else None)
        if not g:
            continue
        props = {k: v for k, v in (f.get("properties") or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}
        fid = f.get("id")
        out.append({"id": str(fid) if fid not in (None, "") else str(i + 1), "props": props, "geom": g})
    return {"name": name, "features": out}


def lire_fichier(data, filename):
    nom = (filename or "").lower()
    base = os.path.splitext(os.path.basename(filename or "couche"))[0]
    if nom.endswith(".gpkg") or data[:15] == b"SQLite format 3":
        return lire_gpkg(data)
    if nom.endswith(".zip") or data[:2] == b"PK":
        return lire_shapefile_zip(data)
    if nom.endswith((".geojson", ".json")) or data.lstrip()[:1] == b"{":
        return [features_geojson(data, base)]
    raise LectureErreur("Format non reconnu : déposez un .geojson, un .gpkg ou un Shapefile zippé (.zip).")
