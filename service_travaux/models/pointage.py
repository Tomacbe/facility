from odoo import api, fields, models


class TravauxPointage(models.Model):
    """Heures imputées par une équipe à un travail (jamais par personne)."""
    _name = "travaux.pointage"
    _description = "Heures d'équipe imputées à un travail"
    _order = "date desc, id desc"

    demande_id = fields.Many2one("travaux.demande", "Travail", required=True, ondelete="cascade", index=True)
    equipe_id = fields.Many2one("travaux.equipe", "Équipe", required=True, index=True)
    date = fields.Date("Jour", required=True, default=fields.Date.context_today, index=True)
    heures = fields.Float("Heures", required=True)
    origine = fields.Selection([("mesure", "Mesurée (Démarrer / Terminé)"), ("manuel", "Imputée à la main")],
                               default="manuel", required=True)
    note = fields.Char("Remarque")
    type_travail = fields.Selection(related="demande_id.type_travail", store=True)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.mapped("demande_id")._travaux_live_touch()
        return recs

    def write(self, vals):
        res = super().write(vals)
        self.mapped("demande_id")._travaux_live_touch()
        return res

    def unlink(self):
        demandes = self.mapped("demande_id")
        res = super().unlink()
        demandes._travaux_live_touch()
        return res
