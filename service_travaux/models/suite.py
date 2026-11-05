"""Suites d'une demande : attribuer puis envoyer, transférer hors compétence (PDF + mail), fusionner les doublons."""
import base64
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import plaintext2html

from .lieu import distance_m, norm_adresse

RAYON_DOUBLON_DEMANDE = 80  # m : deux signalements aussi proches parlent sans doute du même problème


class TravauxTransfertContact(models.Model):
    _name = "travaux.transfert.contact"
    _description = "Contact de transfert (service compétent hors du service technique)"
    _order = "name"

    name = fields.Char("Service ou organisme", required=True, help="Ex. Service Urbanisme, Police locale, ORES, Région wallonne.")
    email = fields.Char("Adresse(s) e-mail", required=True, help="Plusieurs adresses possibles, séparées par une virgule.")
    note = fields.Char("Pour quel type de demande")
    active = fields.Boolean(default=True)


class TravauxCategorie(models.Model):
    _inherit = "travaux.categorie"

    transfert_contact_id = fields.Many2one("travaux.transfert.contact", "Contact de transfert",
                                           help="Destinataire proposé quand une demande de cette catégorie est hors compétence.")


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    active = fields.Boolean(default=True)
    contact_ids = fields.Many2many(
        "res.partner", "travaux_demande_contact_rel", "demande_id", "partner_id", string="Autres contacts",
        help="Personnes qui ont signalé le même problème (signalements fusionnés). Elles sont informées comme le contact principal.")
    fusion_id = fields.Many2one("travaux.demande", "Fusionnée dans", readonly=True, copy=False, index=True)
    fusion_ids = fields.One2many("travaux.demande", "fusion_id", "Signalements fusionnés", context={"active_test": False})
    transfert_contact_id = fields.Many2one("travaux.transfert.contact", "Transférée à (contact)", readonly=True, copy=False)

    def _tv_check_bureau(self):
        if not (self.env.user.has_group("service_travaux.group_superviseur")
                or self.env.user.has_group("service_travaux.group_secretariat")):
            raise AccessError(_("Réservé au secrétariat et aux superviseurs."))

    # ------------------------------------------------------------ contacts à prévenir
    def _contacts(self):
        self.ensure_one()
        return (self.demandeur_id | self.contact_ids).filtered("email")

    def _inform_citizen(self, template_xmlid, with_photo=False):
        """Prévient chaque contact séparément : personne ne voit l'adresse des autres signaleurs."""
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not template:
            return
        for rec in self:
            contacts = rec._contacts()
            if not contacts:
                continue
            email_values = {}
            if with_photo and rec.photo_apres:
                att = self.env["ir.attachment"].create({
                    "name": "%s_apres.jpg" % rec.name, "datas": rec.photo_apres,
                    "res_model": rec._name, "res_id": rec.id})
                email_values["attachment_ids"] = [(4, att.id)]
            for partner in contacts:
                template.send_mail(rec.id, email_values=dict(email_values, email_to=partner.email, recipient_ids=[]))
            if template_xmlid.endswith("resolu"):
                rec.date_citoyen = fields.Datetime.now()

    # ------------------------------------------------------------ hors compétence : PDF + mail
    def _pdf(self):
        self.ensure_one()
        content, _fmt = self.env["ir.actions.report"].sudo()._render_qweb_pdf("service_travaux.report_demande", self.ids)
        return content

    @api.model
    def tv_transfert_prepare(self, rec_id):
        self._tv_check_bureau()
        rec = self.browse(rec_id)
        contacts = self.env["travaux.transfert.contact"].search([])
        default = rec.categorie_id.transfert_contact_id
        company = self.env.company
        lieu = rec.adresse or ""
        if rec.lieu_id:
            lieu = ("#%s, %s" % (rec.lieu_id.name, lieu)).strip(", ")
        message = _(
            "Bonjour,\n\nLe service technique de %(c)s a reçu la demande ci-dessous. Elle ne relève pas de sa compétence "
            "et nous vous la transmettons pour suite utile.\n\nDemande : %(t)s\nLieu : %(l)s\nReçue le : %(d)s\n\n"
            "Le détail complet (description, photos, contact du demandeur) se trouve dans le PDF joint.\n\n"
            "Merci de nous tenir informés de la suite donnée.\n\nCordialement,\n%(u)s\n%(c)s",
            c=company.name, t=rec.titre, l=lieu or _("non précisé"),
            d=fields.Datetime.context_timestamp(rec, rec.create_date).strftime("%d/%m/%Y"), u=self.env.user.name)
        return {
            "contacts": [{"id": c.id, "name": c.name, "email": c.email, "note": c.note or ""} for c in contacts],
            "contact_id": default.id or False,
            "sujet": _("Transfert d'une demande : %(t)s (%(r)s)", t=rec.titre, r=rec.name),
            "message": message,
            "pdf_url": "/report/pdf/service_travaux.report_demande/%s" % rec.id,
            "can_config": self._tv_is_sup(),
            "has_contact": bool(rec._contacts()),
        }

    @api.model
    def tv_transfert_send(self, rec_id, vals):
        """Transfère la demande : PDF complet joint au mail vers le contact choisi, demandeur prévenu."""
        self._tv_check_bureau()
        rec = self.browse(rec_id)
        contact = self.env["travaux.transfert.contact"].browse(int(vals.get("contact_id") or 0)).exists()
        email_to = (vals.get("email") or contact.email or "").strip()
        send = bool(vals.get("send", True))
        if send and not email_to:
            raise UserError(_("Choisissez un contact de transfert ou indiquez une adresse e-mail."))
        name = contact.name or (vals.get("service") or "").strip() or email_to or _("service compétent")
        pdf = rec._pdf()
        att = self.env["ir.attachment"].create({
            "name": "%s.pdf" % rec.name, "datas": base64.b64encode(pdf), "mimetype": "application/pdf",
            "res_model": rec._name, "res_id": rec.id})
        sent = False
        if send:
            mail = self.env["mail.mail"].sudo().create({
                "subject": (vals.get("sujet") or "").strip() or _("Transfert d'une demande : %s", rec.titre),
                "body_html": plaintext2html(vals.get("message") or ""),
                "email_to": email_to,
                "email_from": self.env.user.email_formatted or self.env.company.email_formatted,
                "reply_to": self.env.user.email_formatted or self.env.company.email_formatted,
                "attachment_ids": [(4, att.id)], "auto_delete": False,
                "model": rec._name, "res_id": rec.id,
            })
            mail.send(raise_exception=False)
            sent = mail.state == "sent"
        rec.write({"state": "transferee", "service_transfert": name, "transfert_contact_id": contact.id or False})
        if send and sent:
            body = _("Hors compétence : transférée à %(n)s (%(e)s), PDF de la demande joint.", n=name, e=email_to)
        elif send:
            body = _("Hors compétence : transférée à %(n)s. Le mail vers %(e)s n'est pas parti (envoi de mails non "
                     "configuré ?) : envoyez le PDF joint à la main.", n=name, e=email_to)
        else:
            body = _("Hors compétence : transférée à %s (sans mail). PDF de la demande joint.", name)
        rec.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note", attachment_ids=[att.id])
        rec._inform_citizen("service_travaux.mail_template_transfert")
        return {"sent": sent, "asked": send, "service": name, "pdf_url": "/web/content/%s?download=true" % att.id}

    @api.model
    def _facility_logo(self):
        """Logo FACILITY (blanc) intégré au PDF, sans dépendre d'une adresse web."""
        from odoo.tools import file_open
        with file_open("service_travaux/static/src/img/facility_blanc.png", "rb") as f:
            return base64.b64encode(f.read())

    def action_pdf(self):
        return self.env.ref("service_travaux.action_report_demande").report_action(self)

    # ------------------------------------------------------------ doublons : repérer et fusionner
    def _tv_doublons(self):
        """Signalements ouverts qui parlent sans doute du même problème (même endroit, même sujet)."""
        self.ensure_one()
        if self.type_travail != "demande" or self.state in ("close", "transferee", "annulee"):
            return []
        since = fields.Datetime.now() - timedelta(days=60)
        others = self.search([
            ("id", "!=", self.id), ("type_travail", "=", "demande"), ("create_date", ">=", since),
            ("state", "not in", ("close", "transferee", "annulee"))], limit=300)
        adr = norm_adresse(self.adresse)
        out = []
        for o in others:
            dist = distance_m(self.latitude, self.longitude, o.latitude, o.longitude)
            near = dist is not None and dist <= RAYON_DOUBLON_DEMANDE
            same_place = near or (adr and adr == norm_adresse(o.adresse)) or (self.lieu_id and self.lieu_id == o.lieu_id)
            if not same_place:
                continue
            same_topic = (
                (self.categorie_id and self.categorie_id == o.categorie_id)
                or bool(self.objet_ids & o.objet_ids)
                or not (self.categorie_id and o.categorie_id))
            if not same_topic:
                continue
            out.append({
                "id": o.id, "ref": o.name, "titre": o.titre, "adresse": o.adresse or "",
                "dist": round(dist) if dist is not None else False,
                "date": fields.Datetime.to_string(o.create_date), "st_label": o._tv_status()[1],
                "older": o.create_date <= self.create_date,
            })
        return sorted(out, key=lambda x: (not x["older"], x["dist"] is False, x["dist"] or 0))[:3]

    @api.model
    def tv_fusionner(self, rec_id, target_id, prevenir=True):
        """Fusionne le signalement ``rec`` dans ``target`` : une seule demande à traiter, tous les signaleurs gardés."""
        self._tv_check_bureau()
        rec, target = self.browse(rec_id), self.browse(int(target_id))
        if not rec.exists() or not target.exists() or rec == target:
            raise UserError(_("Choisissez la demande dans laquelle fusionner."))
        if target.fusion_id or not target.active:
            raise UserError(_("%s a déjà été fusionnée.", target.name))
        ctx = dict(travaux_no_photo_post=True, tracking_disable=True)
        # contacts : tous les signaleurs restent informés
        people = (rec.demandeur_id | rec.contact_ids) - target.demandeur_id
        vals = {"contact_ids": [(4, p.id) for p in people]}
        if not target.demandeur_id and rec.demandeur_id:
            vals["demandeur_id"] = rec.demandeur_id.id
            vals["contact_ids"] = [(4, p.id) for p in people - rec.demandeur_id]
        if rec.priorite == "1":
            vals["priorite"] = "1"
        if not target.lieu_id and rec.lieu_id:
            vals["lieu_id"] = rec.lieu_id.id
        if rec.objet_ids - target.objet_ids:
            vals["objet_ids"] = [(4, o.id) for o in rec.objet_ids - target.objet_ids]
        if not target.latitude and rec.latitude:
            vals.update(latitude=rec.latitude, longitude=rec.longitude)
        if not target.photo_avant and rec.photo_avant:
            vals["photo_avant"] = rec.photo_avant
        target.with_context(**ctx).write(vals)
        # le second signalement reste lisible dans le fil de la demande gardée, avec ses photos
        images = self.env["ir.attachment"].search([
            ("res_model", "=", rec._name), ("res_id", "=", rec.id), ("mimetype", "=like", "image/%")], order="id")
        who = rec.demandeur_id.name or _("signaleur inconnu")
        body = Markup("<b>%s</b><br/>%s") % (
            _("Signalement %(r)s fusionné (reçu le %(d)s, %(w)s)", r=rec.name, w=who,
              d=fields.Datetime.context_timestamp(rec, rec.create_date).strftime("%d/%m/%Y")),
            rec.description or rec.titre)
        target.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_comment",
                            attachments=[(a.name or "photo.jpg", base64.b64decode(a.datas)) for a in images[:6]])
        rec.with_context(**ctx).write({"fusion_id": target.id, "active": False})
        rec.message_post(body=_("Fusionnée dans %s : même problème déjà signalé.", target.name),
                         message_type="notification", subtype_xmlid="mail.mt_note")
        informed = 0
        if prevenir:
            template = self.env.ref("service_travaux.mail_template_fusion", raise_if_not_found=False)
            for partner in (rec.demandeur_id | rec.contact_ids).filtered("email"):
                if template:
                    template.send_mail(rec.id, email_values={"email_to": partner.email, "recipient_ids": []})
                    informed += 1
        self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "demande", "ids": [rec.id, target.id]})
        return {"id": target.id, "ref": target.name, "informed": informed}


    # ------------------------------------------------------------ changer le statut à la main
    @api.model
    def tv_set_state(self, rec_id, state, garder_equipe=True):
        """Remet une demande dans un autre statut (glisser dans le kanban, liste « Statut » de la fiche)."""
        if not self._tv_is_sup():
            raise AccessError(_("Seul un superviseur peut changer le statut à la main."))
        rec = self.browse(rec_id)
        labels = dict(self._fields["state"].selection)
        if state not in ("recue", "planif", "cours", "valider", "close") or state == rec.state:
            return self.tv_conversation(rec_id)
        now = fields.Datetime.now()
        old = rec._tv_status()[1]
        if state == "recue":
            vals = {"state": "recue", "date_debut": False, "date_fin": False, "date_cloture": False}
            if not garder_equipe:
                vals.update(equipe_id=False, date_prevue=False)
        else:
            if not rec.equipe_id:
                raise UserError(_("Choisissez d'abord une équipe pour %s.", rec.name))
            vals = {"state": state}
            if not rec.date_attribution:
                vals.update(date_attribution=now, attribue_par_id=self.env.uid)
            if state == "planif":
                vals.update(date_debut=False, date_fin=False, date_cloture=False)
                if not rec.date_prevue:
                    vals["date_prevue"] = fields.Date.context_today(rec)
            elif state == "cours":
                vals.update(date_debut=rec.date_debut or now, date_fin=False, date_cloture=False)
            elif state == "valider":
                vals.update(date_debut=rec.date_debut or now, date_fin=rec.date_fin or now, date_cloture=False)
            elif state == "close":
                vals.update(date_debut=rec.date_debut or now, date_fin=rec.date_fin or now,
                            date_cloture=now, cloture_par_id=self.env.uid)
        rec.write(vals)
        if state == "planif":
            rec._notify_team()
        rec.message_post(
            body=_("Statut changé à la main : %(a)s → %(b)s", a=old, b=rec._tv_status()[1]),
            message_type="notification", subtype_xmlid="mail.mt_note")
        return self.tv_conversation(rec_id)

    # ------------------------------------------------------------ fiche de travail directe
    @api.model
    def tv_fiche_travail(self, vals):
        """Crée une fiche de travail pour une équipe, sans passer par « Demandes à traiter »."""
        if not self._tv_droits()["peut_envoyer"]:
            raise AccessError(_("Dans l'organisation choisie, c'est le superviseur qui envoie le travail à l'équipe."))
        titre = (vals.get("titre") or "").strip()
        if not titre:
            raise UserError(_("Donnez un titre à la fiche de travail."))
        if not vals.get("equipe_id"):
            raise UserError(_("Choisissez l'équipe."))
        rec = self.create({
            "titre": titre, "description": (vals.get("description") or "").strip() or False,
            "type_travail": vals.get("type") if vals.get("type") in ("demande", "maintenance", "tournee") else "demande",
            "equipe_id": int(vals["equipe_id"]), "date_prevue": vals.get("date") or False,
            "duree_prevue": float(vals.get("h") or 0), "adresse": (vals.get("adresse") or "").strip() or False,
            "lieu_precision": (vals.get("precision") or "").strip() or False,
            "consigne": (vals.get("consigne") or "").strip() or False,
            "priorite": "1" if vals.get("urgent") else "0", "source": "service", "state": "planif",
            "date_attribution": fields.Datetime.now(), "attribue_par_id": self.env.uid,
        })
        rec._notify_team()
        rec.message_post(body=_("Fiche de travail créée pour l'équipe %s", rec.equipe_id.name),
                         message_type="notification", subtype_xmlid="mail.mt_note")
        if rec.consigne:
            rec.message_post(body=rec.consigne, message_type="comment", subtype_xmlid="mail.mt_comment")
        return rec.id
