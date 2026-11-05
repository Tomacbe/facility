from datetime import timedelta

from odoo import _, api, fields, models

HEURES_JOUR = 7.6  # 38 h / 5 jours


class TravauxVue(models.Model):
    """Vue personnalisée du suivi : un regroupement d'équipes."""
    _name = "travaux.vue"
    _description = "Vue personnalisée (groupe d'équipes)"
    _order = "sequence, name"

    name = fields.Char("Nom de la vue", required=True)
    sequence = fields.Integer(default=10)
    user_id = fields.Many2one("res.users", "Créée par", default=lambda self: self.env.user, required=True)
    equipe_ids = fields.Many2many("travaux.equipe", string="Équipes")
    partagee = fields.Boolean("Partagée avec tous")


class TravauxPresence(models.Model):
    """Présences d'une équipe pour une journée (instantané), pour contrôler
    que le temps déclaré est cohérent avec les heures de présence."""
    _name = "travaux.presence"
    _description = "Présences d'une équipe (jour)"
    _order = "date desc, equipe_id"

    date = fields.Date(required=True, index=True)
    equipe_id = fields.Many2one("travaux.equipe", required=True, index=True, ondelete="cascade")
    nb_membres = fields.Integer("Membres")
    nb_presents = fields.Integer("Présents")
    heures_dispo = fields.Float("Heures de présence")
    # absences du jour, par statut (jamais par personne ni par motif médical)
    nb_conge = fields.Integer("En congé")
    nb_formation = fields.Integer("En formation")
    nb_recup = fields.Integer("En récupération")
    nb_absent = fields.Integer("Absents")
    effectif_min = fields.Integer("Effectif minimum ce jour-là")

    _uniq_jour = models.Constraint("unique(date, equipe_id)", "Une seule ligne par équipe et par jour.")

    @api.model
    def snapshot(self, day=None, teams=None):
        """Enregistre (ou met à jour) les présences du jour pour chaque équipe."""
        day = day or fields.Date.context_today(self)
        if day.weekday() >= 5:
            return True
        teams = teams or self.env["travaux.equipe"].search([("is_pool", "=", False)])
        for team in teams.filtered(lambda t: not t.is_pool):
            presents = len(team.jour_ids.filtered(lambda e: e.statut_jour == "present"))
            own = team.member_ids
            vals = {"nb_membres": len(own), "nb_presents": presents,
                    "heures_dispo": round(presents * HEURES_JOUR, 2), "effectif_min": team.effectif_min,
                    "nb_conge": len(own.filtered(lambda e: e.statut_jour == "conge")),
                    "nb_formation": len(own.filtered(lambda e: e.statut_jour == "formation")),
                    "nb_recup": len(own.filtered(lambda e: e.statut_jour == "recup")),
                    "nb_absent": len(own.filtered(lambda e: e.statut_jour == "absent"))}
            line = self.sudo().search([("date", "=", day), ("equipe_id", "=", team.id)], limit=1)
            if line:
                line.write(vals)
            else:
                self.sudo().create(dict(vals, date=day, equipe_id=team.id))
        return True


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def write(self, vals):
        teams_before = self.mapped("equipe_jour_id")
        res = super().write(vals)
        if set(vals) & {"statut_jour", "equipe_pret_id", "equipe_id"} and not self.env.context.get("travaux_no_snapshot"):
            teams = (teams_before | self.mapped("equipe_jour_id")).filtered(lambda t: not t.is_pool)
            if teams:
                self.env["travaux.presence"].snapshot(teams=teams)
        return res

    @api.model
    def _cron_reset_journee(self):
        # d'abord figer les présences de la veille, puis remettre tout le monde « présent »
        yesterday = fields.Date.context_today(self) - timedelta(days=1)
        self.env["travaux.presence"].snapshot(day=yesterday)
        return super(HrEmployee, self.with_context(travaux_no_snapshot=True))._cron_reset_journee()
