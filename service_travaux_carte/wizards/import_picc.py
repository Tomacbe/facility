import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models import geo
from ..models.lieu import COULEURS

_logger = logging.getLogger(__name__)

PICC_URL = "https://geoservices.wallonie.be/arcgis/rest/services/TOPOGRAPHIE/PICC_VDIFF/MapServer/4"

# nature PICC → (# objet proposé, préfixe, importé par défaut)
NATURES = {
    "Avaloir - Petit": ("Avaloir", "AV", True),
    "Avaloir - Grand": ("Avaloir", "AV", True),
    "Chambre de visite": ("ChambreDeVisite", "CV", True),
    "Point lumineux": ("Lampadaire", "LP", True),
    "Poteau": ("Poteau", "PO", False),
    "Taque - Petite": ("Taque", "TQ", False),
    "Taque - Grande": ("Taque", "TQ", False),
    "Borne": ("Borne", "BO", False),
    "Cabine - Petite": ("Cabine", "CB", False),
    "Monument - Petit": ("Monument", "MO", False),
}


class TravauxImportPicc(models.TransientModel):
    _name = "travaux.import.picc"
    _description = "Importer des objets depuis le PICC"

    state = fields.Selection([("zone", "Zone"), ("classes", "Classes"), ("done", "Résultat")], default="zone")
    mode = fields.Selection([("lieu", "Autour d'un # lieu"), ("rect", "Rectangle (coordonnées)")],
                            default="lieu", required=True)
    lieu_id = fields.Many2one("travaux.lieu", "# Lieu", domain=[("kind", "=", "lieu"), ("latitude", "!=", 0)])
    rayon = fields.Integer("Rayon (m)", default=250)
    min_lng = fields.Float("Longitude min", digits=(10, 6))
    min_lat = fields.Float("Latitude min", digits=(10, 6))
    max_lng = fields.Float("Longitude max", digits=(10, 6))
    max_lat = fields.Float("Latitude max", digits=(10, 6))
    features_json = fields.Text()
    line_ids = fields.One2many("travaux.import.picc.line", "wizard_id", "Classes du PICC")
    total = fields.Integer("Points dans la zone")
    result = fields.Text("Résultat", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        mc = self.env["travaux.lieu"]._by_name("MaisonCommunale")
        if mc and mc.latitude and "lieu_id" in fields_list and not res.get("lieu_id"):
            res["lieu_id"] = mc.id
        return res

    def _bbox(self):
        self.ensure_one()
        if self.mode == "lieu":
            if not self.lieu_id:
                raise UserError(_("Choisissez le # lieu autour duquel importer."))
            return geo.bbox_around(self.lieu_id.latitude, self.lieu_id.longitude, max(self.rayon, 20))
        box = (self.min_lng, self.min_lat, self.max_lng, self.max_lat)
        if not all(box) or box[0] >= box[2] or box[1] >= box[3]:
            raise UserError(_("Le rectangle est incomplet."))
        return box

    @api.model
    def _fetch(self, bbox):
        """Points « Équipement » du PICC dans le rectangle (GeoJSON, WGS84), page par page."""
        feats, offset = [], 0
        while True:
            params = {
                "geometry": "%s,%s,%s,%s" % bbox, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects", "outFields": "GEOREF_ID,NATUR_DESC",
                "outSR": 4326, "f": "geojson", "resultOffset": offset, "resultRecordCount": 1000,
                "orderByFields": "OBJECTID",
            }
            try:
                r = requests.get(PICC_URL + "/query", params=params, timeout=60)
                r.raise_for_status()
                data = r.json()
            except (requests.RequestException, ValueError) as err:
                raise UserError(_("Le PICC du SPW ne répond pas (%s). Vérifiez l'accès Internet du serveur.", err)) from err
            if data.get("error"):
                raise UserError(_("Le PICC a renvoyé une erreur : %s", data["error"].get("message")))
            batch = data.get("features") or []
            feats.extend(batch)
            more = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
            if not batch or not more:
                return feats
            offset += len(batch)

    @api.model
    def _points(self, feats):
        out = []
        for f in feats:
            p = f.get("properties") or {}
            c = (f.get("geometry") or {}).get("coordinates")
            if c and p.get("NATUR_DESC"):
                out.append({"id": str(p.get("GEOREF_ID") or "") or False, "nature": p["NATUR_DESC"],
                            "lng": c[0], "lat": c[1]})
        return out

    def _reopen(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": _("Importer depuis le PICC")}

    def action_compter(self):
        self.ensure_one()
        pts = self._points(self._fetch(self._bbox()))
        self._set_points(pts)
        return self._reopen()

    def _set_points(self, pts):
        Lieu = self.env["travaux.lieu"]
        counts = {}
        for p in pts:
            counts[p["nature"]] = counts.get(p["nature"], 0) + 1
        lines = []
        for nature, n in sorted(counts.items(), key=lambda x: -x[1]):
            tag, _pre, on = NATURES.get(nature, (nature.split(" - ")[0], "", False))
            lieu = Lieu._by_name(tag)
            lines.append((0, 0, {"nature": nature, "nombre": n, "importer": on,
                                 "type_id": lieu.id if lieu and lieu.kind == "objet" else False,
                                 "nouveau": "" if lieu else tag}))
        self.line_ids.unlink()
        self.write({"features_json": json.dumps(pts), "line_ids": lines, "total": len(pts), "state": "classes"})

    def _type_for(self, line):
        Lieu = self.env["travaux.lieu"].sudo()
        if line.type_id:
            return line.type_id
        name = line.nouveau or NATURES.get(line.nature, (line.nature,))[0]
        lieu = Lieu._by_name(name)
        if not lieu:
            tag, pre, _on = NATURES.get(line.nature, (name, "", False))
            lieu = Lieu.create({"name": name, "kind": "objet", "state": "valide",
                                "prefixe": pre or False, "couleur": COULEURS.get(name.lower(), False)})
        return lieu

    def action_importer(self):
        self.ensure_one()
        res = self._import_points(json.loads(self.features_json or "[]"))
        self.write({"state": "done", "result": res["text"]})
        return self._reopen()

    def _import_points(self, pts):
        Objet = self.env["travaux.objet"].sudo().with_context(active_test=False)
        Lieu = self.env["travaux.lieu"]
        types = {}
        for line in self.line_ids.filtered("importer"):
            t = self._type_for(line)
            if not t.prefixe:
                t.sudo().prefixe = NATURES.get(line.nature, ("", ""))[1] or False
            types[line.nature] = t
        created = updated = 0
        to_create = []
        for p in pts:
            t = types.get(p["nature"])
            if not t:
                continue
            existing = Objet.search([("picc_id", "=", p["id"])], limit=1) if p["id"] else Objet.browse()
            if not existing:
                existing = Objet._proche(p["lat"], p["lng"], t.id)
            if existing:
                vals = {"picc_nature": p["nature"]}
                if p["id"] and not existing.picc_id:
                    vals["picc_id"] = p["id"]
                existing.write(vals)
                updated += 1
                continue
            to_create.append({"type_id": t.id, "latitude": p["lat"], "longitude": p["lng"], "source": "picc",
                              "picc_id": p["id"], "picc_nature": p["nature"], "state": "valide",
                              "lieu_id": Lieu._lieu_proche(p["lat"], p["lng"]).id or False})
        if to_create:
            Objet.create(to_create)
            created = len(to_create)
        text = _("%(c)s objet(s) créé(s), %(u)s déjà présent(s) mis à jour.", c=created, u=updated)
        _logger.info("FACILITY carte : import PICC, %s", text)
        return {"created": created, "updated": updated, "text": text}

    def action_voir_carte(self):
        return {"type": "ir.actions.client", "tag": "travaux_carte", "name": _("Carte"),
                "params": {"lieu_id": self.lieu_id.id} if self.lieu_id else {}}


class TravauxImportPiccLine(models.TransientModel):
    _name = "travaux.import.picc.line"
    _description = "Classe PICC à importer"
    _order = "nombre desc"

    wizard_id = fields.Many2one("travaux.import.picc", required=True, ondelete="cascade")
    nature = fields.Char("Classe PICC", readonly=True)
    nombre = fields.Integer("Points", readonly=True)
    importer = fields.Boolean("Importer")
    type_id = fields.Many2one("travaux.lieu", "# objet existant", domain=[("kind", "=", "objet")])
    nouveau = fields.Char("ou nouveau # objet")
