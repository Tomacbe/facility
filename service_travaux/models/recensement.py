from odoo import fields, models, tools


class TravauxRecensement(models.Model):
    """Jeu de données construit depuis le terrain : une ligne par objet tagué (#Banc, #Poubelle…) sur une demande."""
    _name = "travaux.recensement"
    _description = "Jeu de données des # objets"
    _auto = False
    _order = "date desc"
    _rec_name = "objet_id"

    objet_id = fields.Many2one("travaux.lieu", "# Objet", readonly=True)
    lieu_id = fields.Many2one("travaux.lieu", "# Lieu", readonly=True)
    demande_id = fields.Many2one("travaux.demande", "Demande", readonly=True)
    titre = fields.Char("Objet de la demande", readonly=True)
    adresse = fields.Char("Adresse", readonly=True)
    latitude = fields.Float("Latitude", digits=(10, 7), readonly=True, aggregator=None)
    longitude = fields.Float("Longitude", digits=(10, 7), readonly=True, aggregator=None)
    date = fields.Datetime("Signalé le", readonly=True)
    equipe_id = fields.Many2one("travaux.equipe", "Équipe", readonly=True)
    state = fields.Selection(lambda self: self.env["travaux.demande"]._fields["state"].selection, "Statut", readonly=True)
    heures = fields.Float("Heures", readonly=True)
    photo = fields.Binary("Photo", related="demande_id.photo_avant")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW travaux_recensement AS
            SELECT (r.demande_id::bigint * 100000 + r.objet_id) AS id, r.objet_id, d.lieu_id, d.id AS demande_id,
                   d.titre, d.adresse, d.latitude, d.longitude, d.create_date AS date, d.equipe_id, d.state,
                   d.duree_mesuree AS heures
              FROM travaux_demande_objet_rel r
              JOIN travaux_demande d ON d.id = r.demande_id
        """)

    def action_ouvrir(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "travaux.demande", "res_id": self.demande_id.id,
                "views": [(False, "form")]}
