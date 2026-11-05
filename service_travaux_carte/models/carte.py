import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.service_travaux.models.lieu import tag_format

from . import geo


class TravauxCarte(models.AbstractModel):
    """Données et outils de l'écran « Carte » de FACILITY (bureau) et de la carte de l'app terrain."""

    _name = "travaux.carte"
    _description = "Carte FACILITY"

    def _is_sup(self):
        return self.env.user.has_group("service_travaux.group_superviseur")

    def _need_sup(self, msg=None):
        if not self._is_sup():
            raise AccessError(msg or _("Réservé aux superviseurs."))

    @api.model
    def _zone_etat(self, zone):
        """Fait dans le tour en cours de la tournée, sinon à faire."""
        return "fait" if zone.tour_fait and zone.tour_fait >= (zone.circuit_id.tour_no or 1) else "todo"

    def _objet(self, objet_id):
        objet = self.env["travaux.objet"].browse(int(objet_id)).exists()
        if not objet:
            raise UserError(_("Cet objet n'existe plus."))
        return objet

    def _lieu(self, lieu_id):
        lieu = self.env["travaux.lieu"].browse(int(lieu_id)).exists()
        if not lieu or lieu.kind != "lieu":
            raise UserError(_("Ce # lieu n'existe plus."))
        return lieu

    # ------------------------------------------------------------ lecture
    @api.model
    def carte_data(self):
        Objet = self.env["travaux.objet"]
        Lieu = self.env["travaux.lieu"]
        objets = Objet.search([])
        counts = {}
        for o in objets:
            counts[o.type_id.id] = counts.get(o.type_id.id, 0) + 1
        types = []
        for t in Lieu.search([("kind", "=", "objet"), ("state", "=", "valide")]) | objets.type_id:
            d = t._carte_type(counts.get(t.id, 0))
            # « sur la carte » : a des objets ou a été configuré pour la carte (sinon simple # de demande)
            d["carte"] = bool(counts.get(t.id) or t.geom_type != "point" or t.prefixe or t.couleur)
            types.append(d)
        zones, points = [], []
        Zone = self.env["travaux.circuit.zone"].sudo()
        lieux = Lieu.search([("kind", "=", "lieu"), ("state", "=", "valide")])
        for lieu in lieux.filtered("has_contour"):
            zs = Zone.search([("lieu_id", "=", lieu.id), ("circuit_id.active", "=", True)])
            etat = "fait" if zs and all(self._zone_etat(z) == "fait" for z in zs) else ("todo" if zs else "lieu")
            zones.append({"id": lieu.id, "name": lieu.name, "geojson": json.loads(lieu.geojson), "etat": etat,
                          "circuits": ", ".join(zs.circuit_id.mapped("name"))})
        sans = []
        for lieu in lieux.filtered(lambda x: not x.has_contour):
            if lieu.latitude and lieu.longitude:
                points.append({"id": lieu.id, "name": lieu.name, "lat": lieu.latitude, "lng": lieu.longitude})
            else:
                sans.append({"id": lieu.id, "name": lieu.name})
        centre = False
        if objets:
            centre = [sum(objets.mapped("latitude")) / len(objets), sum(objets.mapped("longitude")) / len(objets)]
        circuits = self.env["travaux.circuit"].sudo().search([("mode", "=", "une")])
        return {
            "objets": [o._carte_point() for o in objets],
            "types": sorted(types, key=lambda t: ({"point": 0, "ligne": 1, "surface": 2}[t["g"]], -t["n"], t["name"])),
            "zones": zones, "lieux_points": points, "sans_geo": sorted(sans, key=lambda x: x["name"].lower())[:300],
            "nb_sans_geo": len(sans),
            "circuits": [{"id": c.id, "name": c.name} for c in circuits],
            "centre": centre, "is_sup": self._is_sup(),
            "can_add": self.env.user.has_group("service_travaux.group_agent"),
        }

    @api.model
    def carte_objet(self, objet_id):
        objet = self.env["travaux.objet"].browse(int(objet_id)).exists()
        return objet._carte_fiche() if objet else False

    @api.model
    def carte_point(self, objet_id):
        objet = self.env["travaux.objet"].browse(int(objet_id)).exists()
        return objet._carte_point() if objet else False

    @api.model
    def carte_zone(self, lieu_id):
        lieu = self.env["travaux.lieu"].browse(int(lieu_id)).exists()
        if not lieu:
            return False
        zones = self.env["travaux.circuit.zone"].sudo().search([("lieu_id", "=", lieu.id)])
        passages = self.env["travaux.passage"].sudo().search([("zone_id", "in", zones.ids)], order="date desc, id desc", limit=5)
        objets = self.env["travaux.objet"].search([("lieu_id", "=", lieu.id)])
        par_type = {}
        for o in objets:
            par_type[o.type_id.name] = par_type.get(o.type_id.name, 0) + 1
        return {
            "kind": "zone", "id": lieu.id, "name": lieu.name, "surface": lieu.surface, "adresse": lieu.adresse or "",
            "objets": len(objets), "objets_types": ", ".join("#%s (%s)" % kv for kv in sorted(par_type.items())),
            "zones": [{"name": z.name, "circuit": z.circuit_id.name, "equipe": z.circuit_id.equipe_id.name,
                       "etat": self._zone_etat(z), "dernier": z.dernier_passage and fields.Date.to_string(z.dernier_passage)}
                      for z in zones],
            "passages": [{"date": fields.Date.to_string(p.date), "zone": p.zone_id.name,
                          "h": p.heures, "inutile": bool(getattr(p, "pas_necessaire", False))} for p in passages],
            "geojson": json.loads(lieu.geojson) if lieu.has_contour else False,
            "point": [lieu.latitude, lieu.longitude] if (not lieu.has_contour and lieu.latitude) else False,
            "sans_geo": not lieu.has_contour and not lieu.latitude,
        }

    @api.model
    def terrain_types(self):
        """Types que l'agent peut proposer depuis la carte de sa tournée."""
        types = self.env["travaux.lieu"].search([("kind", "=", "objet"), ("state", "=", "valide"), ("proposable", "=", True)])
        used = set(self.env["travaux.objet"].search([]).type_id.ids)
        return sorted([t._carte_type() for t in types if t.id in used or t.geom_type != "point" or t.prefixe],
                      key=lambda t: t["name"].lower())

    # ------------------------------------------------------------ objets
    @api.model
    def carte_ajouter(self, type_id, lat=None, lng=None, geojson=None):
        """Nouvel objet : figé si superviseur, proposé sinon. Point (lat, lng) ou forme (geojson)."""
        if not self.env.user.has_group("service_travaux.group_agent") and not self._is_sup():
            raise AccessError(_("Vous ne pouvez pas ajouter d'objet."))
        t = self.env["travaux.lieu"].browse(int(type_id)).exists()
        if not t or t.kind != "objet":
            raise UserError(_("Type d'objet inconnu."))
        if not self._is_sup() and not t.proposable:
            raise AccessError(_("#%s ne se propose pas depuis le terrain.", t.name))
        vals = {"type_id": t.id, "source": "bureau" if self._is_sup() else "terrain",
                "state": "valide" if self._is_sup() else "propose", "propose_par_id": self.env.uid}
        if geojson:
            vals["geojson"] = json.dumps(geojson) if isinstance(geojson, dict) else geojson
        else:
            vals.update(latitude=lat, longitude=lng)
        objet = self.env["travaux.objet"].sudo().create(vals)
        return objet.id

    @api.model
    def carte_deplacer(self, objet_id, lat, lng):
        objet = self._objet(objet_id)
        if not objet._peut_modifier():
            raise AccessError(_("Seul un superviseur peut déplacer un objet figé."))
        objet.sudo()._deplacer(lat, lng)
        return objet.id

    @api.model
    def carte_forme(self, objet_id, geojson):
        objet = self._objet(objet_id)
        if not objet._peut_modifier():
            raise AccessError(_("Seul un superviseur peut modifier un objet figé."))
        objet.sudo()._set_forme(geojson)
        return objet.id

    @api.model
    def carte_figer(self, objet_id):
        self.env["travaux.objet"].browse(int(objet_id)).action_figer()
        return True

    @api.model
    def carte_supprimer(self, objet_id):
        """Supprime l'objet ; s'il a des demandes, il est archivé (retiré) pour garder l'historique."""
        objet = self._objet(objet_id)
        if not objet._peut_modifier():
            raise AccessError(_("Seul un superviseur peut supprimer un objet figé."))
        objet = objet.sudo()
        if objet.with_context(active_test=False).demande_ids:
            objet.active = False
            return "archive"
        objet.unlink()
        return "supprime"

    @api.model
    def carte_refuser(self, objet_id):
        self._need_sup()
        objet = self._objet(objet_id)
        if objet.state != "propose":
            raise UserError(_("Cet objet est déjà figé."))
        return self.carte_supprimer(objet.id)

    # ------------------------------------------------------------ # lieux
    @api.model
    def carte_lieu_creer(self, name, geojson=None, lat=None, lng=None, circuit_id=None):
        self._need_sup(_("Seul un superviseur peut créer un # lieu sur la carte."))
        name = (name or "").strip()
        if not name:
            raise UserError(_("Donnez un nom au # lieu."))
        Lieu = self.env["travaux.lieu"].sudo()
        if Lieu._by_name(tag_format(name)) or Lieu._by_name(name):
            raise UserError(_("#%s existe déjà : choisissez-le dans la liste des # lieux sans géométrie.", name))
        lieu = Lieu.create({"name": name, "kind": "lieu", "state": "valide"})
        self._lieu_geo(lieu, geojson, lat, lng)
        if circuit_id:
            circuit = self.env["travaux.circuit"].sudo().browse(int(circuit_id)).exists()
            if circuit:
                seq = max(circuit.zone_ids.mapped("sequence") or [0]) + 10
                self.env["travaux.circuit.zone"].sudo().create({"circuit_id": circuit.id, "name": lieu.name,
                                                                "lieu_id": lieu.id, "sequence": seq})
        return lieu.id

    def _lieu_geo(self, lieu, geojson, lat, lng):
        if geojson:
            g = geo.loads(json.dumps(geojson) if isinstance(geojson, dict) else geojson)
            if not g:
                raise UserError(_("Contour invalide."))
            lieu.write({"geojson": json.dumps(g)})
            lieu._rattacher_objets()
        elif lat and lng:
            lieu.write({"geojson": False, "latitude": lat, "longitude": lng})

    @api.model
    def carte_lieu_geometrie(self, lieu_id, geojson=None, lat=None, lng=None):
        """Donne (ou corrige) la géométrie d'un # lieu : un contour, ou un simple point."""
        self._need_sup(_("Seul un superviseur peut modifier un # lieu sur la carte."))
        lieu = self._lieu(lieu_id).sudo()
        if not geojson and not (lat and lng):
            raise UserError(_("Dessinez un contour ou placez un point."))
        self._lieu_geo(lieu, geojson, lat, lng)
        return lieu.id

    @api.model
    def carte_contour(self, lieu_id, geojson):  # compatibilité v1
        if geojson:
            return self.carte_lieu_geometrie(lieu_id, geojson=geojson)
        self._need_sup()
        self._lieu(lieu_id).sudo().write({"geojson": False})
        return True

    @api.model
    def carte_lieu_supprimer(self, lieu_id):
        """Efface le # lieu de la carte : archivé s'il sert (demandes, tournées), sinon supprimé.
        Ses objets restent, sans # lieu."""
        self._need_sup(_("Seul un superviseur peut supprimer un # lieu."))
        lieu = self._lieu(lieu_id).sudo()
        self.env["travaux.objet"].sudo().with_context(active_test=False).search(
            [("lieu_id", "=", lieu.id)]).write({"lieu_id": False})
        used = self.env["travaux.demande"].sudo().with_context(active_test=False).search_count([("lieu_id", "=", lieu.id)]) \
            or self.env["travaux.circuit.zone"].sudo().search_count([("lieu_id", "=", lieu.id)])
        if used:
            lieu.write({"active": False})
            return "archive"
        try:
            with self.env.cr.savepoint():
                lieu.unlink()
        except Exception:  # noqa: BLE001 - référencé ailleurs : on archive
            lieu.write({"active": False})
            return "archive"
        return "supprime"

    # ------------------------------------------------------------ # objets (types)
    @api.model
    def carte_type(self, vals, type_id=None):
        """Crée ou modifie un # objet : nom, géométrie, couleur, préfixe, proposable."""
        self._need_sup(_("Seul un superviseur peut modifier les # objets."))
        Lieu = self.env["travaux.lieu"].sudo()
        data = {k: vals[k] for k in ("name", "geom_type", "couleur", "prefixe", "proposable") if k in vals}
        if data.get("prefixe"):
            data["prefixe"] = data["prefixe"].strip().upper()[:4]
        if "name" in data:
            data["name"] = (data["name"] or "").strip().lstrip("#")
            if not data["name"]:
                raise UserError(_("Donnez un nom au #."))
        if type_id:
            t = Lieu.browse(int(type_id)).exists()
            if not t or t.kind != "objet":
                raise UserError(_("Ce # objet n'existe plus."))
            t.write(data)
        else:
            existing = Lieu._by_name(tag_format(data.get("name") or ""))
            if existing:
                raise UserError(_("#%s existe déjà.", existing.name))
            t = Lieu.create(dict(data, kind="objet", state="valide"))
            if not t.prefixe:
                t.prefixe = t._carte_prefixe()
        return t._carte_type(t.nb_objets)

    @api.model
    def carte_type_supprimer(self, type_id):
        self._need_sup(_("Seul un superviseur peut supprimer un # objet."))
        t = self.env["travaux.lieu"].sudo().browse(int(type_id)).exists()
        if not t or t.kind != "objet":
            return False
        if t.nb_objets:
            raise UserError(_("#%s a encore des objets sur la carte.", t.name))
        try:
            with self.env.cr.savepoint():
                t.unlink()
        except Exception:  # noqa: BLE001 - utilisé sur des demandes : on archive
            t.write({"active": False})
            return "archive"
        return "supprime"
