from odoo import api, fields, models

from .objet import OUVERTS


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    objet_recense_id = fields.Many2one("travaux.objet", "Objet précis", index=True, ondelete="set null",
                                       help="L'objet réel concerné (cet avaloir-là), en plus de son type #.")

    def _objet_vals(self, objet, vals):
        """Complète la demande avec ce que l'objet sait : type #, # lieu, position, rue."""
        current = self[:1]
        out = {}
        if objet.type_id and objet.type_id not in (current.objet_ids if current else self.env["travaux.lieu"]):
            out["objet_ids"] = [(4, objet.type_id.id)]
        if objet.lieu_id and not vals.get("lieu_id") and not (current and current.lieu_id):
            out["lieu_id"] = objet.lieu_id.id
        if not vals.get("latitude") and not (current and current.latitude):
            out.update(latitude=objet.latitude, longitude=objet.longitude)
        if not vals.get("adresse") and not (current and current.adresse) and objet.rue:
            out["adresse"] = objet.rue
        return out

    @api.model_create_multi
    def create(self, vals_list):
        Objet = self.env["travaux.objet"]
        for vals in vals_list:
            if vals.get("objet_recense_id"):
                objet = Objet.browse(vals["objet_recense_id"])
                extra = self.browse()._objet_vals(objet, vals)
                if "objet_ids" in extra:
                    vals["objet_ids"] = list(vals.get("objet_ids") or []) + extra.pop("objet_ids")
                for k, v in extra.items():
                    vals.setdefault(k, v)
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if vals.get("objet_recense_id"):
            objet = self.env["travaux.objet"].browse(vals["objet_recense_id"])
            for rec in self:
                extra = rec._objet_vals(objet, vals)
                if extra:
                    super(TravauxDemande, rec).write(extra)
        return res

    # ------------------------------------------------------------ app terrain
    def _tz_zones(self):
        out = super()._tz_zones()
        zones = self.env["travaux.circuit.zone"].sudo().browse([z["id"] for z in out])
        geoms = {z.id: z.lieu_id.geojson for z in zones if z.lieu_id.has_contour}
        for z in out:
            z["geojson"] = geoms.get(z["id"]) or False
        return out

    def _carte_circuit(self):
        """Ce que la carte de l'app terrain montre pour ce bon : zones et objets de la tournée."""
        self.ensure_one()
        circuit = self.circuit_id.sudo()
        if not circuit:
            return False
        objets = self.env["travaux.objet"].sudo().search([("circuit_id", "=", circuit.id)])
        lat = [o.latitude for o in objets] + [z.lieu_id.latitude for z in circuit.zone_ids if z.lieu_id.latitude]
        lng = [o.longitude for o in objets] + [z.lieu_id.longitude for z in circuit.zone_ids if z.lieu_id.longitude]
        autour = self.env["travaux.objet"].sudo().browse()
        if lat:
            pad = 0.0015
            autour = self.env["travaux.objet"].sudo().search([
                ("circuit_id", "!=", circuit.id), ("latitude", ">", min(lat) - pad), ("latitude", "<", max(lat) + pad),
                ("longitude", ">", min(lng) - pad), ("longitude", "<", max(lng) + pad)], limit=500)
        return {
            "mode": circuit.mode,
            "objets": [o._carte_point() for o in objets],
            "autour": [o._carte_point() for o in autour],
        }

    @api.model
    def tv_conversation(self, rec_id):
        res = super().tv_conversation(rec_id)
        rec = self.browse(rec_id)
        if rec.circuit_id:
            res["carte"] = rec._carte_circuit()
        return res

    @api.model
    def tv_objet_signaler(self, objet_id, titre, description=False):
        """Signalement depuis la carte de l'app terrain : la demande part avec l'objet exact."""
        objet = self.env["travaux.objet"].browse(int(objet_id)).exists()
        if not objet:
            return False
        rec_id = self.tv_signaler({"titre": titre, "description": description or False,
                                   "adresse": objet.rue or False, "lat": objet.latitude, "lng": objet.longitude})
        self.browse(rec_id).sudo().write({"objet_recense_id": objet.id})
        return rec_id

    @api.model
    def tv_objet_infos(self, objet_id):
        objet = self.env["travaux.objet"].browse(int(objet_id)).exists()
        if not objet:
            return False
        d = objet._carte_point()
        d.update(rue=objet.rue or "", lieu=("#" + objet.lieu_id.name) if objet.lieu_id else "",
                 ouvertes=[{"titre": x.titre, "ref": x.name} for x in objet.demande_ids if x.state in OUVERTS][:3],
                 choix=objet._choix_signalement())
        return d


class TravauxObjetChoix(models.Model):
    _inherit = "travaux.objet"

    def _choix_signalement(self):
        """Titres proposés au signalement, d'après les demandes déjà faites sur ce type d'objet."""
        self.ensure_one()
        titres = self.env["travaux.demande"].sudo().search(
            [("objet_ids", "in", self.type_id.ids)], order="create_date desc", limit=40).mapped("titre")
        seen, out = set(), []
        for t in titres:
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        defaults = {"avaloir": ["Avaloir bouché", "Grille cassée"], "poubelle": ["Corbeille débordante", "Corbeille cassée"],
                    "poteau": ["Poteau penché", "Poteau endommagé"], "chambredevisite": ["Taque descellée", "Odeurs"]}
        for t in defaults.get(self.type_id.cle or "", ["À vérifier"]):
            if t.lower() not in seen:
                out.append(t)
        return out[:3]
