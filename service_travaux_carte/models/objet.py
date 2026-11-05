import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import geo
from .lieu import GEOMETRIES

OUVERTS = ("recue", "precision", "planif", "cours", "valider")


class TravauxObjet(models.Model):
    """Un objet réel du terrain : « cet avaloir-là », « la poubelle devant l'école ».

    Le # objet (#Avaloir, #Poubelle) reste le type ; l'objet recensé lui donne une position,
    une référence et un historique de demandes.
    """

    _name = "travaux.objet"
    _description = "Objet recensé"
    _order = "type_id, numero"

    name = fields.Char("Référence", readonly=True, copy=False, index=True)
    numero = fields.Integer(readonly=True, copy=False)
    type_id = fields.Many2one("travaux.lieu", "Type (# objet)", required=True, index=True, ondelete="restrict",
                              domain=[("kind", "=", "objet")])
    lieu_id = fields.Many2one("travaux.lieu", "# Lieu", index=True, ondelete="set null",
                              domain=[("kind", "=", "lieu")])
    circuit_id = fields.Many2one("travaux.circuit", "Tournée", index=True, ondelete="set null",
                                 help="Ex. la tournée des poubelles qui passe par cet objet.")
    latitude = fields.Float("Latitude", digits=(10, 7), required=True,
                            help="Position du point, ou centre de la ligne / de la surface.")
    longitude = fields.Float("Longitude", digits=(10, 7), required=True)
    geom_type = fields.Selection(related="type_id.geom_type", store=True, string="Géométrie")
    geojson = fields.Text("Forme", help="Ligne ou surface (GeoJSON, WGS84). Vide pour un objet point.")
    mesure = fields.Float("Mesure", compute="_compute_mesure", store=True, digits=(12, 1),
                          help="Longueur en m d'une ligne, surface en m² d'une surface.")
    mesure_txt = fields.Char("Longueur / surface", compute="_compute_mesure", store=True)
    geom_modifie = fields.Boolean("Forme corrigée dans FACILITY", copy=False,
                                  help="Un nouvel import ne remplace pas une position ou une forme corrigée à la main.")
    ext_ref = fields.Char("Référence d'import", index=True, copy=False, readonly=True,
                          help="Source, couche et identifiant d'origine : un nouvel import met l'objet à jour.")
    rue = fields.Char("Rue")
    note = fields.Char("Remarque")
    source = fields.Selection([("picc", "PICC (SPW)"), ("spw", "Géoportail SPW"), ("wfs", "Service WFS"),
                               ("odwb", "ODWB"), ("fichier", "Fichier QGIS"), ("terrain", "Terrain"),
                               ("bureau", "Bureau")], default="bureau", required=True)
    picc_id = fields.Char("Identifiant PICC", index=True, copy=False, readonly=True,
                          help="GEOREF_ID du PICC : un nouvel import met l'objet à jour au lieu de le dupliquer.")
    picc_nature = fields.Char("Nature PICC", readonly=True)
    state = fields.Selection([("propose", "Proposé · à figer"), ("valide", "Figé")], "Statut",
                             default="valide", required=True)
    propose_par_id = fields.Many2one("res.users", "Proposé par", readonly=True)
    demande_ids = fields.One2many("travaux.demande", "objet_recense_id", "Demandes")
    nb_demandes = fields.Integer("Nombre de demandes", compute="_compute_nb")
    nb_ouvertes = fields.Integer("Demandes ouvertes", compute="_compute_nb")
    couleur = fields.Char(compute="_compute_couleur")
    active = fields.Boolean(default=True)

    _picc_uniq = models.Constraint("unique(picc_id)", "Cet objet du PICC est déjà importé.")
    _ext_uniq = models.Constraint("unique(ext_ref)", "Cet objet est déjà importé.")

    @api.depends("geojson", "geom_type")
    def _compute_mesure(self):
        for rec in self:
            g = geo.loads_any(rec.geojson) if rec.geom_type in ("ligne", "surface") else None
            m = geo.measure(g) if g else 0.0
            rec.mesure = m
            if not m:
                rec.mesure_txt = False
            elif rec.geom_type == "ligne":
                rec.mesure_txt = "%s m" % format(round(m), ",").replace(",", " ")
            else:
                rec.mesure_txt = "%s m²" % format(round(m), ",").replace(",", " ")

    @api.model
    def _geom_vals(self, geojson):
        """geojson (texte ou dict) → valeurs à écrire : la forme et son centre."""
        g = geo.loads_any(geojson)
        if not g:
            raise UserError(_("Forme invalide."))
        c = geo.centroid(g)
        if g["type"] == "Point":
            return {"geojson": False, "longitude": c[0], "latitude": c[1]}
        return {"geojson": json.dumps(g), "longitude": c[0], "latitude": c[1]}

    def _check_geom_type(self):
        for rec in self:
            g = geo.loads_any(rec.geojson)
            k = geo.kind(g) if g else "point"
            if rec.geom_type and k != rec.geom_type and not (k == "point" and not rec.geojson):
                raise UserError(_("#%(t)s attend une %(g)s.", t=rec.type_id.name,
                                  g=dict(GEOMETRIES).get(rec.geom_type, "").lower()))
            if rec.geom_type in ("ligne", "surface") and not g:
                raise UserError(_("#%s : dessinez la forme de l'objet.", rec.type_id.name))

    @api.depends("demande_ids.state")
    def _compute_nb(self):
        for rec in self:
            rec.nb_demandes = len(rec.demande_ids)
            rec.nb_ouvertes = len(rec.demande_ids.filtered(lambda d: d.state in OUVERTS))

    @api.depends("type_id.couleur", "type_id.cle")
    def _compute_couleur(self):
        for rec in self:
            rec.couleur = rec.type_id._carte_couleur() if rec.type_id else "#55606A"

    @api.depends("name", "type_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "#%s %s" % (rec.type_id.name or "", rec.name or "")

    # ------------------------------------------------------------ création
    @api.model_create_multi
    def create(self, vals_list):
        Lieu = self.env["travaux.lieu"]
        last = {}
        for vals in vals_list:
            if vals.get("geojson"):
                vals.update(self._geom_vals(vals["geojson"]))
            type_id = vals.get("type_id")
            if type_id not in last:
                last[type_id] = max(self.with_context(active_test=False).search(
                    [("type_id", "=", type_id)]).mapped("numero") or [0])
            last[type_id] += 1
            vals["numero"] = last[type_id]
            vals["name"] = "%s-%03d" % (Lieu.browse(type_id)._carte_prefixe(), last[type_id])
            if "lieu_id" not in vals and vals.get("latitude"):
                vals["lieu_id"] = Lieu._lieu_proche(vals["latitude"], vals["longitude"]).id or False
            if vals.get("state") == "propose" and not vals.get("propose_par_id"):
                vals["propose_par_id"] = self.env.uid
        recs = super().create(vals_list)
        recs._check_geom_type()
        return recs

    def write(self, vals):
        if vals.get("geojson"):
            vals = dict(vals, **self._geom_vals(vals["geojson"]))
        res = super().write(vals)
        if "geojson" in vals or "type_id" in vals:
            self._check_geom_type()
        return res

    # ------------------------------------------------------------ gestion
    def _peut_modifier(self):
        """Superviseur, ou l'agent qui a proposé l'objet tant qu'il n'est pas figé."""
        self.ensure_one()
        return self.env.user.has_group("service_travaux.group_superviseur") or (
            self.state == "propose" and self.propose_par_id == self.env.user)

    def _check_sup(self):
        if not self.env.user.has_group("service_travaux.group_superviseur"):
            raise AccessError(_("Seul un superviseur peut figer, déplacer ou retirer un objet."))

    def action_figer(self):
        self._check_sup()
        self.write({"state": "valide"})
        return True

    def action_voir_carte(self):
        self.ensure_one()
        return {"type": "ir.actions.client", "tag": "travaux_carte", "name": "Carte",
                "params": {"objet_id": self.id}}

    def action_voir_demandes(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Demandes – %s", self.display_name),
                "res_model": "travaux.demande", "view_mode": "list,form",
                "domain": [("objet_recense_id", "=", self.id)],
                "context": {"default_objet_recense_id": self.id}}

    def _carte_point(self):
        self.ensure_one()
        return {"id": self.id, "ref": self.name, "type": self.type_id.name, "type_id": self.type_id.id,
                "c": self.couleur, "lat": self.latitude, "lng": self.longitude, "g": self.geom_type or "point",
                "geo": json.loads(self.geojson) if self.geojson else False,
                "prop": self.state == "propose", "open": self.nb_ouvertes,
                "mine": self.state == "propose" and self.propose_par_id == self.env.user,
                "circuit_id": self.circuit_id.id or False}

    def _carte_fiche(self):
        self.ensure_one()
        states = dict(self.env["travaux.demande"]._fields["state"].selection)
        dem = self.demande_ids.sorted("create_date", reverse=True)[:6]
        return {
            **self._carte_point(), "kind": "objet",
            "lieu": ("#" + self.lieu_id.name) if self.lieu_id else "", "rue": self.rue or "",
            "circuit": self.circuit_id.name or "", "equipe": self.circuit_id.equipe_id.name or "",
            "source": dict(self._fields["source"].selection)[self.source],
            "src": self.source, "picc_nature": self.picc_nature or "", "note": self.note or "",
            "propose_par": self.propose_par_id.name or "", "mesure": self.mesure_txt or "",
            "nb_demandes": self.nb_demandes, "can_edit": self._peut_modifier(),
            "choix": self._choix_signalement(),
            "demandes": [{"id": d.id, "ref": d.name, "titre": d.titre, "state": d.state,
                          "st": states.get(d.state), "date": fields.Date.to_string(d.create_date.date())}
                         for d in dem],
        }

    def _deplacer(self, lat, lng):
        self.ensure_one()
        if self.state == "valide":
            self._check_sup()
        old = (self.latitude, self.longitude)
        vals = {"latitude": lat, "longitude": lng, "geom_modifie": True,
                "lieu_id": self.env["travaux.lieu"]._lieu_proche(lat, lng).id or self.lieu_id.id}
        if self.geojson:  # translation de la forme entière
            dlat, dlng = lat - self.latitude, lng - self.longitude
            g = geo.map_coords(geo.loads_any(self.geojson), lambda x, y: (x + dlng, y + dlat))
            vals["geojson"] = json.dumps(g)
        self.write(vals)
        return old

    def _set_forme(self, geojson):
        """Nouvelle forme dessinée sur la carte (point, ligne ou surface selon le #)."""
        self.ensure_one()
        if self.state == "valide":
            self._check_sup()
        vals = self._geom_vals(geojson)
        vals["geom_modifie"] = True
        vals["lieu_id"] = self.env["travaux.lieu"]._lieu_proche(vals["latitude"], vals["longitude"]).id or self.lieu_id.id
        self.write(vals)

    @api.model
    def _proche(self, lat, lng, type_id=False, rayon=1.5):
        """Objet déjà présent à cet endroit (même type) : sert à ne pas dupliquer à l'import."""
        dom = [("latitude", ">", lat - 0.0001), ("latitude", "<", lat + 0.0001),
               ("longitude", ">", lng - 0.00015), ("longitude", "<", lng + 0.00015)]
        if type_id:
            dom.append(("type_id", "=", type_id))
        for rec in self.with_context(active_test=False).search(dom):
            if geo.distance_m(lat, lng, rec.latitude, rec.longitude) <= rayon:
                return rec
        return self.browse()

    def unlink(self):
        if self.filtered("demande_ids"):
            raise UserError(_("Un objet qui a des demandes s'archive (Retirer), il ne se supprime pas."))
        return super().unlink()
