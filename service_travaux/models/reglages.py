"""Réglages de FACILITY : organisation des rôles selon la taille de la commune, pointeuse, réponse au contact."""
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import plaintext2html

P = "service_travaux."


class TravauxConfig(models.TransientModel):
    _name = "travaux.config"
    _description = "Réglages de FACILITY"

    commune = fields.Char("Nom de la commune", help="Affiché en haut à droite, dans l'app terrain, les mails et les PDF.")
    logo = fields.Binary("Logo de la commune", help="Affiché à côté du nom en haut à droite et sur tous les documents (PDF).")
    organisation = fields.Selection([
        ("fusion", "Rôles réunis : la même personne reçoit, attribue et envoie à l'équipe (petite commune)"),
        ("separe", "Rôles séparés : le secrétariat reçoit et attribue, le superviseur planifie et envoie à l'équipe"),
    ], "Organisation", required=True, default="fusion")
    secretariat_attribue = fields.Boolean(
        "Le secrétariat choisit l'équipe", default=True,
        help="Décoché : le secrétariat complète la demande et répond au contact ; c'est le superviseur qui choisit l'équipe.")
    pointeuse = fields.Boolean(
        "Présences d'après la pointeuse",
        help="Le statut du jour de chaque agent vient de l'application Présences d'Odoo : pointé = présent.")
    pointeuse_heure = fields.Float(
        "Absent si pas pointé à", default=8.5,
        help="Avant cette heure, personne n'est marqué absent. Après, un agent qui n'a pas pointé est compté absent.")
    pointeuse_ok = fields.Boolean("Application Présences installée", readonly=True)

    @api.model
    def _get(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "organisation": ICP.get_param(P + "organisation") or "fusion",
            "secretariat_attribue": ICP.get_param(P + "secretariat_attribue", "1") != "0",
            "pointeuse": ICP.get_param(P + "pointeuse") == "1" and "hr.attendance" in self.env,
            "pointeuse_heure": float(ICP.get_param(P + "pointeuse_heure") or 8.5),
        }

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        cfg = self._get()
        ICP = self.env["ir.config_parameter"].sudo()
        res.update(cfg, pointeuse=ICP.get_param(P + "pointeuse") == "1", pointeuse_ok="hr.attendance" in self.env,
                   commune=self.env.company.name, logo=self.env.company.logo)
        return res

    def action_save(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Seul l'administrateur peut modifier les paramètres."))
        ICP = self.env["ir.config_parameter"].sudo()
        company = self.env.company.sudo()
        if (self.commune or "").strip() and self.commune.strip() != company.name:
            company.name = self.commune.strip()
        if self.logo != company.logo:
            company.logo = self.logo
        ICP.set_param(P + "organisation", self.organisation)
        ICP.set_param(P + "secretariat_attribue", "1" if self.secretariat_attribue else "0")
        ICP.set_param(P + "pointeuse", "1" if self.pointeuse else "0")
        ICP.set_param(P + "pointeuse_heure", str(self.pointeuse_heure or 8.5))
        if self.pointeuse:
            self.env["hr.employee"]._travaux_sync_pointeuse()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_install_presences(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Seul l'administrateur peut installer une application."))
        module = self.env["ir.module.module"].sudo().search([("name", "=", "hr_attendance")], limit=1)
        if not module:
            raise UserError(_("L'application Présences n'est pas disponible sur ce serveur."))
        self.env["ir.config_parameter"].sudo().set_param(P + "pointeuse", "1")
        return module.button_immediate_install()


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    @api.model
    def _travaux_sync_pointeuse(self):
        """Statut du jour d'après la pointeuse : pointé = présent ; pas pointé après l'heure limite = absent."""
        cfg = self.env["travaux.config"]._get()
        if not cfg["pointeuse"]:
            return False
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        day_start = fields.Datetime.now() - timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)
        emps = self.sudo().search([("equipe_id", "!=", False)])
        clocked = set(self.env["hr.attendance"].sudo().search([
            ("employee_id", "in", emps.ids), ("check_in", ">=", day_start)]).mapped("employee_id").ids)
        late = now.weekday() < 5 and (now.hour + now.minute / 60.0) >= cfg["pointeuse_heure"]
        to_present = emps.filtered(lambda e: e.id in clocked and e.statut_jour != "present")
        to_absent = emps.filtered(lambda e: late and e.id not in clocked and e.statut_jour == "present")
        if to_present:
            to_present.write({"statut_jour": "present"})
        if to_absent:
            to_absent.write({"statut_jour": "absent", "equipe_pret_id": False})
        if to_present or to_absent:
            self.env["bus.bus"]._sendone("travaux.live", "travaux/update", {"kind": "equipes", "ids": []})
        return True


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    @api.model
    def _tv_droits(self):
        """Ce que l'utilisateur peut faire, selon son rôle et l'organisation choisie."""
        cfg = self.env["travaux.config"]._get()
        sup = self._tv_is_sup()
        secr = self.env.user.has_group("service_travaux.group_secretariat")
        return {
            "organisation": cfg["organisation"],
            "peut_attribuer": sup or (secr and cfg["secretariat_attribue"]),
            # envoyer à l'équipe : le superviseur toujours ; le secrétariat seulement quand les rôles sont réunis
            "peut_envoyer": sup or (secr and cfg["secretariat_attribue"] and cfg["organisation"] == "fusion"),
            "bureau": sup or secr,
        }

    @api.model
    def tv_repondre(self, rec_id, message):
        """Répond au contact de la demande (et aux autres signaleurs), chacun par un mail séparé."""
        self._tv_check_bureau()
        rec = self.browse(rec_id)
        message = (message or "").strip()
        if not message:
            raise UserError(_("Écrivez votre réponse."))
        contacts = rec._contacts()
        if not contacts:
            raise UserError(_("Cette demande n'a pas de contact avec une adresse e-mail."))
        company = self.env.company
        body = plaintext2html(message) + Markup("<p style='color:#666'>%s · %s</p>") % (company.name, _("Service technique"))
        sender = self.env.user.email_formatted or company.email_formatted
        sent = 0
        for partner in contacts:
            mail = self.env["mail.mail"].sudo().create({
                "subject": _("Votre demande %(r)s : %(t)s", r=rec.name, t=rec.titre), "body_html": body,
                "email_to": partner.email, "email_from": sender, "reply_to": sender,
                "auto_delete": False, "model": rec._name, "res_id": rec.id})
            mail.send(raise_exception=False)
            sent += mail.state == "sent"
        rec.message_post(
            body=Markup("<b>%s</b><br/>%s") % (
                _("Réponse au contact (%s)", ", ".join(contacts.mapped("name"))), plaintext2html(message)),
            message_type="comment", subtype_xmlid="mail.mt_note")
        return {"asked": len(contacts), "sent": sent}
