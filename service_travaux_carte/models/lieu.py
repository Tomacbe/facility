from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import geo

COULEURS = {
    "avaloir": "#1F5FA8", "chambredevisite": "#6A5CC4", "poteau": "#6B7780", "poubelle": "#C2410C",
    "lampadaire": "#B7791F", "banc": "#0F766E", "panneau": "#9D174D",
    "pelouse": "#4E9A3A", "massif": "#B5527E", "haie": "#2F6B2A", "arbreremarquable": "#7A5C1E",
}
GEOMETRIES = [("point", "Point"), ("ligne", "Ligne"), ("surface", "Surface")]


class TravauxLieu(models.Model):
    _inherit = "travaux.lieu"

    # # lieu : un contour (espace vert, cimetière, cour d'école…) en plus du point
    geojson = fields.Text("Contour", help="Surface du lieu (GeoJSON, WGS84). Dessiné dans FACILITY ou importé de QGIS.")
    has_contour = fields.Boolean("Contour dessiné", compute="_compute_contour", store=True)
    surface = fields.Integer("Surface (m²)", compute="_compute_contour", store=True)
    carte_ext_ref = fields.Char("Référence d'import", index=True, copy=False, readonly=True,
                                help="Source et identifiant d'origine : un nouvel import met ce # lieu à jour.")
    # # objet : géométrie, présentation sur la carte et préfixe des références (AV-001…)
    geom_type = fields.Selection(GEOMETRIES, "Géométrie", default="point", required=True,
                                 help="Ce qu'on dessine pour ce # objet : un point (#Poubelle), une ligne (#Haie, "
                                 "avec sa longueur) ou une surface (#Pelouse, avec ses m²).")
    proposable = fields.Boolean("Proposable depuis l'app terrain", default=True,
                                help="Les agents peuvent proposer des objets de ce type depuis la carte de leur tournée.")
    couleur = fields.Char("Couleur sur la carte", help="Couleur des objets de ce type (#Avaloir, #Poubelle…).")
    prefixe = fields.Char("Préfixe", size=4, help="Début de la référence des objets recensés : AV, PB, CV…")
    objet_recense_ids = fields.One2many("travaux.objet", "type_id", "Objets recensés")
    nb_objets = fields.Integer("Nombre d'objets", compute="_compute_nb_objets")

    @api.depends("geojson")
    def _compute_contour(self):
        for rec in self:
            g = geo.loads(rec.geojson)
            rec.has_contour = bool(g)
            rec.surface = round(geo.area_m2(g)) if g else 0

    def _compute_nb_objets(self):
        counts = dict(self.env["travaux.objet"].with_context(active_test=False)._read_group(
            [("type_id", "in", self.ids)], ["type_id"], ["__count"]))
        for rec in self:
            rec.nb_objets = counts.get(rec, 0)

    def write(self, vals):
        if "geom_type" in vals:
            for rec in self.filtered(lambda r: r.kind == "objet" and r.geom_type != vals["geom_type"]):
                if rec.with_context(active_test=False).objet_recense_ids:
                    raise UserError(_("La géométrie de #%s est verrouillée : des objets ont déjà leur forme. "
                                      "Créez un autre # pour une autre géométrie.", rec.name))
        res = super().write(vals)
        if vals.get("geojson") and "latitude" not in vals:
            for rec in self.filtered("has_contour"):
                c = geo.centroid(geo.loads(rec.geojson))
                if c:
                    super(TravauxLieu, rec).write({"longitude": c[0], "latitude": c[1]})
        return res

    def _carte_couleur(self):
        self.ensure_one()
        return self.couleur or COULEURS.get(self.cle or "", "#55606A")

    def _carte_prefixe(self):
        self.ensure_one()
        return (self.prefixe or (self.name or "OB")[:2]).upper()

    def _carte_type(self, n=0):
        self.ensure_one()
        return {"id": self.id, "name": self.name, "c": self._carte_couleur(), "g": self.geom_type or "point",
                "pre": self._carte_prefixe(), "proposable": self.proposable, "n": n}

    @api.model
    def _lieu_proche(self, lat, lng, rayon=150):
        """# lieu dont le contour contient le point, sinon le # lieu ponctuel le plus proche dans le rayon."""
        if not (lat and lng):
            return self.browse()
        lieux = self.search([("kind", "=", "lieu"), ("state", "=", "valide")])
        for lieu in lieux.filtered("has_contour"):
            if geo.contains(geo.loads(lieu.geojson), lng, lat):
                return lieu
        best, best_d = self.browse(), None
        for lieu in lieux.filtered(lambda x: not x.has_contour):
            d = geo.distance_m(lat, lng, lieu.latitude, lieu.longitude)
            if d is not None and d <= rayon and (best_d is None or d < best_d):
                best, best_d = lieu, d
        return best

    def _rattacher_objets(self):
        """Les objets sans # lieu dont le centre tombe dans ce contour lui sont rattachés."""
        for lieu in self.filtered("has_contour"):
            g = geo.loads(lieu.geojson)
            b = geo.bbox(g)
            for o in self.env["travaux.objet"].sudo().search([
                    ("lieu_id", "=", False), ("latitude", ">=", b[1]), ("latitude", "<=", b[3]),
                    ("longitude", ">=", b[0]), ("longitude", "<=", b[2])]):
                if geo.contains(g, o.longitude, o.latitude):
                    o.lieu_id = lieu

    def action_voir_carte(self):
        self.ensure_one()
        return {"type": "ir.actions.client", "tag": "travaux_carte", "name": "Carte",
                "params": {"lieu_id": self.id}}
