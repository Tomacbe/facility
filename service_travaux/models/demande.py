import base64

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .lieu import tag_key

STATES = [
    ("recue", "À attribuer"),
    ("precision", "Précision demandée"),
    ("transferee", "Transférée"),
    ("planif", "Planifiée"),
    ("cours", "En cours"),
    ("valider", "Terminée · à valider"),
    ("close", "Clôturée"),
    ("annulee", "Annulée"),
]
KANBAN_STATES = ["planif", "cours", "valider", "close"]

SEQUENCES = {
    "demande": "travaux.demande",
    "tournee": "travaux.tournee",
    "maintenance": "travaux.maintenance",
}


class TravauxDemande(models.Model):
    _name = "travaux.demande"
    _description = "Demande de travaux / tâche"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priorite desc, date_prevue, id desc"
    _rec_name = "display_title"

    name = fields.Char("Référence", default="/", copy=False, readonly=True, index=True)
    titre = fields.Char("Objet", required=True, tracking=True)
    display_title = fields.Char(compute="_compute_display_title", store=True)
    type_travail = fields.Selection(
        [("demande", "Demande"), ("tournee", "Tournée récurrente"), ("maintenance", "Maintenance planifiée")],
        "Type", default="demande", required=True, tracking=True)
    description = fields.Text("Message")
    adresse = fields.Char("Lieu", tracking=True)
    latitude = fields.Float("Latitude", digits=(10, 7))
    longitude = fields.Float("Longitude", digits=(10, 7))
    source = fields.Selection([
        ("guichet", "Guichet"),
        ("telephone", "Téléphone"),
        ("email", "E-mail"),
        ("courrier", "Courrier"),
        ("citoyen", "Citoyen (en ligne)"),
        ("police", "Police locale"),
        ("service", "Service communal"),
        ("app", "App terrain"),
        ("planning", "Planning"),
    ], "Origine", default="guichet")
    demandeur_id = fields.Many2one("res.partner", "Demandeur", tracking=True)
    demandeur_mobile = fields.Char("GSM du demandeur", related="demandeur_id.phone", readonly=False)

    categorie_id = fields.Many2one("travaux.categorie", "Catégorie", tracking=True)
    hors_competence = fields.Boolean(related="categorie_id.hors_competence")
    equipe_suggeree_id = fields.Many2one(related="categorie_id.equipe_defaut_id", string="Équipe habituelle")
    equipe_id = fields.Many2one("travaux.equipe", "Équipe", tracking=True, index=True,
                                domain=[("is_pool", "=", False)])
    equipe_color = fields.Integer(related="equipe_id.color")
    superviseur_id = fields.Many2one(related="equipe_id.superviseur_id", store=True, string="Superviseur")
    lieu_id = fields.Many2one("travaux.lieu", "# Lieu", index=True, tracking=True, ondelete="set null",
                              domain=[("kind", "=", "lieu")],
                              help="Nom connu de l'endroit (École du Centre, Maison communale…). "
                                   "Rempli tout seul quand l'adresse correspond à un # figé.")

    objet_ids = fields.Many2many("travaux.lieu", "travaux_demande_objet_rel", "demande_id", "objet_id",
                                 string="# Objets", domain=[("kind", "=", "objet")],
                                 help="Ce qui est concerné : #Banc, #Poubelle, #Chaufferie… Plusieurs # possibles.")

    lieu_precision = fields.Char("Précision sur l'endroit", help="Ex. derrière le préau, côté parking, 2e étage.")
    recurrence_id = fields.Many2one("travaux.recurrence", "Tâche récurrente", index=True, ondelete="set null", copy=False)
    liste = fields.Text("Liste indicative", help="Rues ou arrêts du circuit, pour mémoire.")

    priorite = fields.Selection([("0", "Normale"), ("1", "Urgente")], "Priorité", default="0", tracking=True)
    date_prevue = fields.Date("Jour prévu", tracking=True)
    duree_prevue = fields.Float("Temps prévu (h)")
    consigne = fields.Text("Consigne pour l'équipe")

    state = fields.Selection(STATES, "Statut", default="recue", required=True, tracking=True,
                             group_expand="_group_expand_state", index=True)
    photo_avant = fields.Image("Photo avant", max_width=1280, max_height=1280)
    photo_apres = fields.Image("Photo après", max_width=1280, max_height=1280)

    date_debut = fields.Datetime("Démarré le", readonly=True, copy=False)
    date_fin = fields.Datetime("Terminé le", readonly=True, copy=False)
    pointage_ids = fields.One2many("travaux.pointage", "demande_id", "Heures imputées")
    duree_mesuree = fields.Float(
        "Durée mesurée (h)", compute="_compute_duree", store=True, readonly=False, copy=False,
        help="Calculée entre « Démarrer » et « Terminé ». Corrigeable en cas d'oubli. "
             "Enregistrée pour l'équipe, jamais pour une personne.")
    date_cloture = fields.Datetime("Clôturée le", readonly=True, copy=False)
    date_attribution = fields.Datetime("Attribuée le", readonly=True, copy=False)
    attribue_par_id = fields.Many2one("res.users", "Attribuée par", readonly=True, copy=False)
    cloture_par_id = fields.Many2one("res.users", "Clôturée par", readonly=True, copy=False)
    date_citoyen = fields.Datetime("Demandeur informé le", readonly=True, copy=False)
    service_transfert = fields.Char("Transférée à")
    carte_url = fields.Char("Carte", compute="_compute_carte_url")

    # ------------------------------------------------------------------ computes
    @api.depends("name", "titre")
    def _compute_display_title(self):
        for rec in self:
            rec.display_title = "%s · %s" % (rec.name, rec.titre) if rec.name and rec.name != "/" else rec.titre

    @api.depends("date_debut", "date_fin", "pointage_ids.heures")
    def _compute_duree(self):
        for rec in self:
            if rec.pointage_ids:
                rec.duree_mesuree = round(sum(rec.pointage_ids.mapped("heures")), 2)
            elif rec.date_debut and rec.date_fin:
                rec.duree_mesuree = round((rec.date_fin - rec.date_debut).total_seconds() / 3600.0, 2)
            elif not rec.duree_mesuree:
                rec.duree_mesuree = 0.0

    @api.depends("adresse", "latitude", "longitude")
    def _compute_carte_url(self):
        for rec in self:
            if rec.latitude and rec.longitude:
                rec.carte_url = "https://www.openstreetmap.org/?mlat=%s&mlon=%s#map=18/%s/%s" % (
                    rec.latitude, rec.longitude, rec.latitude, rec.longitude)
            elif rec.adresse:
                rec.carte_url = "https://www.openstreetmap.org/search?query=%s" % (
                    ("%s, %s %s" % (rec.adresse, rec.env.company.zip or "", rec.env.company.city or "")).strip(", ").replace(" ", "+"))
            else:
                rec.carte_url = False

    @api.model
    def _group_expand_state(self, states, domain, order):
        present = [s for s in states if s]
        return [key for key, _label in STATES if key in KANBAN_STATES or key in present]

    @api.onchange("adresse")
    def _onchange_adresse_lieu(self):
        if self.adresse:
            lieu = self.env["travaux.lieu"]._match(self.adresse)
            if lieu:
                self.lieu_id = lieu

    @api.onchange("lieu_id")
    def _onchange_lieu(self):
        if self.lieu_id and not self.adresse:
            self.adresse = self.lieu_id.adresse or self.lieu_id.name

    @api.model
    def _lieu_vals(self, vals, current=None):
        """Complète lieu-dit / adresse / position l'un à partir de l'autre."""
        Lieu = self.env["travaux.lieu"]
        if vals.get("lieu_id"):
            lieu = Lieu.browse(vals["lieu_id"])
            if "adresse" not in vals and not (current and current.adresse) or tag_key(vals.get("adresse")) == lieu.cle:
                vals["adresse"] = lieu.adresse or lieu.name
            if lieu.latitude and not vals.get("latitude") and not (current and current.latitude):
                vals.update(latitude=lieu.latitude, longitude=lieu.longitude)
        elif "lieu_id" not in vals and vals.get("adresse"):
            lieu = Lieu._match(vals["adresse"])
            if lieu:
                vals["lieu_id"] = lieu.id
                if lieu.adresse and tag_key(vals["adresse"]) == lieu.cle:
                    vals["adresse"] = lieu.adresse
                if lieu.latitude and not vals.get("latitude") and not (current and current.latitude):
                    vals.update(latitude=lieu.latitude, longitude=lieu.longitude)
        return vals

    @api.onchange("categorie_id")
    def _onchange_categorie(self):
        if self.categorie_id.equipe_defaut_id and not self.equipe_id:
            self.equipe_id = self.categorie_id.equipe_defaut_id

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                code = SEQUENCES.get(vals.get("type_travail") or "demande")
                vals["name"] = self.env["ir.sequence"].next_by_code(code) or "/"
            if vals.get("categorie_id") and not vals.get("equipe_id") and vals.get("state") not in (None, "recue"):
                cat = self.env["travaux.categorie"].browse(vals["categorie_id"])
                vals["equipe_id"] = cat.equipe_defaut_id.id
            if not vals.get("lieu_id"):
                vals.pop("lieu_id", None)
            self._lieu_vals(vals)
        records = super().create(vals_list)
        if not self.env.context.get("travaux_no_photo_post"):
            for rec in records.filtered("photo_avant"):
                rec._post_photo("photo_avant", _("AVANT"), rec.description or "")
        return records

    def write(self, vals):
        if ("adresse" in vals or vals.get("lieu_id")) and len(self) == 1:
            vals = self._lieu_vals(dict(vals), current=self)
        elif "adresse" in vals and "lieu_id" not in vals:
            for rec in self:
                rec.write(dict(vals))
            return True
        res = super().write(vals)
        if "photo_apres" in vals and vals["photo_apres"] and not self.env.context.get("travaux_no_photo_post"):
            for rec in self:
                rec._post_photo("photo_apres", _("APRÈS"), "")
        return res

    def _post_photo(self, field, tag, text):
        """Publie la photo dans le fil de la demande, comme un message avec image."""
        self.ensure_one()
        data = self[field]
        if not data:
            return
        body = Markup("<b>%s</b>%s") % (tag, (" · " + text) if text else "")
        self.message_post(
            body=body, message_type="comment", subtype_xmlid="mail.mt_comment",
            attachments=[("%s_%s.jpg" % (self.name, field), base64.b64decode(data))])

    # ------------------------------------------------------------------ workflow
    def _notify_team(self):
        for rec in self.filtered("equipe_id"):
            employees = rec.equipe_id.jour_ids | rec.equipe_id.member_ids
            partners = employees.mapped("user_id.partner_id")
            if partners:
                rec.message_subscribe(partner_ids=partners.ids)

    def _inform_citizen(self, template_xmlid, with_photo=False):
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not template:
            return
        for rec in self.filtered(lambda r: r.demandeur_id.email):
            email_values = {}
            if with_photo and rec.photo_apres:
                att = self.env["ir.attachment"].create({
                    "name": "%s_apres.jpg" % rec.name,
                    "datas": rec.photo_apres,
                    "res_model": rec._name,
                    "res_id": rec.id,
                })
                email_values["attachment_ids"] = [(4, att.id)]
            template.send_mail(rec.id, email_values=email_values)
            if template_xmlid.endswith("resolu"):
                rec.date_citoyen = fields.Datetime.now()

    def action_attribuer(self):
        for rec in self:
            if not rec.equipe_id:
                raise UserError(_("Choisissez une équipe avant d'attribuer %s.", rec.name))
            vals = {"state": "planif", "date_attribution": fields.Datetime.now(), "attribue_par_id": self.env.uid}
            if not rec.date_prevue:
                vals["date_prevue"] = fields.Date.context_today(rec)
            rec.write(vals)
            rec._notify_team()
            rec.message_post(
                body=_("Attribuée à l'équipe %(e)s · priorité %(p)s",
                       e=rec.equipe_id.name, p=dict(rec._fields["priorite"].selection)[rec.priorite].lower()),
                message_type="notification", subtype_xmlid="mail.mt_note")
            if rec.consigne:
                rec.message_post(body=rec.consigne, message_type="comment", subtype_xmlid="mail.mt_comment")
        self._inform_citizen("service_travaux.mail_template_prise_en_charge")
        return True

    def action_transferer(self):
        for rec in self:
            rec.state = "transferee"
            rec.service_transfert = rec.service_transfert or rec.categorie_id.service_competent or _("service compétent")
            rec.message_post(body=_("Hors compétence : transférée à %s.", rec.service_transfert))
        self._inform_citizen("service_travaux.mail_template_transfert")
        return True

    def action_demander_precision(self):
        for rec in self:
            rec.state = "precision"
            rec.message_post(body=_("Précision demandée au demandeur."))
        return True

    def action_remettre_a_traiter(self):
        self.write({"state": "recue"})
        return True

    def action_demarrer(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.state not in ("planif", "recue"):
                continue
            rec.write({"state": "cours", "date_debut": now, "date_fin": False})
            rec.message_post(
                body=_("%(u)s a démarré l'intervention · %(h)s",
                       u=self.env.user.name, h=fields.Datetime.context_timestamp(rec, now).strftime("%H:%M")),
                message_type="notification", subtype_xmlid="mail.mt_note")
        return True

    def action_terminer(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.type_travail == "demande" and not rec.photo_apres:
                raise UserError(_("Ajoutez d'abord la photo « après » (bouton appareil photo)."))
            vals = {"state": "valider", "date_fin": now}
            if not rec.date_debut:
                vals["date_debut"] = now
            rec.write(vals)
            # durée sur place : mesurée entre Démarrer et Terminé, ou confirmée par l'équipe à la clôture
            clock = self.env.context.get("travaux_duree") or round((rec.date_fin - rec.date_debut).total_seconds() / 3600.0, 2)
            n = max(1, rec.nb_presents)   # personnes confirmées par l'équipe : le temps compté est durée × personnes
            measured = round(clock * n, 2)
            rec.duree_horloge = clock
            if rec.equipe_id:
                self.env["travaux.pointage"].create({
                    "demande_id": rec.id, "equipe_id": rec.equipe_id.id, "heures": measured,
                    "date": fields.Date.context_today(rec), "origine": "mesure"})
            detail = _("%(c)s × %(n)s personne(s) = %(d)s comptées", c=rec._fmt_heures(clock), n=n,
                       d=rec._fmt_heures(rec.duree_mesuree)) if rec.nb_presents else _("%s mesurées", rec._fmt_heures(rec.duree_mesuree))
            if rec.avancement:
                detail += _(" · circuit fait à %s %%", rec.avancement)
            if rec.presents:
                detail += _(" · présents : %s", rec.presents)
            rec.message_post(
                body=_("%(u)s a terminé le travail · %(h)s · %(d)s",
                       u=self.env.user.name,
                       h=fields.Datetime.context_timestamp(rec, now).strftime("%H:%M"), d=detail),
                message_type="notification", subtype_xmlid="mail.mt_note")
        return True

    def action_cloturer(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.write({"state": "close", "date_cloture": now, "cloture_par_id": self.env.uid})
            informed = bool(rec.demandeur_id.email)
            rec.message_post(
                body=_("Demande clôturée par le superviseur") + (
                    _(" · le demandeur est informé que le problème est résolu") if informed else ""),
                message_type="notification", subtype_xmlid="mail.mt_note")
        self._inform_citizen("service_travaux.mail_template_resolu", with_photo=True)
        return True

    def action_rouvrir(self):
        self.write({"state": "cours", "date_cloture": False})
        return True

    def action_ouvrir_carte(self):
        self.ensure_one()
        if not self.carte_url:
            raise UserError(_("Aucun lieu indiqué."))
        return {"type": "ir.actions.act_url", "url": self.carte_url, "target": "new"}

    @staticmethod
    def _fmt_heures(x):
        h = int(x)
        m = int(round((x - h) * 60))
        return "%s h %02d" % (h, m) if m else "%s h" % h
