"""Importer des données géographiques dans FACILITY : catalogue SPW (ArcGIS REST), service WFS,
Open Data Wallonie-Bruxelles (ODWB) ou fichier exporté de QGIS (GeoJSON, GeoPackage, Shapefile zippé).

Étapes : 1. source  2. couches et zone  3. correspondance (valeur d'un champ → # objet ou # lieu)  4. résultat.
Chaque entité garde son identifiant d'origine (ext_ref) : un nouvel import met à jour au lieu de dupliquer,
sans écraser une forme corrigée à la main dans FACILITY.
"""

import base64
import json
import logging
import re
import xml.etree.ElementTree as ET

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.service_travaux.models.lieu import tag_format

from ..models import geo
from ..models.lieu import COULEURS
from . import lecteurs
from .import_picc import NATURES

_logger = logging.getLogger(__name__)

SPW_ROOT = "https://geoservices.wallonie.be/arcgis/rest/services"
SPW_PICC = SPW_ROOT + "/TOPOGRAPHIE/PICC_VDIFF/MapServer"
ODWB_API = "https://www.odwb.be/api/explore/v2.1"
MAX_FEATURES = 20000
TIMEOUT = 60
# champs qui portent souvent la « nature » de l'entité
CHAMPS_CLASSE = ("natur_desc", "nature", "type", "categorie", "category", "classe", "class", "typologie", "genre",
                 "essence", "espece", "layer", "theme")
CHAMPS_NOM = ("nom", "name", "libelle", "label", "denomination", "titre", "title", "adresse", "nom_lieu")
SOURCES = [("spw", "Catalogue SPW (ArcGIS REST)"), ("wfs", "Service WFS"), ("odwb", "ODWB (Open Data Wallonie-Bruxelles)"),
           ("fichier", "Fichier QGIS")]


def _camel(text):
    words = re.findall(r"[0-9A-Za-zÀ-ÿ]+", str(text or ""))
    return "".join(w[:1].upper() + w[1:] for w in words)[:40] or "Objet"


def _get(url, params=None, what="le service"):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r
    except requests.RequestException as err:
        raise UserError(_("%(w)s ne répond pas (%(e)s). Vérifiez l'adresse et l'accès Internet du serveur.",
                          w=what, e=err)) from err


def _json(resp, what="le service"):
    try:
        data = resp.json()
    except ValueError as err:
        raise UserError(_("%s n'a pas renvoyé de données lisibles.", what)) from err
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise UserError(_("%(w)s a renvoyé une erreur : %(e)s", w=what,
                          e=err.get("message") if isinstance(err, dict) else err))
    return data


def _esri_geom(g):
    """Géométrie ArcGIS JSON → GeoJSON."""
    if not g:
        return None
    if "x" in g:
        return {"type": "Point", "coordinates": [g["x"], g["y"]]}
    if g.get("points"):
        return {"type": "MultiPoint", "coordinates": [p[:2] for p in g["points"]]}
    if g.get("paths"):
        paths = [[p[:2] for p in path] for path in g["paths"]]
        return {"type": "LineString", "coordinates": paths[0]} if len(paths) == 1 else {"type": "MultiLineString", "coordinates": paths}
    if g.get("rings"):
        polys = []
        for ring in g["rings"]:
            r = [p[:2] for p in ring]
            if lecteurs._ring_area(r) <= 0 or not polys:
                polys.append([r])
            else:
                polys[-1].append(r)
        return {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}
    return None


def _fix_axis(g):
    """Coordonnées renvoyées en (lat, lon) par certains WFS : on les remet en (lon, lat)."""
    first = next(geo.iter_coords(g), None)
    if first and 49 <= first[0] <= 52 and 2 <= first[1] <= 7:
        return geo.map_coords(g, lambda x, y: (y, x))
    return g


class TravauxImportGeo(models.TransientModel):
    _name = "travaux.import.geo"
    _description = "Importer des données géographiques"

    state = fields.Selection([("source", "Source"), ("couches", "Couches et zone"), ("classes", "Correspondance"),
                              ("done", "Résultat")], default="source")
    source = fields.Selection(SOURCES, "Source", default="spw", required=True)
    url = fields.Char("Adresse du service", default=SPW_PICC,
                      help="SPW : un service MapServer (ou le catalogue https://geoservices.wallonie.be/arcgis/rest/services). "
                      "WFS : l'adresse du service, avec ou sans ?service=WFS&request=GetCapabilities.")
    recherche = fields.Char("Rechercher sur ODWB", default="arbres remarquables")
    fichier = fields.Binary("Fichier", attachment=False)
    fichier_nom = fields.Char("Nom du fichier")
    couche_ids = fields.One2many("travaux.import.geo.couche", "wizard_id", "Couches")
    zone = fields.Selection([("lieu", "Autour d'un # lieu"), ("rect", "Rectangle (coordonnées)"),
                             ("tout", "Tout le contenu")], default="lieu", required=True,
                            help="« Tout le contenu » : tout le fichier, ou toute la couche du service (limité à %s entités)." % MAX_FEATURES)
    lieu_id = fields.Many2one("travaux.lieu", "# Lieu", domain=[("kind", "=", "lieu"), ("latitude", "!=", 0)])
    rayon = fields.Integer("Rayon (m)", default=250)
    min_lng = fields.Float("Longitude min", digits=(10, 6))
    min_lat = fields.Float("Latitude min", digits=(10, 6))
    max_lng = fields.Float("Longitude max", digits=(10, 6))
    max_lat = fields.Float("Latitude max", digits=(10, 6))
    features_json = fields.Text()
    total = fields.Integer("Entités chargées", readonly=True)
    champ_ids = fields.One2many("travaux.import.geo.champ", "wizard_id")
    champ_id = fields.Many2one("travaux.import.geo.champ", "Classer selon le champ",
                               domain="[('wizard_id', '=', id)]",
                               help="Les entités sont regroupées par valeur de ce champ ; vide : tout dans une seule classe.")
    nom_champ_id = fields.Many2one("travaux.import.geo.champ", "Champ copié dans la remarque / le nom",
                                   domain="[('wizard_id', '=', id)]")
    classe_ids = fields.One2many("travaux.import.geo.classe", "wizard_id", "Correspondance")
    result = fields.Text("Résultat", readonly=True)
    info = fields.Char(readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        mc = self.env["travaux.lieu"]._by_name("MaisonCommunale")
        if mc and mc.latitude and "lieu_id" in fields_list and not res.get("lieu_id"):
            res["lieu_id"] = mc.id
        return res

    @api.onchange("source")
    def _onchange_source(self):
        if self.source == "spw" and (not self.url or "wallonie" not in self.url):
            self.url = SPW_PICC
        elif self.source == "wfs" and self.url and "wallonie.be/arcgis" in self.url:
            self.url = False

    def _reopen(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": _("Importer des données")}

    def _bbox(self):
        self.ensure_one()
        if self.zone == "tout":
            return None
        if self.zone == "lieu":
            if not self.lieu_id:
                raise UserError(_("Choisissez le # lieu autour duquel importer."))
            return geo.bbox_around(self.lieu_id.latitude, self.lieu_id.longitude, max(self.rayon, 20))
        box = (self.min_lng, self.min_lat, self.max_lng, self.max_lat)
        if not all(box) or box[0] >= box[2] or box[1] >= box[3]:
            raise UserError(_("Le rectangle est incomplet."))
        return box

    # ================================================================ 1. source → couches
    def action_lire(self):
        self.ensure_one()
        lines, info = getattr(self, "_lire_" + self.source)()
        self.couche_ids.unlink()
        self.write({"couche_ids": [(0, 0, vals) for vals in lines], "state": "couches", "info": info or False})
        return self._reopen()

    def _lire_fichier(self):
        if not self.fichier:
            raise UserError(_("Déposez un fichier exporté de QGIS : .geojson, .gpkg ou Shapefile zippé (.zip)."))
        try:
            couches = lecteurs.lire_fichier(base64.b64decode(self.fichier), self.fichier_nom or "")
        except lecteurs.LectureErreur as err:
            raise UserError(str(err)) from err
        feats = []
        lines = []
        for c in couches:
            kinds = {}
            for f in c["features"]:
                k = geo.kind(f["geom"])
                kinds[k] = kinds.get(k, 0) + 1
                feats.append({"c": c["name"], "id": f["id"], "p": f["props"], "g": f["geom"]})
            if c["features"]:
                lines.append({"name": c["name"], "code": c["name"], "kind": "layer", "importer": True,
                              "geom": max(kinds, key=kinds.get), "nombre": len(c["features"])})
        if not feats:
            raise UserError(_("Aucune entité géographique trouvée dans ce fichier."))
        self.features_json = json.dumps(feats)
        return lines, _("%(n)s entités dans %(c)s couche(s), converties en WGS84.", n=len(feats), c=len(lines))

    def _lire_spw(self):
        url = (self.url or SPW_PICC).split("?")[0].rstrip("/")
        data = _json(_get(url, {"f": "json"}, _("Le géoportail")), _("Le géoportail"))
        lines = []
        if data.get("layers") is not None or data.get("type") in ("Feature Layer", "Table"):
            layers = data.get("layers") or [dict(data, id=url.rsplit("/", 1)[-1])]
            base = url if data.get("layers") is not None else url.rsplit("/", 1)[0]
            for lay in layers:
                if lay.get("subLayerIds"):
                    continue  # groupe de couches
                g = {"esriGeometryPoint": "point", "esriGeometryMultipoint": "point", "esriGeometryPolyline": "ligne",
                     "esriGeometryPolygon": "surface"}.get(lay.get("geometryType"), False)
                code = "%s/%s" % (base, lay.get("id"))
                # PICC : la couche des équipements ponctuels (avaloirs, chambres de visite, poteaux…) cochée d'office
                on = code == SPW_PICC + "/4" or (len(layers) == 1)
                lines.append({"name": lay.get("name"), "code": code, "kind": "layer", "geom": g, "importer": on})
            return lines, _("%s couche(s) dans ce service. Cochez celles à importer.", len(lines))
        for folder in data.get("folders") or []:
            lines.append({"name": folder, "code": "%s/%s" % (url, folder), "kind": "service", "geom": False})
        root = url[:url.index("/rest/services") + len("/rest/services")] if "/rest/services" in url else url
        for srv in data.get("services") or []:
            if srv.get("type") in ("MapServer", "FeatureServer"):
                name = srv["name"].split("/")[-1]
                lines.append({"name": "%s (%s)" % (name, srv["type"]), "kind": "service", "geom": False,
                              "code": "%s/%s/%s" % (root, srv["name"], srv["type"])})
        if not lines:
            raise UserError(_("Aucune couche ni service à cette adresse."))
        return lines, _("Catalogue : ouvrez un dossier ou un service avec le bouton « Ouvrir ».")

    def _wfs_base(self):
        url = (self.url or "").strip()
        if not url:
            raise UserError(_("Indiquez l'adresse du service WFS."))
        return url.split("?")[0], {k.lower(): v for k, v in
                                   (p.split("=", 1) for p in (url.split("?", 1)[1].split("&") if "?" in url else []) if "=" in p)
                                   if k.lower() not in ("service", "request", "version", "typename", "typenames")}

    def _lire_wfs(self):
        base, extra = self._wfs_base()
        resp = _get(base, dict(extra, service="WFS", request="GetCapabilities", version="1.1.0"), _("Le service WFS"))
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as err:
            raise UserError(_("Le service WFS n'a pas renvoyé de capacités lisibles.")) from err
        lines = []
        for ft in root.iter():
            if ft.tag.split("}")[-1] != "FeatureType":
                continue
            name = title = None
            for child in ft:
                tag = child.tag.split("}")[-1]
                if tag == "Name":
                    name = (child.text or "").strip()
                elif tag == "Title":
                    title = (child.text or "").strip()
            if name:
                lines.append({"name": title or name, "code": name, "kind": "layer", "geom": False})
        if not lines:
            raise UserError(_("Aucune couche (FeatureType) dans ce service WFS."))
        return lines, _("%s couche(s) dans ce service WFS.", len(lines))

    def _lire_odwb(self):
        q = (self.recherche or "").strip()
        params = {"limit": 30}
        if q:
            params["where"] = '"%s"' % q.replace('"', " ")
        data = _json(_get(ODWB_API + "/catalog/datasets", params, "ODWB"), "ODWB")
        lines = []
        for ds in data.get("results") or []:
            feats = ds.get("features") or []
            if "geo" not in feats:
                continue
            title = ((ds.get("metas") or {}).get("default") or {}).get("title") or ds.get("dataset_id")
            gfield = next((f["name"] for f in ds.get("fields") or [] if f.get("type") == "geo_point_2d"), "")
            shape = any(f.get("type") == "geo_shape" for f in ds.get("fields") or [])
            lines.append({"name": title, "code": "%s|%s" % (ds["dataset_id"], gfield), "kind": "layer",
                          "geom": False if shape else "point",
                          "nombre": ds.get("records_count") or ds.get("metas", {}).get("default", {}).get("records_count") or 0})
        if not lines:
            raise UserError(_("Aucun jeu de données géographique ODWB ne correspond à « %s ».", q))
        return lines, _("%s jeu(x) de données géographiques trouvés sur ODWB.", len(lines))

    # ================================================================ 2. couches + zone → entités
    def action_charger(self):
        self.ensure_one()
        couches = self.couche_ids.filtered(lambda c: c.importer and c.kind == "layer")
        if not couches:
            raise UserError(_("Cochez au moins une couche."))
        box = self._bbox()
        feats = []
        for c in couches:
            if self.source == "fichier":
                stored = [f for f in json.loads(self.features_json or "[]") if f["c"] == c.code]
                got = [f for f in stored if not box or geo.intersects_bbox(f["g"], box)]
            else:
                got = getattr(self, "_fetch_" + self.source)(c, box)
            c.nombre = len(got)
            feats.extend(got)
            if len(feats) > MAX_FEATURES:
                raise UserError(_("Plus de %s entités : réduisez la zone.", MAX_FEATURES))
        if not feats:
            raise UserError(_("Aucune entité dans la zone choisie."))
        self.features_json = json.dumps(feats)
        self.total = len(feats)
        self._set_champs(feats)
        self._classer()
        self.state = "classes"
        return self._reopen()

    def _fetch_spw(self, couche, box):
        url = couche.code + "/query"
        feats, offset = [], 0
        while True:
            params = {"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson",
                      "resultOffset": offset, "resultRecordCount": 1000}
            if box:
                params.update(geometry="%s,%s,%s,%s" % box, geometryType="esriGeometryEnvelope", inSR=4326,
                              spatialRel="esriSpatialRelIntersects")
            data = _json(_get(url, params, _("Le géoportail")), _("Le géoportail"))
            if "features" in data and data.get("type") != "FeatureCollection" and data.get("geometryType"):
                batch = [{"type": "Feature", "properties": f.get("attributes") or {}, "geometry": _esri_geom(f.get("geometry"))}
                         for f in data.get("features") or []]
            else:
                batch = data.get("features") or []
            layer = lecteurs.features_geojson({"type": "FeatureCollection", "features": batch}, couche.name)
            for f in layer["features"]:
                p = f["props"]
                fid = p.get("GEOREF_ID") or p.get("OBJECTID") or p.get("objectid") or f["id"]
                feats.append({"c": couche.name, "id": "%s-%s" % (offset, f["id"]) if fid is None else str(fid),
                              "p": p, "g": f["geom"]})
            more = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
            if not batch or not more or len(feats) > MAX_FEATURES:
                return feats
            offset += len(batch)

    def _fetch_wfs(self, couche, box):
        base, extra = self._wfs_base()
        params = dict(extra, service="WFS", request="GetFeature", version="1.1.0", typeName=couche.code,
                      outputFormat="application/json", srsName="EPSG:4326", maxFeatures=MAX_FEATURES)
        if box:
            params["bbox"] = "%s,%s,%s,%s,EPSG:4326" % box
        resp = _get(base, params, _("Le service WFS"))
        try:
            data = resp.json()
        except ValueError:
            params["outputFormat"] = "json"
            try:
                data = _get(base, params, _("Le service WFS")).json()
            except ValueError as err:
                raise UserError(_("Le service WFS ne sait pas renvoyer du GeoJSON pour « %s ».", couche.name)) from err
        layer = lecteurs.features_geojson(data, couche.name)
        out = []
        for f in layer["features"]:
            g = _fix_axis(f["geom"])
            if box and not geo.intersects_bbox(g, box):
                continue
            out.append({"c": couche.name, "id": f["id"], "p": f["props"], "g": g})
        return out

    def _fetch_odwb(self, couche, box):
        dataset, gfield = (couche.code.split("|") + [""])[:2]
        params = {"limit": -1}
        if box and gfield:
            params["where"] = "in_bbox(%s, %s, %s, %s, %s)" % (gfield, box[1], box[0], box[3], box[2])
        data = _json(_get("%s/catalog/datasets/%s/exports/geojson" % (ODWB_API, dataset), params, "ODWB"), "ODWB")
        layer = lecteurs.features_geojson(data, couche.name)
        out = []
        for f in layer["features"]:
            if box and not geo.intersects_bbox(f["geom"], box):
                continue
            p = f["props"]
            fid = p.get("id") or p.get("identifiant") or p.get("objectid") or f["id"]
            out.append({"c": couche.name, "id": str(fid), "p": p, "g": f["geom"]})
        return out

    # ================================================================ 3. correspondance
    def _set_champs(self, feats):
        stats = {}
        for f in feats[:5000]:
            for k, v in f["p"].items():
                if v in (None, ""):
                    continue
                s = stats.setdefault(k, {"values": set(), "ex": []})
                if len(s["values"]) < 200:
                    s["values"].add(str(v))
                if len(s["ex"]) < 3 and str(v) not in s["ex"]:
                    s["ex"].append(str(v)[:30])
        self.champ_ids.unlink()
        self.champ_id = self.nom_champ_id = False
        champs = self.env["travaux.import.geo.champ"].create([
            {"wizard_id": self.id, "name": k, "nb_valeurs": len(s["values"]), "exemples": ", ".join(s["ex"])}
            for k, s in sorted(stats.items())])
        by = {c.name.lower(): c for c in champs}
        classe = next((by[k] for k in CHAMPS_CLASSE if k in by and 1 < by[k].nb_valeurs < 60), False)
        if not classe:
            classe = next((by[k] for k in CHAMPS_CLASSE if k in by), False)
        nom = next((by[k] for k in CHAMPS_NOM if k in by), False)
        self.write({"champ_id": classe.id if classe else False, "nom_champ_id": nom.id if nom else False})

    def action_classer(self):
        self.ensure_one()
        self._classer()
        return self._reopen()

    def _classer(self):
        Lieu = self.env["travaux.lieu"]
        feats = json.loads(self.features_json or "[]")
        champ = self.champ_id.name
        groups = {}
        for f in feats:
            val = f["p"].get(champ) if champ else None
            key = (f["c"], str(val) if val not in (None, "") else "")
            g = groups.setdefault(key, {"n": 0, "kinds": {}})
            g["n"] += 1
            k = geo.kind(f["g"])
            g["kinds"][k] = g["kinds"].get(k, 0) + 1
        lines = []
        many = len(groups) > 6
        for (couche, val), g in sorted(groups.items(), key=lambda x: -x[1]["n"]):
            kind = max(g["kinds"], key=g["kinds"].get)
            label = val or (couche if not champ else _("(vide)"))
            nat = NATURES.get(val)
            tag = nat[0] if nat else _camel(label if val or not champ else couche)
            t = Lieu._by_name(tag)
            if t and t.kind != "objet":
                t = Lieu.browse()
            if t and t.geom_type != kind and not (t.geom_type == "point" and kind == "surface"):
                t = Lieu.browse()
            on = bool(t) or (nat[2] if nat else not many)
            lines.append({"couche": couche, "valeur": label, "valeur_brute": val, "geom": kind, "nombre": g["n"],
                          "importer": on, "cible": "objet", "type_id": t.id if t else False,
                          "nouveau": False if t else tag})
        self.classe_ids.unlink()
        self.write({"classe_ids": [(0, 0, v) for v in lines]})

    # ================================================================ 4. import
    def action_importer(self):
        self.ensure_one()
        res = self._importer()
        self.write({"state": "done", "result": res["text"]})
        return self._reopen()

    def _type_for(self, line):
        Lieu = self.env["travaux.lieu"].sudo()
        if line.type_id:
            return line.type_id
        name = _camel(line.nouveau or line.valeur)
        t = Lieu._by_name(name)
        if t and t.kind == "objet":
            return t
        nat = NATURES.get(line.valeur_brute or "")
        return Lieu.create({"name": name, "kind": "objet", "state": "valide", "geom_type": line.geom,
                            "prefixe": (nat[1] if nat else name[:2]).upper(),
                            "couleur": COULEURS.get(name.lower(), False)})

    def _lieux_index(self):
        Lieu = self.env["travaux.lieu"]
        lieux = Lieu.search([("kind", "=", "lieu"), ("state", "=", "valide")])
        contours = [(lieu, geo.loads(lieu.geojson)) for lieu in lieux.filtered("has_contour")]
        contours = [(lieu, g, geo.bbox(g)) for lieu, g in contours if g]
        points = [(lieu, lieu.latitude, lieu.longitude) for lieu in lieux.filtered(lambda x: not x.has_contour and x.latitude)]

        def find(lat, lng):
            for lieu, g, b in contours:
                if b[0] <= lng <= b[2] and b[1] <= lat <= b[3] and geo.contains(g, lng, lat):
                    return lieu.id
            best, best_d = False, 150
            for lieu, la, lo in points:
                d = geo.distance_m(lat, lng, la, lo)
                if d is not None and d <= best_d:
                    best, best_d = lieu.id, d
            return best
        return find

    def _importer(self):
        Objet = self.env["travaux.objet"].sudo().with_context(active_test=False)
        Lieu = self.env["travaux.lieu"].sudo().with_context(active_test=False)
        feats = json.loads(self.features_json or "[]")
        champ = self.champ_id.name
        nom = self.nom_champ_id.name
        lines = {}
        for line in self.classe_ids.filtered("importer"):
            lines[(line.couche, line.valeur_brute or "")] = line
        types = {}
        find_lieu = self._lieux_index()
        created = updated = ignores = lieux_crees = lieux_maj = 0
        to_create = []
        existing_refs = {}
        refs = ["%s:%s:%s" % (self.source, f["c"], f["id"]) for f in feats]
        for o in Objet.search([("ext_ref", "in", refs)]):
            existing_refs[o.ext_ref] = o
        for f, ref in zip(feats, refs, strict=True):
            val = f["p"].get(champ) if champ else None
            line = lines.get((f["c"], str(val) if val not in (None, "") else ""))
            if not line:
                continue
            g = f["g"]
            label = f["p"].get(nom) if nom else None
            if line.cible == "lieu":
                if geo.kind(g) != "surface":
                    ignores += 1
                    continue
                lieu = Lieu.search([("carte_ext_ref", "=", ref)], limit=1)
                name = str(label or "%s %s" % (line.valeur, f["id"]))[:80]
                if not lieu:
                    lieu = Lieu._by_name(tag_format(name))
                    lieu = lieu if lieu and lieu.kind == "lieu" and not lieu.has_contour else Lieu.browse()
                if lieu:
                    lieu.write({"geojson": json.dumps(g), "carte_ext_ref": ref, "active": True})
                    lieux_maj += 1
                else:
                    Lieu.create({"name": name, "kind": "lieu", "state": "valide", "geojson": json.dumps(g),
                                 "carte_ext_ref": ref})
                    lieux_crees += 1
                continue
            t = types.get(line.id) or self._type_for(line)
            types[line.id] = t
            k = geo.kind(g)
            if t.geom_type == "point" and k != "point":
                c = geo.centroid(g)
                g = {"type": "Point", "coordinates": [c[0], c[1]]}
            elif t.geom_type == "point" and g["type"] == "MultiPoint":
                g = {"type": "Point", "coordinates": g["coordinates"][0]}
            elif t.geom_type != k:
                ignores += 1
                continue
            obj = existing_refs.get(ref)
            if obj is True:  # même entité deux fois dans la même couche
                continue
            picc_id = str(f["p"].get("GEOREF_ID")) if f["p"].get("GEOREF_ID") else False
            if not obj and picc_id:
                obj = Objet.search([("picc_id", "=", picc_id)], limit=1)
            if not obj and t.geom_type == "point":
                obj = Objet._proche(g["coordinates"][1], g["coordinates"][0], t.id)
                if obj and obj.ext_ref:
                    obj = Objet.browse()  # un autre objet importé au même endroit
            if obj:
                vals = {"ext_ref": ref} if not obj.ext_ref else {}
                if not obj.geom_modifie and obj.type_id == t:
                    vals.update(Objet._geom_vals(g))
                if label and not obj.note:
                    vals["note"] = str(label)[:120]
                if vals:
                    obj.write(vals)
                updated += 1
                continue
            gv = Objet._geom_vals(g)
            vals = dict(gv, type_id=t.id, source=self.source, ext_ref=ref, state="valide",
                        lieu_id=find_lieu(gv["latitude"], gv["longitude"]))
            if picc_id and not Objet.search_count([("picc_id", "=", picc_id)]):
                vals.update(picc_id=picc_id, picc_nature=f["p"].get("NATUR_DESC") or False)
            if label:
                vals["note"] = str(label)[:120]
            to_create.append(vals)
            existing_refs[ref] = True
        if to_create:
            Objet.create(to_create)
            created = len(to_create)
        parts = [_("%(c)s objet(s) créé(s), %(u)s déjà présent(s) mis à jour.", c=created, u=updated)]
        if lieux_crees or lieux_maj:
            parts.append(_("%(c)s # lieu(x) créé(s), %(u)s mis à jour.", c=lieux_crees, u=lieux_maj))
        if ignores:
            parts.append(_("%s entité(s) ignorée(s) : géométrie incompatible avec le # choisi.", ignores))
        text = " ".join(parts)
        _logger.info("FACILITY carte : import %s, %s", self.source, text)
        return {"created": created, "updated": updated, "lieux": lieux_crees + lieux_maj, "ignores": ignores, "text": text}

    def action_retour(self):
        self.ensure_one()
        self.state = {"couches": "source", "classes": "couches", "done": "classes"}.get(self.state, "source")
        return self._reopen()

    def action_voir_carte(self):
        return {"type": "ir.actions.client", "tag": "travaux_carte", "name": _("Carte"),
                "params": {"lieu_id": self.lieu_id.id} if self.zone == "lieu" and self.lieu_id else {}}


class TravauxImportGeoCouche(models.TransientModel):
    _name = "travaux.import.geo.couche"
    _description = "Couche disponible à l'import"

    wizard_id = fields.Many2one("travaux.import.geo", required=True, ondelete="cascade")
    name = fields.Char("Couche", readonly=True)
    code = fields.Char(readonly=True)
    kind = fields.Selection([("layer", "Couche"), ("service", "Dossier / service")], default="layer", readonly=True)
    geom = fields.Selection([("point", "Point"), ("ligne", "Ligne"), ("surface", "Surface")], "Géométrie", readonly=True)
    nombre = fields.Integer("Entités", readonly=True)
    importer = fields.Boolean("Importer")

    def action_ouvrir(self):
        """Catalogue SPW : descendre dans un dossier ou un service."""
        self.ensure_one()
        wiz = self.wizard_id
        wiz.url = self.code
        return wiz.action_lire()


class TravauxImportGeoChamp(models.TransientModel):
    _name = "travaux.import.geo.champ"
    _description = "Champ des entités importées"
    _order = "name"
    _rec_name = "label"

    wizard_id = fields.Many2one("travaux.import.geo", required=True, ondelete="cascade")
    name = fields.Char(required=True)
    nb_valeurs = fields.Integer()
    exemples = fields.Char()
    label = fields.Char(compute="_compute_label")

    @api.depends("name", "exemples")
    def _compute_label(self):
        for rec in self:
            rec.label = "%s (%s)" % (rec.name, rec.exemples) if rec.exemples else rec.name


class TravauxImportGeoClasse(models.TransientModel):
    _name = "travaux.import.geo.classe"
    _description = "Classe d'entités à importer"
    _order = "nombre desc"

    wizard_id = fields.Many2one("travaux.import.geo", required=True, ondelete="cascade")
    couche = fields.Char("Couche", readonly=True)
    valeur = fields.Char("Valeur", readonly=True)
    valeur_brute = fields.Char(readonly=True)
    geom = fields.Selection([("point", "Point"), ("ligne", "Ligne"), ("surface", "Surface")], "Géométrie", readonly=True)
    nombre = fields.Integer("Nb", readonly=True)
    importer = fields.Boolean("Importer")
    cible = fields.Selection([("objet", "Objet (# objet)"), ("lieu", "# lieu (zone)")], "Devient", default="objet",
                             required=True)
    type_id = fields.Many2one("travaux.lieu", "# objet existant", domain="[('kind', '=', 'objet')]")
    nouveau = fields.Char("ou nouveau # objet")

    @api.onchange("type_id")
    def _onchange_type(self):
        if self.type_id:
            self.nouveau = False
