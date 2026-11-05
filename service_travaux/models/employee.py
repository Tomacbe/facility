from odoo import _, api, fields, models

STATUTS = [
    ("present", "Présent"),
    ("conge", "Congé"),
    ("formation", "Formation"),
    ("recup", "Récupération"),
    ("absent", "Absent"),
]


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    # Odoo 19 réserve le poste (« job_title ») au service du personnel : FACILITY garde sa propre fonction,
    # lisible par les équipes (Chef d'équipe, Ouvrier, Chauffeur…).
    fonction = fields.Char("Fonction dans l'équipe")

    equipe_id = fields.Many2one("travaux.equipe", "Équipe d'origine", tracking=True)
    equipe_pret_id = fields.Many2one(
        "travaux.equipe", "Prêté aujourd'hui à",
        help="Prêt pour la journée uniquement : effacé automatiquement le lendemain.")
    equipe_jour_id = fields.Many2one(
        "travaux.equipe", "Équipe du jour",
        compute="_compute_equipe_jour", inverse="_inverse_equipe_jour", store=True,
        group_expand="_read_group_equipe_jour", index=True)
    statut_jour = fields.Selection(
        STATUTS, "Statut du jour", default="present", required=True,
        help="Les absences peuvent être reprises du logiciel du personnel ; "
             "seul le statut est affiché, jamais le motif.")
    present_jour = fields.Boolean("Présent aujourd'hui", compute="_compute_present", store=True)
    superviseur_travaux_id = fields.Many2one(
        "res.users", "Superviseur", related="equipe_jour_id.superviseur_id", store=True)
    info_pret = fields.Char("Prêt", compute="_compute_info_pret")

    @api.depends("equipe_id", "equipe_pret_id")
    def _compute_equipe_jour(self):
        for emp in self:
            emp.equipe_jour_id = emp.equipe_pret_id or emp.equipe_id

    def _inverse_equipe_jour(self):
        # Glisser un agent dans une autre colonne = le prêter pour la journée.
        for emp in self:
            target = emp.equipe_jour_id
            if not emp.equipe_id:
                emp.equipe_id = target
                emp.equipe_pret_id = False
            elif target == emp.equipe_id:
                emp.equipe_pret_id = False
            else:
                emp.equipe_pret_id = target
                if emp.statut_jour != "present":
                    emp.statut_jour = "present"

    @api.model
    def _read_group_equipe_jour(self, equipes, domain):
        return equipes.search([])

    @api.depends("statut_jour")
    def _compute_present(self):
        for emp in self:
            emp.present_jour = emp.statut_jour == "present"

    @api.depends("equipe_pret_id", "equipe_id")
    def _compute_info_pret(self):
        for emp in self:
            if emp.equipe_pret_id:
                origin = emp.equipe_id
                emp.info_pret = _("Renfort de %s", origin.name) if origin and not origin.is_pool else _("Renfort (sans équipe)")
            else:
                emp.info_pret = False

    # Boutons de la carte « Équipes du jour »
    def _set_statut(self, statut):
        vals = {"statut_jour": statut}
        if statut != "present":
            vals["equipe_pret_id"] = False
        self.write(vals)
        return True

    def action_present(self):
        return self._set_statut("present")

    def action_conge(self):
        return self._set_statut("conge")

    def action_formation(self):
        return self._set_statut("formation")

    def action_recup(self):
        return self._set_statut("recup")

    def action_rendre(self):
        self.write({"equipe_pret_id": False})
        return True

    @api.model
    def _cron_reset_journee(self):
        """Chaque nuit : les prêts et statuts du jour s'effacent."""
        self.search(["|", ("equipe_pret_id", "!=", False), ("statut_jour", "!=", "present")]).write(
            {"equipe_pret_id": False, "statut_jour": "present"})
