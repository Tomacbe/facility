from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TravauxEquipe(models.Model):
    _name = "travaux.equipe"
    _description = "Équipe du service travaux"
    _order = "is_pool, sequence, name"

    name = fields.Char("Équipe", required=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer("Couleur")
    superviseur_id = fields.Many2one("res.users", "Superviseur", default=lambda self: self.env.user)
    brigadier_id = fields.Many2one(
        "res.users", "Brigadier (binôme)",
        help="Binôme du superviseur (contremaître) : il voit et organise le travail de l'équipe comme lui.")
    chef_id = fields.Many2one("hr.employee", "Chef d'équipe")
    department_id = fields.Many2one(
        "hr.department", "Service", index=True,
        help="Place de l'équipe dans l'organigramme. Le responsable du service (et ceux des services au-dessus) "
             "voit et organise le travail de l'équipe, comme son superviseur.")
    responsable_ids = fields.Many2many(
        "res.users", "travaux_equipe_responsable_rel", "equipe_id", "user_id", string="Responsables",
        compute="_compute_responsables", store=True,
        help="Le superviseur de l'équipe et les responsables des services dont elle dépend.")
    effectif_min = fields.Integer("Effectif minimum", default=1)
    capacite_hebdo = fields.Float(
        "Capacité (h / semaine)", default=76,
        help="Heures disponibles par semaine, pour calculer la charge de l'équipe.")
    is_pool = fields.Boolean(
        "Agents sans équipe", help="Réserve d'agents polyvalents à prêter ou à affecter.")
    active = fields.Boolean(default=True)

    member_ids = fields.One2many("hr.employee", "equipe_id", "Membres (équipe d'origine)")
    jour_ids = fields.One2many("hr.employee", "equipe_jour_id", "Agents aujourd'hui")
    demande_ids = fields.One2many("travaux.demande", "equipe_id", "Demandes et tâches")

    nb_membres = fields.Integer(compute="_compute_presence")
    nb_presents = fields.Integer("Présents", compute="_compute_presence")
    presence_label = fields.Char("Présence", compute="_compute_presence")
    sous_effectif = fields.Boolean("Sous l'effectif minimum", compute="_compute_presence")
    alerte = fields.Char(compute="_compute_presence")
    suggestion_id = fields.Many2one("hr.employee", "Renfort suggéré", compute="_compute_presence")

    nb_ouvertes = fields.Integer("Tâches ouvertes", compute="_compute_charge")
    heures_12m = fields.Float("Heures mesurées (12 mois)", compute="_compute_charge")
    capacite_12m = fields.Float("Capacité (12 mois)", compute="_compute_charge")
    charge_pct = fields.Float("Charge (%)", compute="_compute_charge")

    # ------------------------------------------------------------------ organigramme
    @api.depends("superviseur_id", "brigadier_id", "department_id", "department_id.parent_path", "department_id.manager_id.user_id")
    def _compute_responsables(self):
        Dept = self.env["hr.department"].sudo()
        for team in self:
            users = team.superviseur_id | team.brigadier_id
            path = team.department_id.sudo().parent_path
            if path:
                depts = Dept.browse(int(i) for i in path.split("/") if i)
                users |= depts.mapped("manager_id.user_id")
            team.responsable_ids = users

    def _dept_ids(self):
        """Le service de l'équipe et tous ceux au-dessus."""
        self.ensure_one()
        path = self.department_id.sudo().parent_path
        return [int(i) for i in path.split("/") if i] if path else []

    # ------------------------------------------------------------------ présence
    @api.depends("member_ids.statut_jour", "jour_ids.statut_jour", "effectif_min")
    def _compute_presence(self):
        Employee = self.env["hr.employee"]
        for team in self:
            here = team.jour_ids.filtered(lambda e: e.statut_jour == "present")
            team.nb_membres = len(team.member_ids)
            team.nb_presents = len(here)
            if team.is_pool:
                team.presence_label = _("%s disponible(s)", len(here))
            else:
                team.presence_label = _("%(p)s présent(s) sur %(t)s",
                                        p=len(here), t=max(len(team.member_ids), len(here)))
            low = bool(not team.is_pool and team.effectif_min and len(here) < team.effectif_min)
            team.sous_effectif = low
            team.suggestion_id = False
            team.alerte = False
            if low and team.id:
                donor = team._find_renfort()
                team.suggestion_id = donor
                txt = _("%(t)s : %(p)s présent(s) pour un minimum de %(m)s.",
                        t=team.name, p=len(here), m=team.effectif_min)
                if donor:
                    origin = donor.equipe_id
                    txt += " " + _("Suggestion : %(n)s (%(o)s) est disponible.",
                                   n=donor.name, o=origin.name if origin and not origin.is_pool else _("sans équipe"))
                team.alerte = txt

    def _find_renfort(self):
        """Même logique que la maquette : d'abord un agent sans équipe, sinon un agent
        d'une équipe qui reste au-dessus de son minimum. Jamais un chef d'équipe."""
        self.ensure_one()
        candidates = self.env["hr.employee"].search([
            ("statut_jour", "=", "present"),
            ("equipe_pret_id", "=", False),
            ("equipe_id", "!=", self.id),
            ("equipe_id", "!=", False),
        ])
        candidates = candidates.filtered(lambda e: not (e.fonction or "").lower().startswith("chef"))

        def ok(emp):
            home = emp.equipe_id
            if not home or home.is_pool:
                return True
            here = len(home.jour_ids.filtered(lambda e: e.statut_jour == "present"))
            return here - 1 >= home.effectif_min

        pool = candidates.filtered(lambda e: e.equipe_id.is_pool)
        others = (candidates - pool).filtered(ok)
        return (pool or others)[:1]

    def action_preter_suggestion(self):
        for team in self:
            donor = team._find_renfort()
            if not donor:
                raise UserError(_("Aucun agent disponible à prêter pour %s.", team.name))
            donor.sudo().equipe_jour_id = team
        return True

    @api.depends("name")
    @api.depends_context("show_presence")
    def _compute_display_name(self):
        super()._compute_display_name()
        if self.env.context.get("show_presence"):
            for t in self:
                t.display_name = "%s · %s" % (t.name, t.presence_label)

    # ------------------------------------------------------------------ charge
    def _compute_charge(self):
        since = fields.Datetime.now() - timedelta(days=365)
        Demande = self.env["travaux.demande"]
        hours = {team.id: total for team, total in Demande._read_group(
            [("equipe_id", "in", self.ids), ("date_fin", ">=", since)], ["equipe_id"], ["duree_mesuree:sum"])}
        opened = {team.id: n for team, n in Demande._read_group(
            [("equipe_id", "in", self.ids), ("state", "in", ("planif", "cours", "valider"))],
            ["equipe_id"], ["__count"])}
        for team in self:
            team.heures_12m = hours.get(team.id, 0.0)
            team.capacite_12m = team.capacite_hebdo * 52 * 0.8  # congés, formations, météo
            team.charge_pct = 100.0 * team.heures_12m / team.capacite_12m if team.capacite_12m else 0.0
            team.nb_ouvertes = opened.get(team.id, 0)

    def action_voir_demandes(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("service_travaux.action_toutes")
        action["domain"] = [("equipe_id", "=", self.id)]
        action["context"] = {}
        return action
