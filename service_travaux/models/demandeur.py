"""Demandeurs internes (écoles, administration, autres services) : ils encodent une demande et la suivent.

Ils ne voient que leurs propres demandes, en lecture : ils peuvent échanger dans le fil de la demande,
mais ni la modifier, ni l'attribuer, ni la transformer en bon de travail. La création passe par
l'assistant « Nouvelle demande », qui vérifie la saisie minimale puis crée la demande au nom du service.
"""
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"
    # écrire dans le fil d'une demande demande seulement de pouvoir la lire (le demandeur n'a pas le droit de la modifier)
    _mail_post_access = "read"

    demandeur_user_id = fields.Many2one("res.users", "Encodée par (demandeur)", index=True, readonly=True, copy=False)
    service_demandeur = fields.Char("Service demandeur", readonly=True, copy=False)
    etat_demandeur = fields.Char("Où en est la demande", compute="_compute_etat_demandeur")

    @api.depends("state", "date_prevue", "equipe_id")
    def _compute_etat_demandeur(self):
        labels = {
            "recue": _("Reçue, en cours d'examen"), "precision": _("Une précision vous est demandée"),
            "transferee": _("Transférée au service compétent"), "planif": _("Planifiée"),
            "cours": _("En cours"), "valider": _("Travail terminé"), "close": _("Terminée"),
            "annulee": _("Annulée"),
        }
        for rec in self:
            txt = labels.get(rec.state, "")
            if rec.state == "annulee" and rec.annulation == "refus":
                txt = _("Refusée")
            if rec.state == "planif" and rec.date_prevue:
                txt = _("Planifiée le %s", rec.date_prevue.strftime("%d/%m/%Y"))
            rec.etat_demandeur = txt


class TravauxDemandeNouvelle(models.TransientModel):
    _name = "travaux.demande.nouvelle"
    _description = "Nouvelle demande d'un service"

    titre = fields.Char("Quoi ?", required=True, help="En quelques mots : fuite au plafond, porte bloquée…")
    description = fields.Text("Description", required=True)
    lieu_id = fields.Many2one("travaux.lieu", "Bâtiment ou lieu connu",
                              domain=[("kind", "=", "lieu"), ("state", "=", "valide")])
    adresse = fields.Char("Où ?", help="Adresse, ou précision dans le bâtiment : 2e étage, classe 3, côté parking…")
    categorie_id = fields.Many2one("travaux.categorie", "Type de demande", domain=[("hors_competence", "=", False)])
    photo = fields.Image("Photo", max_width=1600, max_height=1600)
    contact = fields.Char("Personne de contact", required=True, default=lambda s: s.env.user.name)
    service = fields.Char("Service", required=True, default=lambda s: s._default_service())

    def _default_service(self):
        emp = self.env.user.employee_ids[:1]
        return emp.department_id.name if emp and emp.department_id else ""

    def action_envoyer(self):
        self.ensure_one()
        if not self.lieu_id and not (self.adresse or "").strip():
            raise UserError(_("Indiquez où se trouve le problème : un bâtiment connu ou une adresse."))
        user = self.env.user
        partner = user.partner_id
        Demande = self.env["travaux.demande"].sudo()
        rec = Demande.create({
            "titre": self.titre.strip(), "description": self.description,
            "lieu_id": self.lieu_id.id or False,
            "adresse": (self.adresse or "").strip() or self.lieu_id.adresse or False,
            "lieu_precision": (self.adresse or "").strip() if self.lieu_id else False,
            "categorie_id": self.categorie_id.id or False, "photo_avant": self.photo or False,
            "source": "service", "state": "recue", "type_travail": "demande",
            "demandeur_id": partner.id, "demandeur_user_id": user.id,
            "service_demandeur": self.service.strip(),
        })
        rec.message_subscribe(partner_ids=partner.ids)
        rec.message_post(body=_("Demande encodée par %(c)s (%(s)s).", c=self.contact, s=self.service),
                         author_id=partner.id, message_type="comment", subtype_xmlid="mail.mt_note")
        return {
            "type": "ir.actions.act_window", "res_model": "travaux.demande", "res_id": rec.id,
            "views": [[self.env.ref("service_travaux.view_mes_demandes_form").id, "form"]], "target": "current",
            "name": _("Ma demande"),
        }


class TravauxDemandeDemandeur(models.Model):
    """Écran « Mes demandes » (client OWL) : même ergonomie que « Demandes à traiter », réduit au demandeur."""
    _inherit = "travaux.demande"

    def _dem_check(self, rec_id):
        rec = self.browse(int(rec_id)).exists()
        if not rec or rec.demandeur_user_id != self.env.user:
            raise UserError(_("Cette demande n'est pas la vôtre."))
        rec.check_access("read")
        return rec

    def _dem_messages(self):
        """Le fil vu par le demandeur : les échanges et les photos, jamais les notes internes du service."""
        self.ensure_one()
        out = []
        for m in self.sudo()._tv_messages():
            msg = self.env["mail.message"].sudo().browse(m["id"])
            if m["kind"] == "bubble" and msg.subtype_id and msg.subtype_id.internal:
                continue
            if m["kind"] == "sys":
                continue
            out.append(m)
        return out

    def _dem_card(self, full=False):
        self.ensure_one()
        rec = self.sudo()
        st = {"recue": "recue", "precision": "recue", "transferee": "close", "annulee": "close", "planif": "planif",
              "cours": "cours", "valider": "valider", "close": "close"}[rec.state]
        d = {
            "id": rec.id, "ref": rec.name, "titre": rec.titre, "lieu": ("#" + rec.lieu_id.name) if rec.lieu_id else "",
            "adresse": rec.adresse or "", "precision": rec.lieu_precision or "", "description": rec.description or "",
            "create": fields.Datetime.to_string(rec.create_date), "etat": self.browse(rec.id).etat_demandeur, "st": st,
            "state": rec.state, "has_avant": bool(rec.photo_avant), "has_apres": bool(rec.photo_apres),
            "date_prevue": rec.date_prevue.isoformat() if rec.date_prevue else False,
            "equipe": rec.equipe_id.name or "", "service": rec.service_demandeur or "",
            "lat": rec.latitude or False, "lng": rec.longitude or False,
            "motif": rec.motif_annulation or "" if rec.state == "annulee" else "",
        }
        if full:
            d["messages"] = self._dem_messages()
            d["timeline"] = [dict(s, detail="") for s in rec._tv_timeline()]
        return d

    @api.model
    def tv_dem_data(self, rec_id=False):
        user = self.env.user
        recs = self.search([("demandeur_user_id", "=", user.id)], order="create_date desc")
        emp = user.employee_ids[:1]
        cur = self._dem_check(rec_id) if rec_id else False
        return {
            "items": [r._dem_card() for r in recs],
            "current": cur._dem_card(full=True) if cur else False,
            "lieux": [{"id": l.id, "name": l.name, "adresse": l.adresse or ""} for l in self.env["travaux.lieu"].search(
                [("kind", "=", "lieu"), ("state", "=", "valide")], order="name")],
            "categories": [{"id": c.id, "name": c.name} for c in self.env["travaux.categorie"].search(
                [("hors_competence", "=", False)])],
            "me": user.name, "service": emp.department_id.name if emp and emp.department_id else "",
            "company": self.env.company.name,
        }

    @api.model
    def tv_dem_create(self, vals):
        wiz = self.env["travaux.demande.nouvelle"].create({
            "titre": (vals.get("titre") or "").strip(), "description": (vals.get("description") or "").strip(),
            "lieu_id": int(vals["lieu_id"]) if vals.get("lieu_id") else False,
            "adresse": (vals.get("adresse") or "").strip() or False,
            "categorie_id": int(vals["categorie_id"]) if vals.get("categorie_id") else False,
            "photo": vals.get("photo") or False,
            "contact": (vals.get("contact") or self.env.user.name).strip(),
            "service": (vals.get("service") or "").strip() or _("Service non précisé"),
        })
        if not wiz.titre or not wiz.description:
            raise UserError(_("Indiquez le problème et une description."))
        action = wiz.action_envoyer()
        return action["res_id"]

    @api.model
    def tv_dem_post(self, rec_id, body, photo=False):
        rec = self._dem_check(rec_id)
        body = (body or "").strip()
        if not body and not photo:
            raise UserError(_("Écrivez un message ou ajoutez une photo."))
        kw = {"body": body or _("Photo"), "message_type": "comment", "subtype_xmlid": "mail.mt_comment",
              "author_id": self.env.user.partner_id.id}
        if photo:
            kw["attachments"] = [("photo_%s.jpg" % rec.name, base64.b64decode(photo))]
        rec.message_post(**kw)
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "demande", "ids": [rec.id]})
        return self.tv_dem_data(rec.id)
