"""Entretiens planifiés et clôture du travail par l'équipe.

- « Terminé » : l'équipe confirme qui était présent ; le temps compté est la durée sur place multipliée
  par le nombre de personnes (toujours enregistré pour l'équipe, jamais par personne).
- Circuits (poubelles, balayeuse, hydrocureuse) : à « Terminé », l'équipe indique 50, 80 ou 100 % du circuit.
- Une intervention planifiée non clôturée glisse à la semaine suivante ; le superviseur voit « reportée N× ».
- Les entretiens planifiés (circuits, actions récurrentes) arrivent dans la liste de l'équipe, comme une demande.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

PARAM_REPORT = "service_travaux.report_semaine"
PARAM_MAX_JOUR = "service_travaux.max_heures_jour"   # heures de travail maximum par ouvrier et par jour


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    nb_presents = fields.Integer("Personnes présentes", copy=False,
                                 help="Nombre de personnes confirmé par l'équipe à « Terminé ».")
    presents = fields.Char("Présents", copy=False)
    duree_horloge = fields.Float("Durée sur place (h)", copy=False,
                                 help="Entre « Démarrer » et « Terminé ». Le temps compté est cette durée × le nombre de personnes.")
    avancement = fields.Integer("Part du circuit réalisée (%)", copy=False)
    present_ids = fields.Many2many("hr.employee", "travaux_demande_present_rel", "demande_id", "employee_id",
                                   "Agents présents", copy=False)
    date_travail = fields.Date("Jour du travail", copy=False, index=True,
                               help="Jour où l'équipe a terminé : sert au plafond d'heures par ouvrier et par jour.")
    annulation = fields.Selection([("refus", "Demande refusée"), ("annul", "Bon de travail annulé")], "Annulation", copy=False)
    motif_annulation = fields.Text("Motif", copy=False)
    nb_reports = fields.Integer("Reports", copy=False, readonly=True,
                                help="Nombre de semaines où l'intervention planifiée a glissé faute d'être clôturée.")

    # ------------------------------------------------------------ « Terminé » : présents et temps
    def _tv_ask_pct(self):
        self.ensure_one()
        if self.type_travail != "tournee" or self.zone_id:
            return False
        return not self.circuit_id or self.circuit_id.mode == "lot"

    @api.model
    def tv_presents(self, rec_id):
        """Agents à proposer à la clôture : ceux de l'équipe présents aujourd'hui (cochés), puis les autres présents."""
        rec = self.browse(rec_id).exists()
        if not rec:
            raise UserError(_("Cette demande n'existe plus."))
        team = rec.sudo().equipe_id
        here = self.env["hr.employee"].sudo().search([("statut_jour", "=", "present"), ("equipe_jour_id", "!=", False)])
        mine = here.filtered(lambda e: e.equipe_jour_id == team)
        if not mine:  # présences du jour non tenues : on propose les membres habituels
            mine = team.member_ids
        others = (here - mine).sorted(lambda e: (e.equipe_jour_id.name or "", e.name))
        return {
            "team": team.name or "",
            "people": [{"id": e.id, "name": e.name, "on": True} for e in mine.sorted("name")],
            "others": [{"id": e.id, "name": e.name, "team": e.equipe_jour_id.name or "", "on": False} for e in others],
            "ask_pct": rec._tv_ask_pct(), "max_jour": self._max_jour(),
        }

    # ------------------------------------------------------------ plafond d'heures par ouvrier et par jour
    @api.model
    def _max_jour(self):
        try:
            return float(self.env["ir.config_parameter"].sudo().get_param(PARAM_MAX_JOUR) or 7.5)
        except ValueError:
            return 7.5

    def _check_heures_jour(self, emps, duree, day=None):
        """Refuse une clôture qui ferait dépasser le plafond (7 h 30 par défaut) à un des agents présents."""
        day = day or fields.Date.context_today(self)
        limite = self._max_jour()
        trop = []
        for e in emps:
            deja = sum(self.sudo().search([("present_ids", "in", e.id), ("date_travail", "=", day),
                                           ("id", "not in", self.ids)]).mapped("duree_horloge"))
            if deja + duree > limite + 0.01:
                trop.append(_("%(n)s (déjà %(h)s aujourd'hui)", n=e.name, h=self._fmt_heures(deja)))
        if trop:
            raise UserError(_("Pas plus de %(l)s de travail par jour et par personne. Avec %(d)s, cela dépasserait pour : %(t)s.\n"
                              "Corrigez la durée ou retirez ces personnes des présents.",
                              l=self._fmt_heures(limite), d=self._fmt_heures(duree), t=", ".join(trop)))

    def _clock_now(self):
        self.ensure_one()
        start = self.date_debut or fields.Datetime.now()
        return round((fields.Datetime.now() - start).total_seconds() / 3600.0, 2)

    @api.model
    def tv_terminer(self, rec_id, employee_ids, pct=False, duree=False):
        rec = self.browse(rec_id).exists()
        if not rec:
            raise UserError(_("Cette demande n'existe plus."))
        emps = self.env["hr.employee"].sudo().browse([int(i) for i in (employee_ids or [])]).exists()
        if not emps:
            raise UserError(_("Cochez au moins une personne présente."))
        duree = round(float(duree), 2) if duree not in (False, None, "") else rec._clock_now()
        if duree <= 0:
            raise UserError(_("Indiquez la durée sur place."))
        rec._check_heures_jour(emps, duree)
        vals = {"nb_presents": len(emps), "presents": ", ".join(emps.sorted("name").mapped("name")),
                "present_ids": [(6, 0, emps.ids)], "date_travail": fields.Date.context_today(self)}
        if rec._tv_ask_pct():
            if pct not in (50, 80, 100):
                raise UserError(_("Indiquez la part du circuit réalisée."))
            vals["avancement"] = pct
        rec.with_context(tracking_disable=True).write(vals)
        rec.with_context(travaux_duree=duree).action_terminer()
        return self.tv_conversation(rec_id)

    @api.model
    def tv_set_duree(self, rec_id, hours):
        """Correction : l'heure saisie est la durée sur place ; elle est multipliée par le nombre de personnes."""
        rec = self.browse(rec_id)
        hours = max(0.0, float(hours))
        n = max(1, rec.nb_presents)
        if rec.present_ids:
            rec._check_heures_jour(rec.present_ids, hours, rec.date_travail)
        super().tv_set_duree(rec_id, hours * n)
        rec.duree_horloge = hours
        return self.tv_conversation(rec_id)

    # ------------------------------------------------------------ refus d'une demande, annulation d'un bon
    @api.model
    def tv_refuser(self, rec_id, motif):
        """« Refuser » dans Demandes à traiter : la demande est classée, le demandeur reçoit le motif."""
        if not self._tv_droits()["bureau"]:
            raise AccessError(_("Réservé au secrétariat et aux superviseurs."))
        rec = self.browse(rec_id).exists()
        motif = (motif or "").strip()
        if not motif:
            raise UserError(_("Indiquez le motif du refus : il est envoyé au demandeur."))
        rec.write({"state": "annulee", "annulation": "refus", "motif_annulation": motif})
        partners = rec._contacts() | rec.demandeur_user_id.partner_id
        rec.message_post(
            body=_("Votre demande %(r)s « %(t)s » ne sera pas traitée par le service technique.\nMotif : %(m)s",
                   r=rec.name, t=rec.titre, m=motif),
            message_type="comment", subtype_xmlid="mail.mt_comment", partner_ids=partners.ids)
        return True

    @api.model
    def tv_annuler_bt(self, rec_id, motif):
        """« Annuler le bon » dans Suivi : le travail ne sera pas fait (doublon, plus nécessaire…)."""
        if not self._tv_is_sup():
            raise AccessError(_("Réservé au superviseur."))
        rec = self.browse(rec_id).exists()
        motif = (motif or "").strip()
        if not motif:
            raise UserError(_("Indiquez pourquoi le bon est annulé."))
        if rec.state in ("close", "annulee"):
            raise UserError(_("Ce bon est déjà clôturé ou annulé."))
        rec.write({"state": "annulee", "annulation": "annul", "motif_annulation": motif})
        rec.message_post(body=_("Bon de travail annulé : %s", motif), message_type="notification", subtype_xmlid="mail.mt_note")
        return self.tv_conversation(rec_id)

    # ------------------------------------------------------------ données d'écran
    def _tv_card(self, last=None):
        d = super()._tv_card(last)
        d.update({
            "reports": self.nb_reports, "nb_presents": self.nb_presents, "presents": self.presents or "",
            "duree_horloge": self.duree_horloge, "avancement": self.avancement, "ask_pct": self._tv_ask_pct(),
            "motif": self.motif_annulation or "", "annulation": self.annulation or "",
        })
        return d

    @api.model
    def _tv_accueil(self):
        """Accueil de FACILITY : le kanban pour le bureau (superviseurs, secrétariat), l'app terrain pour les équipes."""
        bureau = self._tv_is_sup() or self.env.user.has_group("service_travaux.group_secretariat")
        return self.env["ir.actions.actions"]._for_xml_id(
            "service_travaux.action_client_suivi" if bureau else "service_travaux.action_client_terrain")

    @api.model
    def tv_terrain_list(self):
        self._reporter_semaine()
        res = super().tv_terrain_list()
        if self._tv_scope() == "equipe":
            # les ouvriers ne voient que le travail planifié pour eux, pas les demandes en attente d'attribution
            res["items"] = [i for i in res["items"] if i["state"] not in ("recue", "precision", "transferee", "annulee")]
        # entretiens planifiés, dans la même liste que les demandes : circuits du jour, actions récurrentes de la semaine
        today = fields.Date.context_today(self)
        friday = today - timedelta(days=today.weekday()) + timedelta(days=6)
        week_ago = fields.Datetime.now() - timedelta(days=7)
        recs = self.search([
            ("type_travail", "in", ("tournee", "maintenance")), ("equipe_id", "!=", False),
            "|", "|", "|", "|", ("state", "in", ("cours", "valider")),
            "&", "&", ("state", "=", "planif"), ("type_travail", "=", "maintenance"), ("date_prevue", "<=", friday),
            "&", "&", "&", ("state", "=", "planif"), ("type_travail", "=", "tournee"), ("circuit_id", "=", False), ("date_prevue", "=", today),
            # tournée lancée : le bon de la zone (ou du lot) en attente reste visible jusqu'à ce qu'il soit fait
            "&", "&", ("state", "=", "planif"), ("circuit_id", "!=", False), ("date_prevue", "<=", today),
            "&", "&", "&", ("state", "=", "close"), ("date_cloture", ">=", week_ago), ("zone_id", "=", False), ("circuit_id", "=", False)])
        extra = [r._tv_card(r._tv_last(r._tv_messages())) for r in recs]
        res["items"] = sorted(res["items"] + extra, key=lambda r: r["last"]["date"] or "", reverse=True)
        return res

    @api.model
    def tv_suivi(self, week_offset=0, kmode="week", koffset=0):
        """Le kanban a sa propre période : la semaine en cours par défaut, une autre semaine ou un mois."""
        self._reporter_semaine()
        res = super().tv_suivi(week_offset)
        today = fields.Date.context_today(self)
        if kmode == "month":
            start = (today.replace(day=1) + relativedelta(months=koffset))
            end = start + relativedelta(months=1, days=-1)
        else:
            start = today - timedelta(days=today.weekday()) + timedelta(weeks=koffset)
            end = start + timedelta(days=6)
        courant = start <= today <= end
        d0, d1 = fields.Datetime.to_datetime(start), fields.Datetime.to_datetime(end + timedelta(days=1))
        if not (courant and kmode == "week"):
            # autre période : les demandes prévues ou clôturées dans la période (la pile « à attribuer » reste entière)
            jobs = self.search([
                ("type_travail", "=", "demande"),
                "|", "|", ("state", "in", ("recue", "precision")),
                "&", "&", ("state", "in", ("planif", "cours", "valider")), ("date_prevue", ">=", start), ("date_prevue", "<=", end),
                "&", "&", ("state", "=", "close"), ("date_cloture", ">=", d0), ("date_cloture", "<", d1)])
            res["jobs"] = [j._tv_card() for j in jobs]
        # entretiens (circuits, actions récurrentes, zones) de la période, dans les mêmes colonnes que les demandes
        dom = [("type_travail", "in", ("tournee", "maintenance")), ("state", "in", ("planif", "cours", "valider", "close")),
               ("date_prevue", ">=", start), ("date_prevue", "<=", end)]
        if courant:   # ce qui est en cours ou à valider reste visible, quelle que soit sa date
            dom = [("type_travail", "in", ("tournee", "maintenance")),
                   "|", ("state", "in", ("cours", "valider")), "&", "&", ("state", "in", ("planif", "close")),
                   ("date_prevue", ">=", start), ("date_prevue", "<=", end)]
        res["entretiens"] = [e._tv_card() for e in self.search(dom, order="date_prevue, id")]
        if kmode == "month":   # planning du mois : les tâches de chaque semaine du mois
            res["tasks_mois"] = [t._tv_card() for t in self.search([
                ("date_prevue", ">=", start), ("date_prevue", "<=", end),
                ("state", "not in", ("recue", "precision", "transferee", "annulee"))])]
        res["kanban"] = {"mode": kmode, "offset": koffset, "start": start.isoformat(), "end": end.isoformat(),
                         "week": start.isocalendar()[1], "courant": courant}
        return res

    @api.model
    def tv_queue(self):
        res = super().tv_queue()
        for d in res["items"]:
            d["entretien"] = self.browse(d["id"])._tv_entretien_prevu()
        return res

    def _tv_entretien_prevu(self):
        """Phrase « un entretien est déjà prévu sur ce lieu », ou chaîne vide."""
        self.ensure_one()
        if not self.lieu_id:
            return ""
        today = fields.Date.context_today(self)
        nxt = self.sudo().search([
            ("type_travail", "in", ("tournee", "maintenance")), ("lieu_id", "=", self.lieu_id.id),
            ("state", "=", "planif"), ("date_prevue", ">=", today)], order="date_prevue", limit=1)
        if nxt:
            return _("Entretien prévu sur #%(l)s : %(t)s, le %(d)s (%(e)s).", l=self.lieu_id.name, t=nxt.titre,
                     d=nxt.date_prevue.strftime("%d/%m"), e=nxt.equipe_id.name or _("sans équipe"))
        zone = self.env["travaux.circuit.zone"].sudo().search(
            [("lieu_id", "=", self.lieu_id.id), ("active", "=", True), ("circuit_id.active", "=", True)], limit=1)
        if zone:
            last = zone.dernier_passage.strftime("%d/%m") if zone.dernier_passage else _("jamais")
            return _("#%(l)s est une zone d'entretien de l'équipe %(e)s (dernier passage : %(d)s).",
                     l=self.lieu_id.name, e=zone.circuit_id.equipe_id.name, d=last)
        return ""

    # ------------------------------------------------------------ glissement de semaine
    @api.model
    def _reporter_semaine(self):
        """Une fois par semaine : les interventions planifiées non clôturées des semaines passées glissent
        à la semaine en cours (même jour). Les circuits récurrents ne glissent pas : le suivant les remplace."""
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        ICP = self.env["ir.config_parameter"].sudo()
        if ICP.get_param(PARAM_REPORT) == monday.isoformat():
            return 0
        ICP.set_param(PARAM_REPORT, monday.isoformat())
        late = self.sudo().with_context(tracking_disable=True, travaux_no_live=True).search([
            ("state", "in", ("planif", "cours")), ("date_prevue", "<", monday),
            "|", ("type_travail", "!=", "tournee"), ("circuit_id", "!=", False)])
        for rec in late:
            weeks = ((monday - rec.date_prevue).days + 6) // 7
            rec.write({"date_prevue": rec.date_prevue + timedelta(weeks=weeks), "nb_reports": rec.nb_reports + weeks})
        return len(late)

    @api.model
    def _cron_reporter_semaine(self):
        return self._reporter_semaine()
