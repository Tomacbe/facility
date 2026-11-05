"""Tournées de zones : une équipe entretient une liste de zones en boucle, à son rythme.

Sur le terrain, un seul appui « Fait » par zone (photo facultative). L'application retient le dernier
passage de chaque zone et l'avancement du tour ; quand toutes les zones sont faites, un nouveau tour commence.
"""
import base64
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

MOIS = [(str(i), n) for i, n in enumerate(
    ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"], 1)]


class TravauxCircuit(models.Model):
    _name = "travaux.circuit"
    _description = "Tournée de zones (liste de zones à entretenir en boucle)"
    _order = "equipe_id, sequence, name"

    name = fields.Char("Tournée", required=True, help="Ex. Tonte des espaces verts, Taille d'hiver, Désherbage des cimetières.")
    sequence = fields.Integer(default=10)
    equipe_id = fields.Many2one("travaux.equipe", "Équipe", required=True, domain=[("is_pool", "=", False)])
    mois_debut = fields.Selection(MOIS, "Saison : de", help="Laissez vide pour une tournée valable toute l'année.")
    mois_fin = fields.Selection(MOIS, "à")
    en_saison = fields.Boolean("En saison", compute="_compute_saison")
    saison_label = fields.Char("Saison", compute="_compute_saison")
    mode = fields.Selection(
        [("une", "Une par une : un bon de travail par zone (espaces verts)"),
         ("lot", "En lot : un seul bon, avec le pourcentage réalisé (poubelles)")],
        "Validation", default="une", required=True,
        help="Une par une : l'équipe démarre puis coche chaque zone. En lot : l'équipe démarre la tournée, "
             "puis indique 50, 80 ou 100 % en terminant.")
    delai_alerte = fields.Integer("Signaler après (jours)", default=21,
                                  help="Une zone sans passage depuis plus longtemps est affichée en rouge.")
    zone_ids = fields.One2many("travaux.circuit.zone", "circuit_id", "Zones", copy=True)
    tour_no = fields.Integer("Tour en cours", default=1, readonly=True, copy=False)
    tour_debut = fields.Date("Tour commencé le", readonly=True, copy=False)
    nb_zones = fields.Integer("Nombre de zones", compute="_compute_avancement")
    nb_faites = fields.Integer("Faites dans ce tour", compute="_compute_avancement")
    avancement = fields.Char("Avancement", compute="_compute_avancement")
    active = fields.Boolean(default=True)

    @api.depends("mois_debut", "mois_fin")
    def _compute_saison(self):
        month = fields.Date.context_today(self).month
        names = dict(MOIS)
        for rec in self:
            if not rec.mois_debut or not rec.mois_fin:
                rec.en_saison, rec.saison_label = True, _("Toute l'année")
                continue
            a, b = int(rec.mois_debut), int(rec.mois_fin)
            rec.en_saison = a <= month <= b if a <= b else (month >= a or month <= b)
            rec.saison_label = _("De %(a)s à %(b)s", a=names[rec.mois_debut].lower(), b=names[rec.mois_fin].lower())

    @api.depends("zone_ids", "zone_ids.tour_fait", "tour_no")
    def _compute_avancement(self):
        for rec in self:
            zones = rec.zone_ids.filtered("active")
            rec.nb_zones = len(zones)
            rec.nb_faites = len(zones.filtered(lambda z: z.tour_fait == rec.tour_no))
            rec.avancement = "%s / %s" % (rec.nb_faites, rec.nb_zones)

    def action_passages(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Passages – %s", self.name), "res_model": "travaux.passage",
                "view_mode": "list", "domain": [("circuit_id", "=", self.id)]}

    def action_nouveau_tour(self):
        for rec in self:
            rec.write({"tour_no": rec.tour_no + 1, "tour_debut": fields.Date.context_today(rec)})
        return True

    # ------------------------------------------------------------ écrans
    def _tv_dict(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        zones = []
        cours = self.env["travaux.demande"].sudo().search([("circuit_id", "=", self.id), ("state", "=", "cours")])
        lot = cours.filtered(lambda b: not b.zone_id)[:1]
        dernier_lot = self.env["travaux.demande"].sudo().search(
            [("circuit_id", "=", self.id), ("zone_id", "=", False), ("avancement", ">", 0)], order="date_fin desc", limit=1)
        for z in self.zone_ids.filtered("active").sorted("sequence"):
            days = (today - z.dernier_passage).days if z.dernier_passage else None
            last = z._last()
            stamp = int(last.write_date.timestamp()) if last else 0
            bt = cours.filtered(lambda b: b.zone_id == z)[:1]
            zones.append({
                "cours": bool(bt), "bt_id": bt.id or False,
                "debut": fields.Datetime.to_string(bt.date_debut) if bt.date_debut else False,
                "vignette": ("/web/image/travaux.circuit.zone/%s/photo/160x160?unique=%s" % (z.id, int(z.write_date.timestamp()))) if z.photo else False,
                "id": z.id, "name": z.name, "note": z.note or "", "lieu": ("#" + z.lieu_id.name) if z.lieu_id else "",
                "last": z.dernier_passage.isoformat() if z.dernier_passage else False, "days": days,
                "fait": z.tour_fait == self.tour_no, "today": z.dernier_passage == today,
                "retard": days is None or days > self.delai_alerte,
                "photo": ("/web/image/travaux.passage/%s/photo?unique=%s" % (last.id, stamp)) if last.photo else False,
                "thumb": ("/web/image/travaux.passage/%s/photo/160x160?unique=%s" % (last.id, stamp)) if last.photo else False,
                "par": last.user_id.name or "",
            })
        done = len([z for z in zones if z["fait"]])
        return {
            "id": self.id, "name": self.name, "team_id": self.equipe_id.id, "team": self.equipe_id.name,
            "color": self.equipe_id.color_hex, "saison": self.saison_label, "en_saison": self.en_saison,
            "tour": self.tour_no, "tour_debut": self.tour_debut.isoformat() if self.tour_debut else False,
            "done": done, "total": len(zones), "pct": round(100 * done / len(zones)) if zones else 0,
            "retard": len([z for z in zones if z["retard"] and not z["fait"]]),
            "delai": self.delai_alerte, "zones": zones, "mode": self.mode, "lot_id": lot.id or False,
            "lot_dernier": (_("%(p)s %% le %(d)s", p=dernier_lot.avancement, d=fields.Date.context_today(self, dernier_lot.date_fin).strftime("%d/%m"))
                            if dernier_lot else ""),
        }

    @api.model
    def _tv_visible(self, saison=True):
        """Tournées visibles : celles de mon équipe (agent), de mes équipes (superviseur), toutes (secrétariat)."""
        Demande = self.env["travaux.demande"]
        scope = Demande._tv_scope()
        recs = self.sudo().search([])
        if scope == "equipe":
            team = self.env.user.employee_ids[:1].sudo().equipe_jour_id
            home = self.env.user.employee_ids[:1].sudo().equipe_id
            recs = recs.filtered(lambda c: c.equipe_id in (team | home))
        elif scope == "sup":
            recs = recs.filtered(lambda c: self.env.user in c.equipe_id.responsable_ids)
        if saison:
            recs = recs.filtered("en_saison")
        return recs

    @api.model
    def tv_lot_demarrer(self, circuit_id):
        """Tournée en lot : un seul bon de travail pour toute la liste ; l'équipe indiquera le pourcentage réalisé."""
        circuit = self.sudo().browse(circuit_id).exists()
        if not circuit or circuit not in self._tv_visible(saison=False):
            raise AccessError(_("Cette tournée n'est pas celle de votre équipe."))
        Demande = self.env["travaux.demande"].sudo()
        bt = Demande.search([("circuit_id", "=", circuit.id), ("zone_id", "=", False), ("state", "=", "cours")], limit=1)
        if not bt:
            bt = Demande.with_context(mail_create_nolog=True, travaux_no_photo_post=True).create({
                "titre": circuit.name, "type_travail": "tournee", "equipe_id": circuit.equipe_id.id,
                "date_prevue": fields.Date.context_today(self), "state": "planif", "source": "planning",
                "circuit_id": circuit.id,
                "liste": "\n".join(circuit.zone_ids.filtered("active").sorted("sequence").mapped("name"))})
            bt.with_user(self.env.user).action_demarrer()
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "zones", "ids": [circuit.id]})
        return bt.id

    @api.model
    def tv_zones(self, toutes=False):
        circuits = self._tv_visible(saison=not toutes)
        circuits = circuits.sorted(lambda c: (not c.en_saison, c.equipe_id.name or "", c.sequence, c.name))
        return {"circuits": [c._tv_dict() for c in circuits],
                "is_sup": self.env.user.has_group("service_travaux.group_superviseur"),
                "today": fields.Date.context_today(self).isoformat()}


class TravauxCircuitZone(models.Model):
    _name = "travaux.circuit.zone"
    _description = "Zone d'une tournée"
    _order = "circuit_id, sequence, id"

    circuit_id = fields.Many2one("travaux.circuit", "Tournée", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char("Zone", required=True, help="Ex. Parc du Centre, Rond-point de la Gare, Cimetière : allées.")
    lieu_id = fields.Many2one("travaux.lieu", "# Lieu", domain=[("kind", "=", "lieu")])
    note = fields.Char("À savoir", help="Ex. clé au dépôt, attention aux massifs.")
    photo = fields.Image("Photo de la zone", max_width=1280, max_height=1280,
                         help="Photo de référence posée par le superviseur : elle sert de vignette à la zone sur le terrain.")
    duree = fields.Float("Temps habituel (h)", help="Facultatif : compté dans les heures de l'équipe à chaque passage, "
                                                    "sans que l'équipe doive l'encoder.")
    passage_ids = fields.One2many("travaux.passage", "zone_id", "Passages")
    dernier_passage = fields.Date("Dernier passage", readonly=True, copy=False)
    tour_fait = fields.Integer("Fait au tour", readonly=True, copy=False)
    active = fields.Boolean(default=True)

    def _last(self):
        """Dernier passage (recherche explicite : l'ordre du cache n'est pas fiable juste après une création)."""
        return self.env["travaux.passage"].sudo().search([("zone_id", "=", self.id)], order="date desc, id desc", limit=1)

    def _check_team(self):
        """L'agent doit faire partie de l'équipe (ou être responsable de l'équipe, ou au secrétariat)."""
        self.ensure_one()
        if self.circuit_id not in self.env["travaux.circuit"]._tv_visible(saison=False):
            raise AccessError(_("Cette zone n'est pas dans une tournée de votre équipe."))

    @api.model
    def tv_fait(self, zone_id, photo=False):
        """Un appui « Fait » : enregistre le passage, fait avancer le tour, compte le temps habituel."""
        zone = self.sudo().browse(zone_id).exists()
        if not zone:
            raise UserError(_("Cette zone n'existe plus."))
        zone.with_user(self.env.user)._check_team()
        circuit = zone.circuit_id
        today = fields.Date.context_today(self)
        if zone.tour_fait == circuit.tour_no and zone.dernier_passage == today:
            if photo:
                zone._last().photo = photo
            return self.env["travaux.circuit"].tv_zones()
        if not circuit.tour_debut:
            circuit.tour_debut = today
        self.env["travaux.passage"].sudo().create({
            "zone_id": zone.id, "date": today, "user_id": self.env.uid, "tour_no": circuit.tour_no,
            "photo": photo or False, "heures": zone.duree})
        zone.write({"dernier_passage": today, "tour_fait": circuit.tour_no})
        zone._impute(today)
        # toutes les zones faites : le tour est bouclé, on repart
        zones = circuit.zone_ids.filtered("active")
        if zones and all(z.tour_fait == circuit.tour_no for z in zones):
            circuit.write({"tour_no": circuit.tour_no + 1, "tour_debut": False})
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "zones", "ids": [circuit.id]})
        return self.env["travaux.circuit"].tv_zones()

    # ------------------------------------------------------------ une par une : Démarrer puis Fait
    def _bt_cours(self):
        self.ensure_one()
        return self.env["travaux.demande"].sudo().search([("zone_id", "=", self.id), ("state", "=", "cours")], limit=1)

    @api.model
    def tv_zone_demarrer(self, zone_id):
        """« Démarrer » sur une zone : ouvre son bon de travail, le temps est mesuré."""
        zone = self.sudo().browse(zone_id).exists()
        if not zone:
            raise UserError(_("Cette zone n'existe plus."))
        zone.with_user(self.env.user)._check_team()
        if not zone._bt_cours():
            circuit = zone.circuit_id
            bt = self.env["travaux.demande"].sudo().with_context(mail_create_nolog=True, travaux_no_photo_post=True).create({
                "titre": "%s · %s" % (circuit.name, zone.name), "type_travail": "tournee", "equipe_id": circuit.equipe_id.id,
                "date_prevue": fields.Date.context_today(self), "state": "planif", "source": "planning",
                "circuit_id": circuit.id, "zone_id": zone.id, "lieu_id": zone.lieu_id.id or False,
                "consigne": zone.note or False})
            bt.with_user(self.env.user).action_demarrer()
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "zones", "ids": [zone.circuit_id.id]})
        return self.env["travaux.circuit"].tv_zones()

    @api.model
    def tv_zone_stop(self, zone_id):
        """Démarré par erreur : le bon ouvert est supprimé, rien n'est compté."""
        zone = self.sudo().browse(zone_id).exists()
        zone.with_user(self.env.user)._check_team()
        bt = zone._bt_cours()
        bt.pointage_ids.unlink()
        bt.unlink()
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "zones", "ids": [zone.circuit_id.id]})
        return self.env["travaux.circuit"].tv_zones()

    @api.model
    def tv_zone_presents(self, zone_id):
        zone = self.sudo().browse(zone_id).exists()
        zone.with_user(self.env.user)._check_team()
        bt = zone._bt_cours()
        if not bt:
            raise UserError(_("Appuyez d'abord sur « Démarrer »."))
        res = self.env["travaux.demande"].sudo().tv_presents(bt.id)
        res["debut"] = fields.Datetime.to_string(bt.date_debut)
        return res

    @api.model
    def tv_zone_fait(self, zone_id, employee_ids, duree=False):
        """« Fait » : confirme les présents, clôture le bon de la zone (durée × personnes) et note le passage."""
        zone = self.sudo().browse(zone_id).exists()
        zone.with_user(self.env.user)._check_team()
        bt = zone._bt_cours()
        if not bt:
            raise UserError(_("Appuyez d'abord sur « Démarrer »."))
        emps = self.env["hr.employee"].sudo().browse([int(i) for i in (employee_ids or [])]).exists()
        if not emps:
            raise UserError(_("Cochez au moins une personne présente."))
        circuit = zone.circuit_id
        today = fields.Date.context_today(self)
        duree = round(float(duree), 2) if duree not in (False, None, "") else bt._clock_now()
        bt._check_heures_jour(emps, duree)
        bt.with_context(tracking_disable=True).write(
            {"nb_presents": len(emps), "presents": ", ".join(emps.sorted("name").mapped("name")),
             "present_ids": [(6, 0, emps.ids)], "date_travail": today})
        bt.with_user(self.env.user).sudo().with_context(travaux_duree=duree).action_terminer()
        bt.write({"state": "close", "date_cloture": fields.Datetime.now(), "cloture_par_id": self.env.uid})
        if not circuit.tour_debut:
            circuit.tour_debut = today
        self.env["travaux.passage"].sudo().create({
            "zone_id": zone.id, "date": today, "user_id": self.env.uid, "tour_no": circuit.tour_no,
            "heures": bt.duree_mesuree, "demande_id": bt.id})
        zone.write({"dernier_passage": today, "tour_fait": circuit.tour_no})
        zones = circuit.zone_ids.filtered("active")
        if zones and all(z.tour_fait == circuit.tour_no for z in zones):
            circuit.write({"tour_no": circuit.tour_no + 1, "tour_debut": False})
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "zones", "ids": [circuit.id]})
        return self.env["travaux.circuit"].tv_zones()

    @api.model
    def tv_annuler(self, zone_id):
        """Annule le passage d'aujourd'hui (appui par erreur)."""
        zone = self.sudo().browse(zone_id).exists()
        zone.with_user(self.env.user)._check_team()
        today = fields.Date.context_today(self)
        circuit = zone.circuit_id
        last = zone._last()
        if not last or last.date != today:
            raise UserError(_("Seul un passage d'aujourd'hui peut être annulé."))
        if last.tour_no != circuit.tour_no:  # ce passage avait bouclé le tour : on y revient
            circuit.write({"tour_no": last.tour_no})
            # les zones déjà refaites dans le tour qu'on abandonne comptent pour le tour repris
            ahead = circuit.zone_ids.filtered(lambda z: z.tour_fait > last.tour_no)
            self.env["travaux.passage"].sudo().search([("zone_id", "in", ahead.ids), ("tour_no", ">", last.tour_no)]).write({"tour_no": last.tour_no})
            ahead.write({"tour_fait": last.tour_no})
        bt = last.demande_id   # passage « une par une » : son bon de travail part avec lui
        last.unlink()
        if bt:
            bt.pointage_ids.unlink()
            bt.unlink()
        prev = zone._last()
        zone.write({"dernier_passage": prev.date if prev else False, "tour_fait": prev.tour_no if prev else 0})
        zone._impute(today)
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "zones", "ids": [circuit.id]})
        return self.env["travaux.circuit"].tv_zones()

    def _impute(self, day):
        """Temps habituel des zones faites ce jour-là, imputé à l'équipe sur une tâche du jour."""
        self.ensure_one()
        circuit = self.circuit_id
        Demande = self.env["travaux.demande"].sudo().with_context(
            travaux_no_live=True, tracking_disable=True, mail_create_nolog=True, travaux_no_photo_post=True)
        passages = self.env["travaux.passage"].sudo().search(
            [("circuit_id", "=", circuit.id), ("date", "=", day), ("demande_id", "=", False)])
        hours = sum(passages.mapped("heures"))
        task = Demande.search([("circuit_id", "=", circuit.id), ("date_prevue", "=", day), ("zone_id", "=", False),
                               ("avancement", "=", 0), ("state", "=", "close")], limit=1)
        if not hours:
            if task:
                task.pointage_ids.unlink()
                task.unlink()
            return
        if not task:
            task = Demande.create({
                "titre": circuit.name, "type_travail": "tournee", "equipe_id": circuit.equipe_id.id,
                "date_prevue": day, "state": "close", "source": "planning", "circuit_id": circuit.id,
                "date_cloture": fields.Datetime.now()})
        task.write({"liste": "\n".join(passages.sorted("id").mapped("zone_id.name")), "duree_prevue": hours})
        line = task.pointage_ids[:1]
        if line:
            line.heures = hours
        else:
            self.env["travaux.pointage"].sudo().create({
                "demande_id": task.id, "equipe_id": circuit.equipe_id.id, "date": day, "heures": hours,
                "origine": "mesure", "note": _("zones faites")})


class TravauxPassage(models.Model):
    _name = "travaux.passage"
    _description = "Passage dans une zone"
    _order = "date desc, id desc"

    zone_id = fields.Many2one("travaux.circuit.zone", "Zone", required=True, ondelete="cascade", index=True)
    circuit_id = fields.Many2one(related="zone_id.circuit_id", store=True, index=True)
    equipe_id = fields.Many2one(related="zone_id.circuit_id.equipe_id", store=True, string="Équipe")
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    user_id = fields.Many2one("res.users", "Encodé par", default=lambda self: self.env.user)
    tour_no = fields.Integer("Tour")
    heures = fields.Float("Heures comptées")
    photo = fields.Image("Photo", max_width=1280, max_height=1280)
    demande_id = fields.Many2one("travaux.demande", "Bon de travail", ondelete="set null", index=True)


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    zone_id = fields.Many2one("travaux.circuit.zone", "Zone", index=True, ondelete="set null", copy=False)

    circuit_id = fields.Many2one("travaux.circuit", "Tournée de zones", index=True, ondelete="set null", copy=False)
