import math
import re
import unicodedata
from difflib import SequenceMatcher

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

RAYON_PROCHE = 300   # m : # suggérés « aux alentours »
RAYON_DOUBLON = 60   # m : un # aussi près est sans doute le même endroit


def norm_adresse(text):
    """Forme comparable d'une adresse : sans accents, ponctuation, majuscules ni « 1330 Rixensart »."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\b1330\b|\brixensart\b|\bbelgique\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\b(n|no|num)\b", " ", t)
    return " ".join(t.split())


def tag_key(text):
    """Clé d'unicité d'un # : ni accents, ni majuscules, ni espaces (« #école du Centre » = « #EcoleDuCentre »)."""
    return norm_adresse(text).replace(" ", "")


def tag_format(text):
    """Écriture uniforme d'un # : « ecole du centre » → « EcoleDuCentre »."""
    words = re.findall(r"[^\W_]+", (text or "").replace("#", " "), re.UNICODE)
    return "".join(w[0].upper() + w[1:] for w in words)[:48]


def distance_m(lat1, lng1, lat2, lng2):
    if not (lat1 and lng1 and lat2 and lng2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


class TravauxLieu(models.Model):
    _name = "travaux.lieu"
    _description = "# Lieu (étiquette d'un endroit connu)"
    _order = "name"

    kind = fields.Selection([("lieu", "Lieu"), ("objet", "Objet")], "Type de #", default="lieu", required=True,
                            help="Lieu : un endroit (#ÉcoleDuCentre, #LeVerger). "
                                 "Objet : ce qui est concerné (#Banc, #Poubelle, #Chaufferie).")
    name = fields.Char("#", required=True,
                       help="Nom connu de tous, écrit sans espace : ÉcoleDuCentre, MaisonCommunale…")
    cle = fields.Char("Clé", compute="_compute_cle", store=True, index=True)
    adresse = fields.Char("Adresse", help="Toute demande encodée à cette adresse reçoit automatiquement ce #.")
    latitude = fields.Float("Latitude", digits=(10, 7))
    longitude = fields.Float("Longitude", digits=(10, 7))
    note = fields.Char("Remarque", help="Ex. accès par la grille arrière, clé chez le concierge.")
    state = fields.Selection([("propose", "Proposé · à confirmer"), ("valide", "Figé")], "Statut",
                             default="valide", required=True)
    propose_par_id = fields.Many2one("res.users", "Proposé par", readonly=True)
    demande_ids = fields.One2many("travaux.demande", "lieu_id", "Travaux à ce lieu")
    demande_objet_ids = fields.Many2many("travaux.demande", "travaux_demande_objet_rel", "objet_id", "demande_id",
                                         string="Travaux sur cet objet")
    nb_demandes = fields.Integer("Travaux", compute="_compute_nb")
    active = fields.Boolean(default=True)

    _cle_uniq = models.Constraint("unique(cle)", "Ce # existe déjà (même nom, aux accents et majuscules près).")

    @api.depends("name")
    def _compute_cle(self):
        for rec in self:
            rec.cle = tag_key(rec.name)

    def _compute_nb(self):
        counts = {lieu.id: n for lieu, n in self.env["travaux.demande"]._read_group(
            [("lieu_id", "in", self.ids)], ["lieu_id"], ["__count"])}
        for rec in self:
            rec.nb_demandes = len(rec.demande_objet_ids) if rec.kind == "objet" else counts.get(rec.id, 0)

    @api.depends("name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "#%s" % (rec.name or "")

    @api.model
    def _search_display_name(self, operator, value):
        if isinstance(value, str) and value and operator in ("ilike", "like", "=ilike", "="):
            return ["|", ("cle", "ilike", tag_key(value)), ("adresse", "ilike", value)]
        return super()._search_display_name(operator, value)

    # ------------------------------------------------------------ recherche
    @api.model
    def _by_name(self, name):
        key = tag_key(name)
        return self.with_context(active_test=False).search([("cle", "=", key)], limit=1) if key else self.browse()

    @api.model
    def _match(self, adresse):
        """# figé correspondant à une adresse (ou à un nom de lieu tapé dans le champ adresse)."""
        key = norm_adresse(adresse)
        if not key:
            return self.browse()
        for lieu in self.search([("state", "=", "valide"), ("kind", "=", "lieu")]):
            if key == norm_adresse(lieu.adresse) or key.replace(" ", "") == lieu.cle:
                return lieu
        return self.browse()

    @api.model
    def _similar(self, name, lat=0.0, lng=0.0, kind="lieu"):
        """# existants qui ressemblent au nom donné ou qui sont au même endroit : évite les doublons."""
        key = tag_key(name)
        out = []
        for lieu in self.search([("kind", "=", kind)]):
            dist = distance_m(lat, lng, lieu.latitude, lieu.longitude) if kind == "lieu" else None
            close = dist is not None and dist <= RAYON_DOUBLON
            alike = key and lieu.cle and (
                key in lieu.cle or lieu.cle in key or SequenceMatcher(None, key, lieu.cle).ratio() >= 0.78)
            if close or alike:
                out.append({"id": lieu.id, "name": lieu.name, "adresse": lieu.adresse or "",
                            "dist": round(dist) if dist is not None else False})
        return sorted(out, key=lambda x: (x["dist"] is False, x["dist"] or 0))[:4]

    def _tag_existing(self):
        """Rattache les travaux déjà encodés à la même adresse et sans #  (uniquement pour un # figé)."""
        Demande = self.env["travaux.demande"].sudo().with_context(travaux_no_live=True, tracking_disable=True)
        total = 0
        for lieu in self.filtered(lambda l: l.state == "valide" and l.kind == "lieu"):
            adr = norm_adresse(lieu.adresse)
            todo = Demande.search([("lieu_id", "=", False), ("adresse", "!=", False)]).filtered(
                lambda d: norm_adresse(d.adresse) == adr or norm_adresse(d.adresse).replace(" ", "") == lieu.cle)
            if todo:
                todo.write({"lieu_id": lieu.id})
                total += len(todo)
                # adresse tapée sous la forme du nom du lieu : on met la vraie adresse
                by_name = todo.filtered(lambda d: lieu.adresse and norm_adresse(d.adresse).replace(" ", "") == lieu.cle)
                if by_name:
                    by_name.write({"adresse": lieu.adresse})
        return total

    # ------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["name"] = tag_format(vals.get("name"))
            if not vals["name"]:
                raise UserError(_("Donnez un nom au #."))
        recs = super().create(vals_list)
        recs._tag_existing()
        return recs

    def write(self, vals):
        if "name" in vals:
            vals = dict(vals, name=tag_format(vals["name"]))
            if not vals["name"]:
                raise UserError(_("Donnez un nom au #."))
        res = super().write(vals)
        if {"adresse", "name", "state"} & set(vals):
            self._tag_existing()
        return res

    # ------------------------------------------------------------ gestion (superviseurs uniquement)
    def _check_sup(self):
        if not self.env.user.has_group("service_travaux.group_superviseur"):
            raise AccessError(_("Seul un superviseur peut figer, grouper, renommer ou supprimer un #."))

    def action_figer(self):
        self._check_sup()
        self.write({"state": "valide"})
        return True

    def action_fusionner(self, cible):
        """Groupe ces # dans « cible » : tous leurs travaux suivent, les doublons disparaissent."""
        self._check_sup()
        sources = self - cible
        if not cible or not sources:
            raise UserError(_("Choisissez le # à garder."))
        if any(s.kind != cible.kind for s in sources):
            raise UserError(_("On ne groupe pas un lieu avec un objet."))
        for d in sources.mapped("demande_objet_ids").sudo().with_context(travaux_no_live=True, tracking_disable=True):
            d.write({"objet_ids": [(3, i) for i in sources.ids] + [(4, cible.id)]})
        self.env["travaux.demande"].sudo().with_context(travaux_no_live=True, tracking_disable=True).search(
            [("lieu_id", "in", sources.ids)]).write({"lieu_id": cible.id})
        if not cible.adresse:
            cible.adresse = sources[:1].adresse
        if not cible.latitude:
            cible.write({"latitude": sources[:1].latitude, "longitude": sources[:1].longitude})
        sources.unlink()
        return True

    def action_voir_travaux(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Travaux – #%s", self.name),
            "res_model": "travaux.demande", "view_mode": "list,form",
            "domain": [("objet_ids", "in", self.id)] if self.kind == "objet" else [("lieu_id", "=", self.id)],
        }


class TravauxLieuFusion(models.TransientModel):
    _name = "travaux.lieu.fusion"
    _description = "Grouper des # en doublon"

    lieu_ids = fields.Many2many("travaux.lieu", string="# à grouper")
    cible_id = fields.Many2one("travaux.lieu", "# à garder", required=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ids = self.env.context.get("active_ids") or []
        if self.env.context.get("active_model") == "travaux.lieu" and ids:
            res["lieu_ids"] = [(6, 0, ids)]
            lieux = self.env["travaux.lieu"].browse(ids)
            res["cible_id"] = (lieux.filtered(lambda l: l.state == "valide")[:1] or lieux[:1]).id
        return res

    def action_grouper(self):
        self.ensure_one()
        self.lieu_ids.action_fusionner(self.cible_id)
        return {"type": "ir.actions.act_window_close"}

