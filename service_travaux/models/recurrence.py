"""Tâches récurrentes : tournées, circuits, maintenances périodiques.

Les réglages reprennent ceux d'Odoo (calendrier) : répéter tous les N jours / semaines / mois / ans,
jours de la semaine, « le premier mardi du mois », jusqu'à une date ou un nombre de fois.
Chaque occurrence est une tâche normale du planning (travaux.demande), créée quelques semaines à l'avance.
"""
from datetime import timedelta

from dateutil import rrule
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

HORIZON_JOURS = 35  # les occurrences sont créées 5 semaines à l'avance
JOURS = [("mon", "Lundi"), ("tue", "Mardi"), ("wed", "Mercredi"), ("thu", "Jeudi"),
         ("fri", "Vendredi"), ("sat", "Samedi"), ("sun", "Dimanche")]
RR_DAYS = {"mon": rrule.MO, "tue": rrule.TU, "wed": rrule.WE, "thu": rrule.TH,
           "fri": rrule.FR, "sat": rrule.SA, "sun": rrule.SU}
RR_FREQ = {"day": rrule.DAILY, "week": rrule.WEEKLY, "month": rrule.MONTHLY, "year": rrule.YEARLY}
RANGS = [("1", "Premier"), ("2", "Deuxième"), ("3", "Troisième"), ("4", "Quatrième"), ("-1", "Dernier")]


class TravauxRecurrence(models.Model):
    _name = "travaux.recurrence"
    _description = "Tâche récurrente (tournée, circuit, maintenance)"
    _order = "equipe_id, name"

    name = fields.Char("Tâche", required=True, help="Ex. Corbeilles centre, Balayage du marché, Contrôle chaufferies.")
    type_travail = fields.Selection(
        [("tournee", "Tournée / circuit"), ("maintenance", "Maintenance planifiée")],
        "Type", default="tournee", required=True)
    equipe_id = fields.Many2one("travaux.equipe", "Équipe", required=True, domain=[("is_pool", "=", False)])
    duree_prevue = fields.Float("Temps prévu (h)", default=2.0)
    adresse = fields.Char("Où", help="Secteur ou adresse : Centre, Villages, Rue de la Station…")
    lieu_id = fields.Many2one("travaux.lieu", "# Lieu", domain=[("kind", "=", "lieu")])
    objet_ids = fields.Many2many("travaux.lieu", "travaux_recurrence_objet_rel", "recurrence_id", "objet_id",
                                 string="# Objets", domain=[("kind", "=", "objet")])
    consigne = fields.Text("Consigne pour l'équipe")
    liste = fields.Text("Liste indicative", help="Rues, arrêts ou points de contrôle, un par ligne. "
                                                 "Affichée à l'équipe pour mémoire, rien à cocher.")
    active = fields.Boolean(default=True)

    # ---- répétition (mêmes réglages qu'Odoo)
    date_start = fields.Date("À partir du", required=True, default=fields.Date.context_today)
    repeat_interval = fields.Integer("Répéter tous les", default=1, required=True)
    repeat_unit = fields.Selection([("day", "Jours"), ("week", "Semaines"), ("month", "Mois"), ("year", "Années")],
                                   "Unité", default="week", required=True)
    mon = fields.Boolean("Lun")
    tue = fields.Boolean("Mar")
    wed = fields.Boolean("Mer")
    thu = fields.Boolean("Jeu")
    fri = fields.Boolean("Ven")
    sat = fields.Boolean("Sam")
    sun = fields.Boolean("Dim")
    repeat_on_month = fields.Selection([("date", "Date du mois"), ("day", "Jour du mois")], "Chaque mois, le",
                                       default="day", required=True)
    repeat_day = fields.Integer("Date", default=1)
    repeat_week = fields.Selection(RANGS, "Rang", default="1")
    repeat_weekday = fields.Selection(JOURS, "Jour", default="mon")
    repeat_type = fields.Selection([("forever", "Toujours"), ("until", "Jusqu'au"), ("after", "Nombre de fois")],
                                   "Fin", default="forever", required=True)
    repeat_until = fields.Date("Date de fin")
    repeat_number = fields.Integer("Répétitions", default=10)

    resume = fields.Char("Répétition", compute="_compute_resume")
    prochaine = fields.Date("Prochaine", compute="_compute_resume")
    demande_ids = fields.One2many("travaux.demande", "recurrence_id", "Tâches créées")
    nb_occurrences = fields.Integer("Occurrences", compute="_compute_nb")

    @api.constrains("repeat_interval", "repeat_day", "repeat_unit", "repeat_type", "repeat_until", "date_start")
    def _check_repeat(self):
        for rec in self:
            if rec.repeat_interval <= 0:
                raise ValidationError(_("L'intervalle doit être d'au moins 1."))
            if rec.repeat_unit == "month" and rec.repeat_on_month == "date" and not 1 <= rec.repeat_day <= 31:
                raise ValidationError(_("La date du mois doit être comprise entre 1 et 31."))
            if rec.repeat_type == "until" and (not rec.repeat_until or rec.repeat_until < rec.date_start):
                raise ValidationError(_("La date de fin doit être après la date de début."))

    def _compute_nb(self):
        counts = {r.id: n for r, n in self.env["travaux.demande"]._read_group(
            [("recurrence_id", "in", self.ids)], ["recurrence_id"], ["__count"])}
        for rec in self:
            rec.nb_occurrences = counts.get(rec.id, 0)

    # ------------------------------------------------------------ calcul des dates
    def _weekdays(self):
        self.ensure_one()
        return [k for k, _l in JOURS if self[k]]

    def _rrule(self):
        self.ensure_one()
        kw = {"dtstart": fields.Datetime.to_datetime(self.date_start), "interval": max(1, self.repeat_interval)}
        if self.repeat_unit == "week":
            days = self._weekdays() or [JOURS[self.date_start.weekday()][0]]
            kw["byweekday"] = [RR_DAYS[d] for d in days]
            kw["wkst"] = rrule.MO
        elif self.repeat_unit == "month":
            if self.repeat_on_month == "date":
                # le 31 d'un mois de 30 jours : dernier jour du mois
                kw["bymonthday"] = [self.repeat_day] if self.repeat_day <= 28 else list(range(28, self.repeat_day + 1))
                kw["bysetpos"] = -1
            else:
                kw["byweekday"] = RR_DAYS[self.repeat_weekday or "mon"](int(self.repeat_week or "1"))
        if self.repeat_type == "until" and self.repeat_until:
            kw["until"] = fields.Datetime.to_datetime(self.repeat_until)
        elif self.repeat_type == "after":
            kw["count"] = max(1, self.repeat_number)
        return rrule.rrule(RR_FREQ[self.repeat_unit], **kw)

    def _dates(self, start, end):
        """Dates des occurrences entre deux dates incluses."""
        self.ensure_one()
        rule = self._rrule()
        return [d.date() for d in rule.between(fields.Datetime.to_datetime(start), fields.Datetime.to_datetime(end), inc=True)]

    @api.depends("repeat_interval", "repeat_unit", "mon", "tue", "wed", "thu", "fri", "sat", "sun", "repeat_on_month",
                 "repeat_day", "repeat_week", "repeat_weekday", "repeat_type", "repeat_until", "repeat_number", "date_start")
    def _compute_resume(self):
        today = fields.Date.context_today(self)
        units = {"day": ("jour", "jours"), "week": ("semaine", "semaines"), "month": ("mois", "mois"), "year": ("an", "ans")}
        jours = dict(JOURS)
        for rec in self:
            if not rec.date_start or not rec.repeat_unit:
                rec.resume, rec.prochaine = False, False
                continue
            n = max(1, rec.repeat_interval)
            one, many = units[rec.repeat_unit]
            if n == 1:
                txt = {"day": "Chaque jour", "week": "Chaque semaine", "month": "Chaque mois", "year": "Chaque année"}[rec.repeat_unit]
            else:
                txt = "Tous les %s %s" % (n, many) if rec.repeat_unit != "week" else "Toutes les %s semaines" % n
            if rec.repeat_unit == "week":
                days = rec._weekdays() or [JOURS[rec.date_start.weekday()][0]]
                txt += " : " + ", ".join(jours[d].lower() for d in days)
            elif rec.repeat_unit == "month":
                if rec.repeat_on_month == "date":
                    txt += ", le %s" % rec.repeat_day
                else:
                    txt += ", le %s %s" % (dict(RANGS)[rec.repeat_week or "1"].lower(), jours[rec.repeat_weekday or "mon"].lower())
            elif rec.repeat_unit == "year":
                txt += ", le %s" % rec.date_start.strftime("%d/%m")
            if rec.repeat_type == "until" and rec.repeat_until:
                txt += " · jusqu'au %s" % rec.repeat_until.strftime("%d/%m/%Y")
            elif rec.repeat_type == "after":
                txt += " · %s fois" % rec.repeat_number
            rec.resume = txt
            try:
                nxt = rec._rrule().after(fields.Datetime.to_datetime(max(today, rec.date_start)), inc=True)
            except Exception:
                nxt = False
            rec.prochaine = nxt.date() if nxt else False

    # ------------------------------------------------------------ génération des occurrences
    def _occurrence_vals(self, day):
        self.ensure_one()
        return {
            "titre": self.name, "type_travail": self.type_travail, "equipe_id": self.equipe_id.id,
            "date_prevue": day, "duree_prevue": self.duree_prevue, "adresse": self.adresse or False,
            "lieu_id": self.lieu_id.id or False, "objet_ids": [(6, 0, self.objet_ids.ids)],
            "consigne": self.consigne or False, "liste": self.liste or False,
            "state": "planif", "source": "planning", "recurrence_id": self.id,
        }

    def _generate(self, reset=False):
        """Crée les occurrences manquantes jusqu'à l'horizon. ``reset`` : refait celles à venir non commencées."""
        Demande = self.env["travaux.demande"].sudo().with_context(
            travaux_no_live=True, tracking_disable=True, mail_create_nolog=True, travaux_no_photo_post=True)
        today = fields.Date.context_today(self)
        end = today + timedelta(days=HORIZON_JOURS)
        created = 0
        for rec in self:
            future = Demande.search([("recurrence_id", "=", rec.id), ("date_prevue", ">=", today)])
            if reset or not rec.active:
                untouched = future.filtered(lambda d: d.state == "planif" and not d.date_debut and not d.pointage_ids)
                untouched.unlink()
                future -= untouched
            if not rec.active:
                continue
            have = set(future.mapped("date_prevue"))
            vals = [rec._occurrence_vals(day) for day in rec._dates(max(today, rec.date_start), end) if day not in have]
            if vals:
                Demande.create(vals)
                created += len(vals)
        return created

    @api.model
    def _cron_generate(self):
        self.search([])._generate()
        return True

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._generate()
        return recs

    def write(self, vals):
        res = super().write(vals)
        self._generate(reset=True)
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "demande", "ids": []})
        return res

    def unlink(self):
        self.write({"active": False})  # retire les occurrences à venir, garde l'historique
        return super().unlink()

    def action_voir_occurrences(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Occurrences – %s", self.name),
                "res_model": "travaux.demande", "view_mode": "list,form,calendar",
                "domain": [("recurrence_id", "=", self.id)]}
