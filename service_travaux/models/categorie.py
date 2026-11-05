from odoo import fields, models


class TravauxCategorie(models.Model):
    _name = "travaux.categorie"
    _description = "Catégorie de demande"
    _order = "sequence, name"

    name = fields.Char("Catégorie", required=True, translate=True)
    sequence = fields.Integer(default=10)
    equipe_defaut_id = fields.Many2one("travaux.equipe", "Équipe habituelle")
    hors_competence = fields.Boolean(
        "Hors compétence", help="Demande à transférer à un autre service (ex. litige privé)."
    )
    service_competent = fields.Char("Service compétent")
    active = fields.Boolean(default=True)
