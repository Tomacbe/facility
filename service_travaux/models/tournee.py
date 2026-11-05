"""Tournées lancées par le superviseur.

Le superviseur lance une tournée (Suivi → Tournées, ou « Nouvelle fiche de travail » → Tournée) :
- validation « une par une » (espaces verts) : UN bon de travail pour toute la tournée. C'est la discussion de la
  tournée ; elle contient la liste de ses zones (un sous-niveau). L'équipe fait les zones dans l'ordre qu'elle veut,
  sur plusieurs jours : « Démarrer » puis « Fait » sur une zone ouvre puis clôture un sous-bon (présents, durée,
  heures comptées), « Pas nécessaire » clôture la zone à 0 h. Quand toutes les zones sont passées, la tournée est
  clôturée. Les sous-bons ne s'affichent ni dans la liste du terrain ni dans le kanban : seule la tournée apparaît.
- validation « en lot » (poubelles) : un seul bon ; l'équipe indique le pourcentage réalisé en terminant.
Les tournées qui n'ont plus été faites depuis longtemps (délai de la tournée) sont suggérées au superviseur.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


def _h(x):
    """1.5 → « 1,5 » (heures, à la belge)."""
    return ("%g" % round(x or 0.0, 2)).replace(".", ",")


class TravauxCircuit(models.Model):
    _inherit = "travaux.circuit"

    lancement_en_cours = fields.Boolean("Tournée lancée", readonly=True, copy=False)
    lancement_debut = fields.Date("Lancée le", readonly=True, copy=False)
    derniere_tournee = fields.Date("Dernière tournée terminée le", readonly=True, copy=False)

    def _jours_sans_tournee(self):
        """Jours depuis la dernière tournée (ou le dernier passage), None si jamais faite."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        dates = [d for d in [self.derniere_tournee] + self.zone_ids.mapped("dernier_passage") if d]
        return (today - max(dates)).days if dates else None

    def _a_relancer(self):
        self.ensure_one()
        if self.lancement_en_cours or not self.en_saison or not self.zone_ids.filtered("active"):
            return False
        jours = self._jours_sans_tournee()
        return jours is None or jours > (self.delai_alerte or 21)

    def _zones_actives(self):
        return self.zone_ids.filtered("active").sorted("sequence")

    def _bt_cours_tournee(self):
        """Le bon de la tournée lancée (pas un sous-bon de zone)."""
        self.ensure_one()
        return self.env["travaux.demande"].sudo().search([
            ("circuit_id", "=", self.id), ("state", "in", ("planif", "cours", "valider")),
            ("source", "=", "planning"), ("recurrence_id", "=", False),
            ("tournee_id", "=", False), ("zone_id", "=", False)], order="id desc", limit=1)

    def _creer_bt(self, date):
        self.ensure_one()
        zones = self._zones_actives()
        vals = {
            "titre": self.name, "type_travail": "tournee", "equipe_id": self.equipe_id.id, "date_prevue": date,
            "state": "planif", "source": "planning", "circuit_id": self.id,
            "date_attribution": fields.Datetime.now(), "attribue_par_id": self.env.uid,
            "duree_prevue": sum(zones.mapped("duree")),
        }
        if self.mode == "lot":
            vals["liste"] = "\n".join(zones.mapped("name"))
        bt = self.env["travaux.demande"].sudo().with_context(mail_create_nolog=True, travaux_no_photo_post=True).create(vals)
        bt._notify_team()
        return bt

    @api.model
    def tv_lancer(self, circuit_id, date=False):
        """« Lancer la tournée » : crée le bon de la tournée (discussion + liste des zones)."""
        Demande = self.env["travaux.demande"]
        if not Demande._tv_droits()["peut_envoyer"]:
            raise AccessError(_("C'est le superviseur qui lance les tournées."))
        circuit = self.sudo().browse(int(circuit_id)).exists()
        if not circuit:
            raise UserError(_("Cette tournée n'existe plus."))
        if circuit.lancement_en_cours:
            raise UserError(_("La tournée « %s » est déjà lancée.", circuit.name))
        zones = circuit._zones_actives()
        if not zones:
            raise UserError(_("Ajoutez d'abord des zones à la tournée."))
        date = fields.Date.to_date(date) if date else fields.Date.context_today(self)
        bt = circuit._creer_bt(date)
        circuit.write({"lancement_en_cours": True, "lancement_debut": date})
        if circuit.mode == "lot":
            body = _("Tournée « %(t)s » lancée par %(u)s : %(n)s point(s), en une fois.", t=circuit.name, u=self.env.user.name, n=len(zones))
        else:
            body = _("Tournée « %(t)s » lancée par %(u)s : %(n)s zone(s). Faites-les dans l'ordre qui vous arrange, "
                     "sur plusieurs jours si nécessaire.", t=circuit.name, u=self.env.user.name, n=len(zones))
        bt.message_post(body=body, message_type="notification", subtype_xmlid="mail.mt_note")
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "demande", "ids": [bt.id]})
        return bt.id

    @api.model
    def tv_arreter(self, circuit_id):
        """Arrêter une tournée lancée : les zones faites restent faites, la tournée est clôturée (ou annulée)."""
        if not self.env["travaux.demande"]._tv_is_sup():
            raise AccessError(_("Réservé au superviseur."))
        circuit = self.sudo().browse(int(circuit_id)).exists()
        bt = circuit._bt_cours_tournee()
        if bt:
            bt._tz_arreter(_("Tournée arrêtée par le superviseur"))
        circuit._fin_tournee()
        return True

    def _fin_tournee(self):
        for c in self:
            c.write({"lancement_en_cours": False, "derniere_tournee": fields.Date.context_today(c)})

    def _tv_dict(self):
        d = super()._tv_dict()
        bt = self._bt_cours_tournee() if self.lancement_en_cours else self.env["travaux.demande"]
        jours = self._jours_sans_tournee()
        d.update({
            "lance": self.lancement_en_cours, "lance_le": self.lancement_debut.isoformat() if self.lancement_debut else False,
            "bt_id": bt.id or False, "bt_titre": bt.titre or "", "bt_state": bt.state or "",
            "bt_done": len(bt._tz_passages().zone_id) if bt else 0,
            "jours": jours, "a_relancer": self._a_relancer(),
        })
        return d


class TravauxPassage(models.Model):
    _inherit = "travaux.passage"

    pas_necessaire = fields.Boolean("Pas nécessaire", help="Zone vue, mais pas d'entretien nécessaire ce jour-là.")
    tournee_bt_id = fields.Many2one("travaux.demande", "Tournée lancée", index=True, ondelete="set null",
                                    help="Bon de la tournée pendant laquelle la zone a été passée.")


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    tournee_id = fields.Many2one("travaux.demande", "Bon de la tournée", index=True, ondelete="set null", copy=False,
                                 help="Sous-bon d'une zone : il dépend du bon de la tournée lancée.")
    zone_bt_ids = fields.One2many("travaux.demande", "tournee_id", "Sous-bons des zones")

    # ------------------------------------------------------------ outils
    def _is_tz(self):
        """Bon d'une tournée « une par une » lancée : il porte la liste des zones."""
        self.ensure_one()
        return bool(self.circuit_id) and self.circuit_id.mode != "lot" and not self.zone_id and not self.tournee_id \
            and self.source == "planning" and not self.recurrence_id

    def _tz_passages(self):
        if not self:
            return self.env["travaux.passage"]
        return self.env["travaux.passage"].sudo().search([("tournee_bt_id", "=", self.id)])

    def _tz_zones(self):
        """Les zones de la tournée et leur état dans ce lancement."""
        self.ensure_one()
        circuit = self.circuit_id.sudo()
        today = fields.Date.context_today(self)
        passages = self._tz_passages()
        cours = self.sudo().zone_bt_ids.filtered(lambda b: b.state == "cours")
        out = []
        for z in circuit._zones_actives():
            p = passages.filtered(lambda x: x.zone_id == z).sorted("id")[-1:]
            bt = cours.filtered(lambda b: b.zone_id == z)[:1]
            status = ("inutile" if p.pas_necessaire else "fait") if p else ("cours" if bt else "todo")
            days = (today - z.dernier_passage).days if z.dernier_passage else None
            out.append({
                "id": z.id, "name": z.name, "note": z.note or "", "lieu": ("#" + z.lieu_id.name) if z.lieu_id else "",
                "lat": z.lieu_id.latitude or False, "lng": z.lieu_id.longitude or False,
                "vignette": ("/web/image/travaux.circuit.zone/%s/photo/160x160?unique=%s" % (z.id, int(z.write_date.timestamp()))) if z.photo else False,
                "status": status, "h": p.heures if p else 0.0, "date": p.date.isoformat() if p else False,
                "today": bool(p) and p.date == today, "par": p.user_id.name or "" if p else "",
                "debut": fields.Datetime.to_string(bt.date_debut) if bt.date_debut else False,
                "days": days, "duree": z.duree,
            })
        return out

    def _tz_noter(self, zone, heures, child=False, inutile=False):
        """Passage de la zone pendant cette tournée : historique, dernier passage, tour des zones."""
        circuit = zone.circuit_id.sudo()
        today = fields.Date.context_today(self)
        if not circuit.tour_debut:
            circuit.tour_debut = today
        self.env["travaux.passage"].sudo().create({
            "zone_id": zone.id, "date": today, "user_id": self.env.uid, "tour_no": circuit.tour_no, "heures": heures,
            "demande_id": child.id if child else False, "pas_necessaire": inutile, "tournee_bt_id": self.id})
        zone.sudo().write({"dernier_passage": today, "tour_fait": circuit.tour_no})
        zones = circuit._zones_actives()
        if zones and all(z.tour_fait == circuit.tour_no for z in zones):
            circuit.write({"tour_no": circuit.tour_no + 1, "tour_debut": False})

    def _tz_fin_si_complet(self):
        """Toutes les zones sont passées : la tournée est clôturée."""
        self.ensure_one()
        circuit = self.circuit_id.sudo()
        faites = self._tz_passages()
        if not set(circuit._zones_actives().ids) <= set(faites.zone_id.ids):
            return False
        self._tz_clore(faites, _("Tournée terminée"))
        return True

    def _tz_clore(self, faites, titre):
        now = fields.Datetime.now()
        heures = sum(self.sudo().zone_bt_ids.filtered(lambda b: b.state == "close").mapped("duree_mesuree"))
        inutiles = len(faites.filtered("pas_necessaire").zone_id)
        self.sudo().with_context(tracking_disable=True).write({
            "state": "close", "date_fin": now, "date_cloture": now, "cloture_par_id": self.env.uid})
        self.message_post(body=_("%(t)s : %(f)s zone(s) faite(s), %(i)s pas nécessaire(s) · %(h)s h comptées pour l'équipe.",
                                 t=titre, f=len(faites.zone_id) - inutiles, i=inutiles, h=_h(heures)),
                          message_type="notification", subtype_xmlid="mail.mt_note")
        self.circuit_id.sudo()._fin_tournee()

    def _tz_arreter(self, motif):
        """Arrêt (superviseur) ou annulation : les zones démarrées sont abandonnées, les zones faites restent."""
        self.ensure_one()
        for child in self.sudo().zone_bt_ids.filtered(lambda b: b.state in ("planif", "cours")):
            child.pointage_ids.unlink()
            child.unlink()
        faites = self._tz_passages()
        if self.state in ("planif", "cours", "valider"):
            if faites:
                self._tz_clore(faites, motif)
            else:
                self.sudo().write({"state": "annulee", "annulation": "annul", "motif_annulation": motif})
                self.message_post(body=motif, message_type="notification", subtype_xmlid="mail.mt_note")
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "demande", "ids": [self.id]})

    @api.model
    def _tz_get(self, rec_id, zone_id):
        rec = self.browse(int(rec_id)).exists()
        if not rec or not rec._is_tz():
            raise UserError(_("Ce bon n'est pas une tournée de zones."))
        rec.check_access("read")
        if rec.state not in ("planif", "cours"):
            raise UserError(_("Cette tournée est terminée."))
        zone = self.env["travaux.circuit.zone"].sudo().browse(int(zone_id)).exists()
        if not zone or zone.circuit_id != rec.circuit_id:
            raise UserError(_("Cette zone ne fait pas partie de la tournée."))
        zone.with_user(self.env.user)._check_team()
        return rec, zone

    def _tz_child(self, zone):
        return self.sudo().zone_bt_ids.filtered(lambda b: b.zone_id == zone and b.state == "cours")[:1]

    def _tz_live(self):
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "demande", "ids": [self.id]})
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "zones", "ids": [self.circuit_id.id]})

    # ------------------------------------------------------------ zones de la tournée (terrain)
    @api.model
    def tv_tz_demarrer(self, rec_id, zone_id):
        """« Démarrer » une zone : son sous-bon s'ouvre et le temps est mesuré."""
        rec, zone = self._tz_get(rec_id, zone_id)
        if rec._tz_passages().filtered(lambda p: p.zone_id == zone):
            raise UserError(_("Cette zone est déjà faite pour cette tournée."))
        if not rec._tz_child(zone):
            circuit = rec.circuit_id.sudo()
            child = self.sudo().with_context(mail_create_nolog=True, travaux_no_photo_post=True).create({
                "titre": "%s · %s" % (circuit.name, zone.name), "type_travail": "tournee", "equipe_id": rec.equipe_id.id,
                "date_prevue": fields.Date.context_today(self), "state": "planif", "source": "planning",
                "circuit_id": circuit.id, "zone_id": zone.id, "lieu_id": zone.lieu_id.id or False,
                "consigne": zone.note or False, "duree_prevue": zone.duree or 0.0, "tournee_id": rec.id})
            child.with_user(self.env.user).action_demarrer()
            if rec.state == "planif":
                rec.sudo().with_context(tracking_disable=True).write({"state": "cours", "date_debut": fields.Datetime.now()})
            rec.message_post(body=_("%(u)s a démarré « %(z)s ».", u=self.env.user.name, z=zone.name),
                             message_type="notification", subtype_xmlid="mail.mt_note")
        rec._tz_live()
        return self.tv_conversation(rec.id)

    @api.model
    def tv_tz_stop(self, rec_id, zone_id):
        """Démarré par erreur : le sous-bon est supprimé, rien n'est compté."""
        rec, zone = self._tz_get(rec_id, zone_id)
        child = rec._tz_child(zone)
        child.pointage_ids.unlink()
        child.unlink()
        rec._tz_live()
        return self.tv_conversation(rec.id)

    @api.model
    def tv_tz_presents(self, rec_id, zone_id):
        rec, zone = self._tz_get(rec_id, zone_id)
        child = rec._tz_child(zone)
        if not child:
            raise UserError(_("Appuyez d'abord sur « Démarrer »."))
        res = self.sudo().tv_presents(child.id)
        res["debut"] = fields.Datetime.to_string(child.date_debut)
        return res

    @api.model
    def tv_tz_fait(self, rec_id, zone_id, employee_ids, duree=False):
        """« Fait » : présents et durée, le sous-bon est clôturé (durée × personnes), la zone est cochée."""
        rec, zone = self._tz_get(rec_id, zone_id)
        child = rec._tz_child(zone)
        if not child:
            raise UserError(_("Appuyez d'abord sur « Démarrer »."))
        emps = self.env["hr.employee"].sudo().browse([int(i) for i in (employee_ids or [])]).exists()
        if not emps:
            raise UserError(_("Cochez au moins une personne présente."))
        today = fields.Date.context_today(self)
        duree = round(float(duree), 2) if duree not in (False, None, "") else child._clock_now()
        child._check_heures_jour(emps, duree)
        child.with_context(tracking_disable=True).write(
            {"nb_presents": len(emps), "presents": ", ".join(emps.sorted("name").mapped("name")),
             "present_ids": [(6, 0, emps.ids)], "date_travail": today})
        child.with_user(self.env.user).sudo().with_context(travaux_duree=duree).action_terminer()
        child.write({"state": "close", "date_cloture": fields.Datetime.now(), "cloture_par_id": self.env.uid})
        rec._tz_noter(zone, child.duree_mesuree, child=child)
        rec.message_post(body=_("« %(z)s » faite : %(d)s h × %(n)s (%(p)s) = %(h)s h.", z=zone.name, d=_h(duree), n=len(emps),
                                p=", ".join(emps.sorted("name").mapped("name")), h=_h(child.duree_mesuree)),
                         message_type="notification", subtype_xmlid="mail.mt_note")
        rec._tz_fin_si_complet()
        rec._tz_live()
        return self.tv_conversation(rec.id)

    @api.model
    def tv_tz_inutile(self, rec_id, zone_id):
        """« Pas nécessaire » : la zone est vue, clôturée à 0 h."""
        rec, zone = self._tz_get(rec_id, zone_id)
        if rec._tz_passages().filtered(lambda p: p.zone_id == zone):
            raise UserError(_("Cette zone est déjà faite pour cette tournée."))
        child = rec._tz_child(zone)
        if child:
            child.pointage_ids.unlink()
            child.unlink()
        if rec.state == "planif":
            rec.sudo().with_context(tracking_disable=True).write({"state": "cours", "date_debut": fields.Datetime.now()})
        rec._tz_noter(zone, 0.0, inutile=True)
        rec.message_post(body=_("« %(z)s » : vue par %(u)s, pas d'entretien nécessaire (0 h).", z=zone.name, u=self.env.user.name),
                         message_type="notification", subtype_xmlid="mail.mt_note")
        rec._tz_fin_si_complet()
        rec._tz_live()
        return self.tv_conversation(rec.id)

    @api.model
    def tv_tz_annuler(self, rec_id, zone_id):
        """Annule le passage d'aujourd'hui d'une zone (appui par erreur)."""
        rec, zone = self._tz_get(rec_id, zone_id)
        last = zone._last()
        if not last or last.tournee_bt_id != rec or last.date != fields.Date.context_today(self):
            raise UserError(_("Seul un passage d'aujourd'hui peut être annulé."))
        self.env["travaux.circuit.zone"].tv_annuler(zone.id)
        rec.message_post(body=_("« %(z)s » : passage annulé par %(u)s.", z=zone.name, u=self.env.user.name),
                         message_type="notification", subtype_xmlid="mail.mt_note")
        rec._tz_live()
        return self.tv_conversation(rec.id)

    # ------------------------------------------------------------ surcharges
    @api.model
    def tv_terminer(self, rec_id, employee_ids, pct=False, duree=False):
        res = super().tv_terminer(rec_id, employee_ids, pct, duree)
        rec = self.browse(rec_id)
        if rec.circuit_id and not rec.zone_id and rec.circuit_id.mode == "lot" and rec.circuit_id.lancement_en_cours:
            rec.circuit_id.sudo()._fin_tournee()   # tournée en lot : un seul bon, la tournée est faite
        return res

    @api.model
    def tv_annuler_bt(self, rec_id, motif):
        rec = self.browse(rec_id)
        if rec._is_tz():
            rec._tz_arreter(_("Tournée annulée : %s", motif))
            return self.tv_conversation(rec_id)
        res = super().tv_annuler_bt(rec_id, motif)
        if rec.circuit_id and rec.circuit_id.lancement_en_cours and rec.source == "planning" and not rec.recurrence_id \
                and not rec.tournee_id:
            rec.circuit_id.sudo()._fin_tournee()
        return res

    def _tv_card(self, last=None):
        d = super()._tv_card(last)
        d["tz_child"] = bool(self.tournee_id)
        if self._is_tz():
            zones = self._tz_zones()
            todo = [z for z in zones if z["status"] in ("todo", "cours")]
            d.update({
                "tz_done": len(zones) - len(todo), "tz_total": len(zones),
                "pts": [[z["lat"], z["lng"]] for z in todo if z["lat"]],
                "duree_mesuree": sum(self.sudo().zone_bt_ids.filtered(lambda b: b.state == "close").mapped("duree_mesuree")),
            })
        return d

    @api.model
    def tv_conversation(self, rec_id):
        res = super().tv_conversation(rec_id)
        rec = self.browse(rec_id)
        if rec._is_tz():
            res.update({"tz": rec._tz_zones(), "can_start": False, "can_finish": False, "can_impute": False,
                        "can_reopen": False, "can_close": False, "liste": ""})
        return res

    @api.model
    def tv_terrain_list(self):
        res = super().tv_terrain_list()
        res["items"] = [i for i in res["items"] if not i.get("tz_child")]
        return res

    @api.model
    def tv_suivi(self, week_offset=0, kmode="week", koffset=0):
        res = super().tv_suivi(week_offset, kmode, koffset)
        for key in ("jobs", "tasks", "entretiens", "tasks_mois"):
            if isinstance(res.get(key), list):
                res[key] = [i for i in res[key] if not i.get("tz_child")]
        Circuit = self.env["travaux.circuit"]
        circuits = Circuit._tv_visible(saison=False)
        res["tournees"] = [{"id": c.id, "name": c.name, "team_id": c.equipe_id.id, "team": c.equipe_id.name,
                            "mode": c.mode, "lance": c.lancement_en_cours} for c in circuits.sorted(lambda c: (c.equipe_id.name or "", c.name))]
        res["a_relancer"] = [{"id": c.id, "name": c.name, "team": c.equipe_id.name, "team_id": c.equipe_id.id,
                              "jours": c._jours_sans_tournee()} for c in circuits if c._a_relancer()]
        return res
