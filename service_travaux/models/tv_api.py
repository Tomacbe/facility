"""API des écrans sur mesure (OWL) : lecture compacte en JSON et actions.

Toutes les méthodes s'exécutent avec les droits de l'utilisateur connecté
(règles d'accès et règles d'enregistrement Odoo inchangées).
"""
import re
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext, plaintext2html

from .lieu import RAYON_PROCHE, norm_adresse, tag_format

# Palette identique à la maquette (index = champ « color » de l'équipe)
TEAM_HEX = {
    0: "#55606A", 1: "#B91C1C", 2: "#C2410C", 3: "#A16207", 4: "#0891B2", 5: "#6D28D9",
    6: "#B45309", 7: "#0E7490", 8: "#1D4ED8", 9: "#BE185D", 10: "#15803D", 11: "#7C3AED",
}
SOURCE_LABEL = {
    "guichet": "guichet", "telephone": "téléphone", "email": "e-mail", "courrier": "courrier", "citoyen": "citoyen",
    "police": "police", "service": "service communal", "app": "app terrain", "planning": "planning",
}
TAG_RE = re.compile(r"^\s*\*?(AVANT|APRÈS)\*?\s*(·\s*)?", re.I)
LIVE_CHANNEL = "travaux.live"


def _live(env, kind, ids=None):
    """Prévient tous les écrans ouverts (WebSocket Odoo) qu'une donnée a changé."""
    env["bus.bus"]._sendone(LIVE_CHANNEL, "travaux/update", {"kind": kind, "ids": list(ids or [])})


class TravauxEquipe(models.Model):
    _inherit = "travaux.equipe"

    color_hex = fields.Char(compute="_compute_color_hex")

    def _compute_color_hex(self):
        for team in self:
            team.color_hex = "#9AAEB4" if team.is_pool else TEAM_HEX.get(team.color % 12, "#276577")

    def _tv_team_dict(self):
        self.ensure_one()
        return {
            "id": self.id, "name": self.name, "color": self.color_hex, "is_pool": self.is_pool,
            "sup_id": self.superviseur_id.id, "sup_name": self.superviseur_id.name or "",
            "dept_id": self.department_id.id or False, "dept": self.department_id.sudo().name or "",
            "dept_ids": self._dept_ids(), "mine": self.env.user in self.responsable_ids,
            "min": self.effectif_min, "cap": self.capacite_hebdo,
            "presents": self.nb_presents, "membres": self.nb_membres,
            "presence": self.presence_label, "low": self.sous_effectif,
        }

    @api.model
    def tv_teams(self):
        return [t._tv_team_dict() for t in self.search([])]

    @api.model
    def tv_services(self, tous=False):
        """Services de l'organigramme : ceux qui contiennent une équipe (ou tous, pour choisir où ranger une équipe)."""
        Dept = self.env["hr.department"].sudo()
        if tous:
            depts = Dept.search([]).sorted("complete_name")
        else:
            teams = self.search([("is_pool", "=", False), ("department_id", "!=", False)])
            depts = Dept.browse({i for t in teams for i in t._dept_ids()}).sorted("complete_name")
        return [{"id": d.id, "name": d.complete_name, "depth": d.parent_path.count("/") - 1,
                 "manager": d.manager_id.name or ""} for d in depts]

    @api.model
    def tv_supervisors(self):
        root = self.env.ref("base.user_root")
        sups = self.search([("is_pool", "=", False)]).mapped("superviseur_id") - root
        return [{"id": u.id, "name": u.name} for u in sups.sorted("name")]

    # ------------------------------------------------------------ Équipes du jour
    @api.model
    def tv_equipes_jour(self):
        self.env["hr.employee"]._travaux_sync_pointeuse()
        teams = self.search([])
        Employee = self.env["hr.employee"]
        emps = Employee.search([("equipe_id", "!=", False)])
        people = []
        for e in emps:
            people.append({
                "id": e.id, "name": e.name, "role": e.fonction or "",
                "home": e.equipe_id.id, "lent": e.equipe_pret_id.id or False, "status": e.statut_jour,
                "chef": (e.fonction or "").lower().startswith("chef"),
            })
        alert = None
        low = teams.filtered("sous_effectif")
        if low:
            team = low[0]
            urgent = self.env["travaux.demande"].search([
                ("equipe_id", "=", team.id), ("priorite", "=", "1"),
                ("state", "in", ("planif", "cours")), ("date_prevue", "=", fields.Date.context_today(self))], limit=1)
            txt = _("%(t)s : %(p)s agent(s) présent(s) pour un minimum de %(m)s",
                    t=team.name, p=team.nb_presents, m=team.effectif_min)
            txt += (_(" (demande urgente %s aujourd'hui).", urgent.name) if urgent else ".")
            donor = team.suggestion_id
            if donor:
                origin = donor.equipe_id
                txt += " " + _("Suggestion : %(n)s (%(o)s) est disponible.", n=donor.name,
                               o=_("sans équipe") if not origin or origin.is_pool else origin.name)
            alert = {"team_id": team.id, "text": txt, "donor_id": donor.id or False,
                     "fix": _("Prêter %(n)s à %(t)s", n=donor.name, t=team.name) if donor else ""}
        return {
            "date": fields.Date.context_today(self).isoformat(),
            "teams": [t._tv_team_dict() for t in teams],
            "people": people,
            "supervisors": self.tv_supervisors(), "services": self.tv_services(), "departments": self.tv_services(True),
            "alert": alert,
            "can_edit": self.env.user.has_group("service_travaux.group_superviseur"),
            "pointeuse": self.env["travaux.config"]._get()["pointeuse"],
        }

    @api.model
    def tv_create_team(self, name, sup_id, employee_ids, dept_id=False):
        if not (name or "").strip():
            raise UserError(_("Donnez un nom à l'équipe."))
        used = set(self.search([]).mapped("color"))
        color = next((c for c in (4, 9, 11, 1, 3, 5, 6, 7, 8, 10, 2) if c not in used), 4)
        team = self.create({"name": name.strip(), "superviseur_id": sup_id or self.env.uid,
                            "department_id": int(dept_id or 0) or False,
                            "effectif_min": 1, "capacite_hebdo": 38 * max(1, len(employee_ids)), "color": color})
        # Odoo 19 réserve l'écriture sur les employés au service du personnel : le superviseur ne modifie
        # ici que l'équipe, par un passage contrôlé.
        self._tv_check_sup()
        self.env["hr.employee"].sudo().browse(employee_ids).write({"equipe_id": team.id, "equipe_pret_id": False})
        _live(self.env, "equipes")
        return team.id

    def _tv_check_sup(self):
        if not self.env.user.has_group("service_travaux.group_superviseur"):
            raise AccessError(_("Réservé au superviseur."))

    @api.model
    def tv_lend_suggestion(self, team_id):
        self._tv_check_sup()
        self.browse(team_id).action_preter_suggestion()
        _live(self.env, "equipes")
        return True


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    @api.model
    def tv_set(self, emp_id, vals):
        allowed = {k: v for k, v in vals.items() if k in ("statut_jour", "equipe_pret_id", "equipe_id")}
        self.env["travaux.equipe"]._tv_check_sup()
        emp = self.sudo().browse(emp_id)  # seuls statut du jour et équipe sont modifiés
        if allowed.get("statut_jour") and allowed["statut_jour"] != "present":
            allowed["equipe_pret_id"] = False
        if "equipe_pret_id" in allowed and allowed["equipe_pret_id"] == emp.equipe_id.id:
            allowed["equipe_pret_id"] = False
        if "equipe_id" in allowed:
            allowed["equipe_pret_id"] = False
        emp.write(allowed)
        _live(self.env, "equipes")
        return True


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    # ------------------------------------------------------------ temps réel
    def message_post(self, **kwargs):
        # Les agents de terrain n'ont souvent pas d'adresse e-mail : Odoo refuserait le message.
        if not kwargs.get("email_from") and not (self.env.user.email or self.env.user.partner_id.email):
            from email.utils import formataddr
            company = self.env.company
            kwargs["email_from"] = formataddr((self.env.user.name, company.email or "facility@localhost"))
        msg = super().message_post(**kwargs)
        if not self.env.context.get("travaux_no_live"):
            _live(self.env, "demande", self.ids)
        return msg

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("travaux_no_live") and set(vals) & {"state", "equipe_id", "date_prevue", "priorite", "photo_apres", "duree_mesuree"}:
            _live(self.env, "demande", self.ids)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        if not self.env.context.get("travaux_no_live"):
            _live(self.env, "demande", recs.ids)
        return recs

    # ------------------------------------------------------------ helpers
    def _tv_is_sup(self):
        return self.env.user.has_group("service_travaux.group_superviseur")

    def _travaux_live_touch(self):
        if self and not self.env.context.get("travaux_no_live"):
            _live(self.env, "demande", self.ids)

    def _tv_status(self):
        """Statut commun à toutes les vues (mêmes colonnes que le kanban)."""
        return {
            "recue": ("recue", _("Attribuée · à envoyer") if self.equipe_id else _("À attribuer")), "precision": ("recue", _("Précision demandée")),
            "transferee": ("close", _("Transférée")), "planif": ("planif", _("Planifiée")),
            "annulee": ("close", _("Refusée") if self.annulation == "refus" else _("Annulée")),
            "cours": ("cours", _("En cours")), "valider": ("valider", _("Terminée · à valider")),
            "close": ("close", _("Clôturée")),
        }[self.state]

    def _tv_card(self, last=None):
        self.ensure_one()
        key, label = self._tv_status()
        d = {
            "id": self.id, "ref": self.name, "titre": self.titre, "adresse": self.adresse or "",
            "lieu": ("#" + self.lieu_id.name) if self.lieu_id else "", "lieu_id": self.lieu_id.id or False,
            "lieu_state": self.lieu_id.state or "", "lieu_by": self.lieu_id.propose_par_id.name or "",
            "objets": [{"id": o.id, "name": "#" + o.name, "state": o.state} for o in self.objet_ids],
            "ia": self.ia_a_verifier, "ia_auto": self.ia_cree, "ia_note": self.ia_note or "",
            "type": self.type_travail, "state": self.state, "st": key, "st_label": label,
            "state_label": dict(self._fields["state"].selection)[self.state],
            "urgent": self.priorite == "1",
            "equipe_id": self.equipe_id.id or False, "equipe": self.equipe_id.name or "",
            "equipe_color": self.equipe_id.color_hex if self.equipe_id else "#9AAEB4",
            "date_prevue": self.date_prevue.isoformat() if self.date_prevue else False,
            "has_avant": bool(self.photo_avant), "has_apres": bool(self.photo_apres),
            "lat": self.latitude or False, "lng": self.longitude or False,
            "duree_prevue": self.duree_prevue, "duree_mesuree": self.duree_mesuree,
            "source": SOURCE_LABEL.get(self.source, self.source or ""),
            "categorie": self.categorie_id.name or "", "description": self.description or "",
            "consigne": self.consigne or "", "demandeur": self.demandeur_id.name or "",
            "demandeur_tel": self.demandeur_id.phone or "", "demandeur_email": self.demandeur_id.email or "",
            "contacts": [{"id": p.id, "name": p.name, "tel": p.phone or "", "email": p.email or ""} for p in self.contact_ids.sudo()],
            "pret": self.state in ("recue", "precision") and bool(self.equipe_id),
            "nb_fusion": len(self.fusion_ids),
            "precision": self.lieu_precision or "", "liste": self.liste or "",
            "categorie_id": self.categorie_id.id or False, "recurrence": bool(self.recurrence_id),
            "write": fields.Datetime.to_string(self.write_date),
            "create": fields.Datetime.to_string(self.create_date),
            "sup": self.superviseur_id.name or "",
            "unique": "%s-%s" % (self.id, self.write_date),
        }
        if last is not None:
            d["last"] = last
        return d

    def _tv_role(self, partner):
        if not partner:
            return ""
        if partner == self.env.ref("base.partner_root"):
            return _("Système")
        user = partner.user_ids[:1]
        if user:
            if user.has_group("service_travaux.group_superviseur"):
                return _("Superviseur")
            emp = user.employee_ids[:1]
            return emp.fonction or _("Équipe")
        emp = self.env["hr.employee"].sudo().search([("work_contact_id", "=", partner.id)], limit=1)
        return emp.fonction if emp else _("Demandeur")

    def _tv_messages(self):
        self.ensure_one()
        me = self.env.user.partner_id
        msgs = self.env["mail.message"].search([
            ("model", "=", self._name), ("res_id", "=", self.id),
            ("message_type", "in", ("comment", "notification", "email"))], order="date asc, id asc")
        out = []
        for m in msgs:
            images = [a for a in m.attachment_ids if (a.mimetype or "").startswith("image/")]
            others = m.attachment_ids - self.env["ir.attachment"].concat(*images)
            text = html2plaintext(m.body or "").strip()
            if not text and not images and not others:
                continue  # suivi de champs sans texte
            tag = False
            match = TAG_RE.match(text)
            if match:
                tag = match.group(1).upper()
                text = text[match.end():].strip()
            if m.message_type == "notification" and text.endswith(" créé") and len(text) < 60:
                text = _("Demande créée")
            if m.message_type == "email":
                kind, text = "sys", _("Message envoyé au demandeur : %s", m.subject or "")
            elif m.message_type == "notification" or (m.subtype_id and m.subtype_id.internal and not images):
                kind = "sys"
            else:
                kind = "bubble"
            out.append({
                "id": m.id, "kind": kind, "text": text,
                "date": fields.Datetime.to_string(m.date),
                "out": bool(m.author_id and m.author_id == me),
                "author": (m.author_id.name or "").split(" (")[0].split(" ")[0],
                "role": self._tv_role(m.author_id) if m.author_id else "",
                "images": [{"id": a.id, "url": "/web/image/%s" % a.id, "tag": tag or ""} for a in images],
                "files": [{
                    "id": a.id, "name": a.name, "mimetype": a.mimetype or "",
                    "audio": (a.mimetype or "").startswith("audio/") or a.name.startswith("message-vocal"),
                    "url": "/web/content/%s" % a.id, "download": "/web/content/%s?download=true" % a.id,
                    "size": a.file_size or 0,
                } for a in others],
                "tag": tag or "",
            })
        return out

    def _tv_last(self, messages):
        for m in reversed(messages):
            if m["kind"] == "bubble" or m["text"]:
                if m["images"]:
                    prev = _("Photo") + (" · " + m["text"] if m["text"] else "")
                elif m.get("files"):
                    f = m["files"][0]
                    prev = (_("Message vocal") if f["audio"] else _("Fichier : %s", f["name"])) + (" · " + m["text"] if m["text"] else "")
                else:
                    prev = m["text"]
                if m["out"] and m["kind"] == "bubble":
                    prev = _("Vous : ") + prev
                return {"text": prev[:120], "date": m["date"]}
        return {"text": "", "date": fields.Datetime.to_string(self.create_date)}

    # ------------------------------------------------------------ App terrain
    @api.model
    def tv_terrain_list(self):
        week_ago = fields.Datetime.now() - timedelta(days=7)
        recs = self.search([
            ("type_travail", "=", "demande"), ("state", "not in", ("transferee", "annulee")),
            "|", ("state", "!=", "close"), ("date_cloture", ">=", week_ago)])
        res = []
        for rec in recs:
            res.append(rec._tv_card(rec._tv_last(rec._tv_messages())))
        res.sort(key=lambda r: r["last"]["date"] or "", reverse=True)
        return {
            "items": res,
            "is_sup": self._tv_is_sup(),
            "me": self.env.user.name,
            "my_team": self._tv_my_team(),
            "scope": self._tv_scope(),
            "org": self._tv_org(), "company": self.env.company.name, "services": self.env["travaux.equipe"].tv_services(),
            "teams": self.env["travaux.equipe"].tv_teams(),
            "lieux": sorted(self.tv_lieux(), key=lambda l: -l["n"]), "rayon": RAYON_PROCHE,
            "categories": [{"id": c.id, "name": c.name} for c in self.env["travaux.categorie"].search(
                [("hors_competence", "=", False)])],
        }

    def _tv_scope(self):
        user = self.env.user
        if user.has_group("service_travaux.group_secretariat"):
            return "global"
        if user.has_group("service_travaux.group_superviseur"):
            return "sup"
        return "equipe"

    def _tv_org(self):
        """Organigramme visible : superviseur → équipes (limité à ce que l'utilisateur peut voir)."""
        scope = self._tv_scope()
        teams = self.env["travaux.equipe"].search([("is_pool", "=", False)])
        if scope == "sup":
            teams = teams.filtered(lambda t: self.env.user in t.responsable_ids)
        elif scope == "equipe":
            my = self.env.user.employee_ids[:1].equipe_jour_id
            teams = teams & my
        org = []
        for sup in teams.mapped("superviseur_id"):
            org.append({"sup_id": sup.id, "sup": sup.name,
                        "teams": [{"id": t.id, "name": t.name, "color": t.color_hex}
                                  for t in teams.filtered(lambda t: t.superviseur_id == sup)]})
        orphan = teams.filtered(lambda t: not t.superviseur_id)
        if orphan:
            org.append({"sup_id": 0, "sup": _("Sans superviseur"),
                        "teams": [{"id": t.id, "name": t.name, "color": t.color_hex} for t in orphan]})
        return org

    def _tv_timeline(self):
        """Chronologie de la demande, du signalement au demandeur informé."""
        self.ensure_one()
        ts = fields.Datetime.to_string
        order = ["recue", "planif", "cours", "valider", "close"]
        pos = order.index(self.state) if self.state in order else 0
        att_date = self.date_attribution
        if not att_date and pos >= 1:
            msg = self.message_ids.filtered(lambda m: "Attribuée à l'équipe" in (m.body or "")).sorted("date")[:1]
            att_date = msg.date if msg else False
        steps = [{
            "key": "recue", "label": _("Reçue"), "date": ts(self.create_date), "done": True,
            "detail": _("par %s", SOURCE_LABEL.get(self.source, self.source or "")) + (
                " · " + self.demandeur_id.name if self.demandeur_id else ""),
        }]
        if self.state == "annulee":
            steps.append({"key": "annulee", "label": _("Refusée") if self.annulation == "refus" else _("Annulée"),
                          "date": ts(self.write_date), "done": True, "detail": self.motif_annulation or ""})
            return steps
        if self.state == "transferee":
            steps.append({"key": "transferee", "label": _("Transférée"), "date": ts(self.write_date), "done": True,
                          "detail": self.service_transfert or ""})
            return steps
        steps += [
            {"key": "planif", "label": _("Attribuée"), "date": ts(att_date) if att_date else False, "done": pos >= 1,
             "detail": (_("à l'équipe %s", self.equipe_id.name) if self.equipe_id else "") + (
                 _(" par %s", self.attribue_par_id.name) if self.attribue_par_id else "")},
            {"key": "cours", "label": _("Démarrée"), "date": ts(self.date_debut) if self.date_debut else False,
             "done": pos >= 2 and bool(self.date_debut or pos > 2), "detail": self.equipe_id.name or ""},
            {"key": "valider", "label": _("Terminée"), "date": ts(self.date_fin) if self.date_fin else False,
             "done": pos >= 3, "detail": _("%s mesurées", self._fmt_heures(self.duree_mesuree)) if pos >= 3 else ""},
            {"key": "close", "label": _("Clôturée"), "date": ts(self.date_cloture) if self.date_cloture else False,
             "done": pos >= 4, "detail": _("par %s", self.cloture_par_id.name) if self.cloture_par_id else ""},
        ]
        if self.demandeur_id:
            informed = self.date_citoyen or (self.date_cloture if self.state == "close" and self.demandeur_id.email else False)
            steps.append({"key": "citoyen", "label": _("Demandeur informé"), "date": ts(informed) if informed else False,
                          "done": bool(informed), "detail": self.demandeur_id.name})
        current = next((i for i, st in enumerate(steps) if not st["done"]), len(steps))
        for i, st in enumerate(steps):
            st["current"] = i == current
        return steps

    def _tv_my_team(self):
        emp = self.env.user.employee_ids[:1]
        team = emp.equipe_jour_id if emp else False
        return {"id": team.id, "name": team.name} if team else False

    @api.model
    def tv_conversation(self, rec_id):
        rec = self.browse(rec_id).exists()
        if not rec:
            raise UserError(_("Cette demande n'existe plus."))
        messages = rec._tv_messages()
        card = rec._tv_card(rec._tv_last(messages))
        is_sup = self._tv_is_sup()
        card.update({
            "messages": messages,
            "can_start": rec.state == "planif",
            "can_finish": rec.state == "cours",
            "can_close": rec.state == "valider" and is_sup,
            "can_reopen": rec.state in ("valider", "close") and is_sup,
            "can_assign": rec.state in ("recue", "precision") and is_sup,
            "can_impute": rec.state in ("cours", "valider", "close", "planif") and bool(rec.equipe_id),
            "timeline": rec._tv_timeline(),
            "lieu_note": rec.lieu_id.note or "",
            "pointages": [{
                "id": p.id, "date": p.date.isoformat(), "equipe": p.equipe_id.name, "equipe_id": p.equipe_id.id,
                "h": p.heures, "origine": p.origine, "note": p.note or "",
            } for p in rec.pointage_ids.sorted("date")],
            "date_debut": fields.Datetime.to_string(rec.date_debut) if rec.date_debut else False,
            "date_fin": fields.Datetime.to_string(rec.date_fin) if rec.date_fin else False,
            "is_sup": is_sup,
        })
        return card

    @api.model
    def tv_post(self, rec_id, body):
        body = (body or "").strip()
        if not body:
            return self.tv_conversation(rec_id)
        self.browse(rec_id).message_post(body=plaintext2html(body), message_type="comment",
                                         subtype_xmlid="mail.mt_comment")
        return self.tv_conversation(rec_id)

    @api.model
    def tv_photo(self, rec_id, data_b64, kind="message", text=""):
        rec = self.browse(rec_id)
        if kind == "apres":
            rec.with_context(travaux_no_photo_post=True).photo_apres = data_b64
            rec.photo_apres  # force le redimensionnement
            rec._post_photo("photo_apres", "APRÈS", text or "")
        else:
            import base64
            rec.message_post(body=plaintext2html(text) if text else Markup("<p></p>"),
                             message_type="comment", subtype_xmlid="mail.mt_comment",
                             attachments=[("photo.jpg", base64.b64decode(data_b64))])
        return self.tv_conversation(rec_id)

    @api.model
    def tv_attach(self, rec_id, name, mimetype, data_b64, text=""):
        """Pièce jointe (document, plan, PDF…) ou message vocal dans la conversation."""
        rec = self.browse(rec_id)
        rec.check_access_rule("read")
        att = self.env["ir.attachment"].create({
            "name": name or "fichier", "datas": data_b64, "mimetype": mimetype or False,
            "res_model": rec._name, "res_id": rec.id,
        })
        rec.message_post(body=plaintext2html(text) if text else Markup(""), message_type="comment",
                         subtype_xmlid="mail.mt_comment", attachment_ids=[att.id])
        return self.tv_conversation(rec_id)

    @api.model
    def tv_do(self, rec_id, action, vals=None):
        rec = self.browse(rec_id)
        methods = {
            "demarrer": rec.action_demarrer, "terminer": rec.action_terminer,
            "cloturer": rec.action_cloturer, "rouvrir": rec.action_rouvrir,
            "transferer": rec.action_transferer, "precision": rec.action_demander_precision,
            "attribuer": rec.action_attribuer,
            "preparer": lambda: True,      # attribuer sans envoyer : l'équipe ne voit rien tant que ce n'est pas envoyé
            "desattribuer": lambda: rec.write({"equipe_id": False}),
        }
        if action not in methods:
            raise AccessError(_("Action inconnue."))
        if action in ("preparer", "desattribuer") and rec.state not in ("recue", "precision"):
            raise UserError(_("Cette demande est déjà envoyée à l'équipe."))
        droits = self._tv_droits()
        if action in ("cloturer", "rouvrir") and not self._tv_is_sup():
            raise AccessError(_("Réservé au superviseur."))
        if action in ("transferer", "precision") and not droits["bureau"]:
            raise AccessError(_("Réservé au secrétariat et aux superviseurs."))
        if action in ("preparer", "desattribuer") and not droits["peut_attribuer"]:
            raise AccessError(_("Dans l'organisation choisie, c'est le superviseur qui attribue les demandes."))
        if action == "attribuer" and not droits["peut_envoyer"]:
            raise AccessError(_("Dans l'organisation choisie, c'est le superviseur qui envoie le travail à l'équipe."))
        if vals:
            clean = {k: v for k, v in vals.items()
                     if k in ("equipe_id", "date_prevue", "priorite", "consigne", "duree_prevue", "service_transfert")}
            if "date_prevue" in clean and not clean["date_prevue"]:
                clean["date_prevue"] = False   # « sans date » : le superviseur planifiera
            if clean:
                rec.write(clean)
        methods[action]()
        return self.tv_conversation(rec_id)

    @api.model
    def tv_set_duree(self, rec_id, hours):
        """Correction de la durée mesurée (oubli de Terminé…) : ajuste la dernière mesure."""
        rec = self.browse(rec_id)
        hours = max(0.0, float(hours))
        mesures = rec.pointage_ids.filtered(lambda p: p.origine == "mesure").sorted("id")
        if mesures:
            other = sum((rec.pointage_ids - mesures[-1:]).mapped("heures"))
            mesures[-1:].write({"heures": max(0.0, hours - other), "note": _("corrigée")})
        elif rec.equipe_id:
            self.env["travaux.pointage"].create({
                "demande_id": rec.id, "equipe_id": rec.equipe_id.id, "heures": hours,
                "date": fields.Date.context_today(self), "origine": "manuel", "note": _("corrigée")})
        else:
            rec.duree_mesuree = hours
        return self.tv_conversation(rec_id)

    @api.model
    def tv_add_pointage(self, rec_id, vals):
        rec = self.browse(rec_id)
        hours = float(vals.get("h") or 0)
        if hours <= 0:
            raise UserError(_("Indiquez un nombre d'heures."))
        self.env["travaux.pointage"].create({
            "demande_id": rec.id, "equipe_id": int(vals.get("equipe_id") or rec.equipe_id.id),
            "date": vals.get("date") or fields.Date.context_today(self), "heures": hours,
            "origine": "manuel", "note": vals.get("note") or False})
        rec.message_post(body=_("%(h)s imputées à l'équipe %(e)s", h=self._fmt_heures(hours),
                                e=self.env["travaux.equipe"].browse(int(vals.get("equipe_id") or rec.equipe_id.id)).name),
                         message_type="notification", subtype_xmlid="mail.mt_note")
        return self.tv_conversation(rec_id)

    @api.model
    def tv_del_pointage(self, pointage_id):
        p = self.env["travaux.pointage"].browse(pointage_id)
        rec_id = p.demande_id.id
        p.unlink()
        return self.tv_conversation(rec_id)

    @api.model
    def tv_set_prevu(self, rec_id, hours):
        self.browse(rec_id).duree_prevue = max(0.0, float(hours or 0))
        _live(self.env, "demande", [rec_id])
        return self.tv_conversation(rec_id)

    @api.model
    def tv_signaler(self, vals):
        cat = self.env["travaux.categorie"].browse(vals.get("categorie_id") or [])
        rec = self.create({
            "titre": (vals.get("titre") or cat.name or _("Signalement")).strip(),
            "adresse": vals.get("adresse") or False,
            "description": vals.get("description") or False,
            "categorie_id": cat.id or False,
            "photo_avant": vals.get("photo") or False,
            "latitude": vals.get("lat") or 0.0, "longitude": vals.get("lng") or 0.0,
            "source": "app", "state": "recue", "type_travail": "demande",
        })
        if tag_format(vals.get("lieu")):
            self.tv_set_lieu(rec.id, vals["lieu"], True)
        for name in vals.get("objets") or []:
            if tag_format(name):
                self.tv_set_lieu(rec.id, name, True, "objet")
        return rec.id

    @api.model
    def tv_heures(self, team_id=False):
        """Heures de l'équipe (jamais par personne) : imputations de la semaine."""
        Team = self.env["travaux.equipe"]
        team = Team.browse(team_id) if team_id else self.env.user.employee_ids[:1].equipe_jour_id
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        pdom = [("date", ">=", monday), ("date", "<=", sunday)]
        if team:
            pdom.append(("equipe_id", "=", team.id))
        rows = [{"key": "p%s" % p.id, "id": p.demande_id.id, "ref": p.demande_id.name, "titre": p.demande_id.titre,
                 "type": p.demande_id.type_travail, "equipe": p.equipe_id.name, "date": p.date.isoformat(),
                 "h": p.heures, "origine": p.origine}
                for p in self.env["travaux.pointage"].search(pdom)]
        # anciennes mesures sans ligne d'imputation
        ldom = [("date_fin", ">=", fields.Datetime.to_datetime(monday)), ("pointage_ids", "=", False),
                ("duree_mesuree", ">", 0)]
        if team:
            ldom.append(("equipe_id", "=", team.id))
        for r in self.search(ldom):
            rows.append({"key": "d%s" % r.id, "id": r.id, "ref": r.name, "titre": r.titre, "type": r.type_travail,
                         "equipe": r.equipe_id.name, "date": fields.Date.context_today(r, r.date_fin).isoformat(),
                         "h": r.duree_mesuree, "origine": "mesure"})
        rows.sort(key=lambda x: x["date"], reverse=True)
        tdom = [("date_prevue", ">=", monday), ("date_prevue", "<=", sunday),
                ("state", "not in", ("recue", "precision", "transferee", "annulee"))]
        if team:
            tdom.append(("equipe_id", "=", team.id))
        tasks = self.search(tdom, order="date_prevue")
        return {
            "team": team.name if team else False, "team_id": team.id if team else False,
            "cap": team.capacite_hebdo if team else 0,
            "planned": sum(tasks.mapped("duree_prevue")),
            "rows": rows, "week": sum(x["h"] for x in rows),
            "tasks": [{"id": t.id, "label": "%s · %s" % (t.name, t.titre)} for t in tasks],
            "today": today.isoformat(),
        }

    # ------------------------------------------------------------ Demandes à traiter
    @api.model
    def tv_queue(self):
        since = fields.Datetime.now() - timedelta(days=2)
        recs = self.search([
            ("type_travail", "=", "demande"),
            "|", ("state", "in", ("recue", "precision")),
            "&", ("create_date", ">=", since), ("state", "in", ("planif", "transferee", "annulee"))],
            order="priorite desc, create_date desc")
        repeats = self._tv_repeats()
        items = []
        for r in recs:
            d = r._tv_card()
            d["suggest_id"] = r.categorie_id.equipe_defaut_id.id or False
            d["suggest"] = r.categorie_id.equipe_defaut_id.name or ""
            d["suggest_presence"] = r.categorie_id.equipe_defaut_id.presence_label or ""
            d["hors"] = r.categorie_id.hors_competence
            d["repeat"] = 0 if r.lieu_id else repeats.get(norm_adresse(r.adresse), 0)
            d["photos"] = r._tv_photos()
            d["doublons"] = r._tv_doublons() if r.state in ("recue", "precision") else []
            d["timeline"] = r._tv_timeline()
            d["service"] = r.categorie_id.service_competent or r.service_transfert or ""
            items.append(d)
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        planned = {}
        for t in self.search([("date_prevue", ">=", monday), ("date_prevue", "<=", monday + timedelta(days=4)),
                              ("state", "not in", ("recue", "precision", "transferee", "annulee")), ("equipe_id", "!=", False)]):
            planned[t.equipe_id.id] = planned.get(t.equipe_id.id, 0.0) + t.duree_prevue
        teams = [t for t in self.env["travaux.equipe"].tv_teams() if not t["is_pool"]]
        for t in teams:
            t["load"] = "%s h prévues / %s h" % (round(planned.get(t["id"], 0.0), 1), round(t["cap"]))
        return {"items": items, "teams": teams,
                "lieux": self.tv_lieux(), "is_sup": self._tv_is_sup(), "rayon": RAYON_PROCHE, "droits": self._tv_droits(),
                "center": self._tv_center(), "ville": ("%s %s" % (self.env.company.zip or "", self.env.company.city or "")).strip(),
                "categories": [{"id": c.id, "name": c.name} for c in self.env["travaux.categorie"].search([])],
                "today": fields.Date.context_today(self).isoformat()}

    # ------------------------------------------------------------ Suivi et planning
    @api.model
    def tv_suivi(self, week_offset=0):
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        friday = monday + timedelta(days=4)
        week_ago = fields.Datetime.now() - timedelta(days=7)
        jobs = self.search([
            ("type_travail", "=", "demande"), ("state", "in", ("recue", "precision", "planif", "cours", "valider", "close")),
            "|", ("state", "!=", "close"), ("date_cloture", ">=", week_ago)])
        tasks = self.search([("date_prevue", ">=", monday), ("date_prevue", "<=", friday),
                             ("state", "not in", ("recue", "precision", "transferee", "annulee"))])
        # heures déclarées (imputées) par équipe et par jour
        declared = {}
        for p in self.env["travaux.pointage"].search([("date", ">=", monday), ("date", "<=", friday)]):
            k = "%s|%s" % (p.equipe_id.id, p.date.isoformat())
            declared[k] = declared.get(k, 0.0) + p.heures
        for r in self.search([("date_fin", ">=", fields.Datetime.to_datetime(monday)),
                              ("date_fin", "<", fields.Datetime.to_datetime(friday + timedelta(days=1))),
                              ("pointage_ids", "=", False), ("equipe_id", "!=", False)]):
            k = "%s|%s" % (r.equipe_id.id, fields.Date.context_today(r, r.date_fin).isoformat())
            declared[k] = declared.get(k, 0.0) + r.duree_mesuree
        teams = []
        for t in self.env["travaux.equipe"].search([("is_pool", "=", False)]):
            d = t._tv_team_dict()
            d["people"] = ", ".join(t.jour_ids.filtered(lambda e: e.statut_jour == "present").mapped("name"))
            teams.append(d)
        return {
            "monday": monday.isoformat(), "today": today.isoformat(),
            "jobs": [j._tv_card() for j in jobs],
            "tasks": [t._tv_card() for t in tasks],
            "declared": declared,
            "teams": teams,
            "views": [{"id": v.id, "name": v.name, "teams": v.equipe_ids.ids, "mine": v.user_id == self.env.user,
                       "shared": v.partagee} for v in self.env["travaux.vue"].search([])],
            "scope": self._tv_scope(),
            "supervisors": self.env["travaux.equipe"].tv_supervisors(),
            "services": self.env["travaux.equipe"].tv_services(),
            "uid": self.env.uid, "is_sup": self._tv_is_sup(),
        }

    @api.model
    def tv_move_task(self, rec_id, equipe_id, date):
        rec = self.browse(rec_id)
        old = (rec.equipe_id.name, rec.date_prevue)
        rec.write({"equipe_id": equipe_id, "date_prevue": date})
        rec.message_post(body=_("Déplacée : %(e1)s %(d1)s → %(e2)s %(d2)s", e1=old[0] or "", d1=old[1] or "",
                                e2=rec.equipe_id.name, d2=rec.date_prevue),
                         message_type="notification", subtype_xmlid="mail.mt_note")
        return True

    @api.model
    def tv_save_view(self, name, equipe_ids, partagee=False):
        v = self.env["travaux.vue"].create({"name": (name or "").strip() or _("Ma vue"),
                                            "equipe_ids": [(6, 0, equipe_ids)], "partagee": bool(partagee)})
        return v.id

    @api.model
    def tv_del_view(self, view_id):
        self.env["travaux.vue"].browse(view_id).unlink()
        return True

    @api.model
    def tv_place(self, rec_id, equipe_id, date, hours=0):
        """Place une demande de la pile dans le planning : imputée à l'équipe, pour ce jour."""
        rec = self.browse(rec_id)
        vals = {"equipe_id": equipe_id, "date_prevue": date}
        if hours or not rec.duree_prevue:
            vals["duree_prevue"] = float(hours or 2.0)
        if rec.state in ("recue", "precision"):
            rec.write(vals)
            rec.action_attribuer()
        else:
            self.tv_move_task(rec_id, equipe_id, date)
            if "duree_prevue" in vals:
                rec.duree_prevue = vals["duree_prevue"]
        return True

    @api.model
    def tv_quick_task(self, vals):
        if not (vals.get("titre") or "").strip():
            raise UserError(_("Donnez un titre à la tâche."))
        rec = self.create({
            "titre": vals["titre"].strip(), "type_travail": vals.get("type") or "maintenance",
            "equipe_id": int(vals["equipe_id"]), "date_prevue": vals["date"],
            "duree_prevue": float(vals.get("h") or 0), "adresse": vals.get("adresse") or False,
            "state": "planif", "source": "planning",
        })
        rec._notify_team()
        return rec.id

    @api.model
    def tv_gen_tournees(self, monday):
        """Tournées récurrentes : recopie les tournées de la semaine précédente (même équipe, même jour)."""
        monday = fields.Date.to_date(monday)
        prev = self.search([("type_travail", "=", "tournee"),
                            ("date_prevue", ">=", monday - timedelta(days=7)),
                            ("date_prevue", "<", monday)])
        existing = {(t.equipe_id.id, t.date_prevue, t.titre) for t in self.search([
            ("type_travail", "=", "tournee"), ("date_prevue", ">=", monday),
            ("date_prevue", "<", monday + timedelta(days=7))])}
        vals_list = []
        for t in prev:
            day = t.date_prevue + timedelta(days=7)
            if (t.equipe_id.id, day, t.titre) in existing:
                continue
            vals_list.append({"titre": t.titre, "type_travail": "tournee", "equipe_id": t.equipe_id.id,
                              "date_prevue": day, "duree_prevue": t.duree_prevue or t.duree_mesuree,
                              "adresse": t.adresse, "state": "planif", "source": "planning"})
        self.create(vals_list)
        return len(vals_list)

    # ------------------------------------------------------------ Direction
    @api.model
    def tv_dashboard(self):
        if not (self.env.user.has_group("service_travaux.group_direction") or self._tv_is_sup()):
            raise AccessError(_("Réservé aux superviseurs et à la direction."))
        today = fields.Date.context_today(self)
        start = (today.replace(day=1) - timedelta(days=335)).replace(day=1)
        self.env.cr.execute("""
            SELECT equipe_id, type_travail, to_char(date_trunc('month', date_fin), 'YYYY-MM'),
                   COALESCE(SUM(duree_mesuree), 0), COUNT(*)
              FROM travaux_demande
             WHERE date_fin >= %s AND equipe_id IS NOT NULL
          GROUP BY 1, 2, 3
        """, (start,))
        rows = [{"team": r[0], "type": r[1], "month": r[2], "h": float(r[3]), "n": r[4]} for r in self.env.cr.fetchall()]
        self.env.cr.execute("""
            SELECT lieu_id, equipe_id, to_char(date_trunc('month', date_fin), 'YYYY-MM'),
                   COALESCE(SUM(duree_mesuree), 0), COUNT(*)
              FROM travaux_demande
             WHERE date_fin >= %s AND lieu_id IS NOT NULL
          GROUP BY 1, 2, 3
        """, (start,))
        lieu_rows = [{"lieu": r[0], "team": r[1], "month": r[2], "h": float(r[3]), "n": r[4]}
                     for r in self.env.cr.fetchall()]
        months = []
        m = start
        for _i in range(12):
            months.append(m.strftime("%Y-%m"))
            m = (m + timedelta(days=32)).replace(day=1)
        # Contrôle : présences (heures disponibles) par équipe et par mois / par semaine
        Presence = self.env["travaux.presence"]
        Presence.snapshot()
        self.env.cr.execute("""
            SELECT equipe_id, to_char(date_trunc('month', date), 'YYYY-MM'), SUM(heures_dispo), COUNT(*)
              FROM travaux_presence WHERE date >= %s GROUP BY 1, 2
        """, (start,))
        presence = [{"team": r[0], "month": r[1], "h": float(r[2] or 0), "days": r[3]} for r in self.env.cr.fetchall()]
        weeks_start = today - timedelta(days=today.weekday()) - timedelta(weeks=8)
        self.env.cr.execute("""
            SELECT equipe_id, to_char(date_trunc('week', date), 'YYYY-MM-DD'), SUM(heures_dispo)
              FROM travaux_presence WHERE date >= %s GROUP BY 1, 2
        """, (weeks_start,))
        pres_w = {(r[0], r[1]): float(r[2] or 0) for r in self.env.cr.fetchall()}
        self.env.cr.execute("""
            SELECT equipe_id, to_char(date_trunc('week', date_fin), 'YYYY-MM-DD'), SUM(duree_mesuree)
              FROM travaux_demande WHERE date_fin >= %s AND equipe_id IS NOT NULL GROUP BY 1, 2
        """, (weeks_start,))
        decl_w = {(r[0], r[1]): float(r[2] or 0) for r in self.env.cr.fetchall()}
        weekly = [{"team": k[0], "week": k[1], "presence": v, "declared": decl_w.get(k, 0.0)} for k, v in pres_w.items()]
        # Absentéisme : par équipe et par mois, d'après les présences enregistrées chaque jour
        self.env["hr.employee"]._travaux_sync_pointeuse()
        self.env.cr.execute("""
            SELECT equipe_id, to_char(date_trunc('month', date), 'YYYY-MM'),
                   SUM(nb_membres), SUM(LEAST(nb_presents, nb_membres)),
                   SUM(nb_conge), SUM(nb_formation), SUM(nb_recup), SUM(nb_absent),
                   SUM(CASE WHEN nb_presents < COALESCE(NULLIF(effectif_min, 0), 1) THEN 1 ELSE 0 END), COUNT(*)
              FROM travaux_presence WHERE date >= %s AND nb_membres > 0 GROUP BY 1, 2
        """, (start,))
        absences = [{"team": r[0], "month": r[1], "membres": r[2] or 0, "presents": r[3] or 0, "conge": r[4] or 0,
                     "formation": r[5] or 0, "recup": r[6] or 0, "absent": r[7] or 0, "sous_min": r[8] or 0, "jours": r[9]}
                    for r in self.env.cr.fetchall()]
        # Aujourd'hui : équipes disponibles et surcharges
        today_rows = []
        for t in self.env["travaux.equipe"].search([("is_pool", "=", False)]):
            tasks = self.search([("equipe_id", "=", t.id), ("date_prevue", "=", today),
                                 ("state", "not in", ("recue", "precision", "transferee", "annulee"))])
            presents = len(t.jour_ids.filtered(lambda e: e.statut_jour == "present"))
            dispo = presents * 7.6
            prevu = sum(tasks.mapped("duree_prevue"))
            today_rows.append({"team": t.id, "name": t.name, "color": t.color_hex, "presents": presents,
                               "membres": len(t.member_ids), "dispo": round(dispo, 1), "prevu": round(prevu, 1),
                               "reste": round(dispo - prevu, 1), "tasks": len(tasks)})
        return {
            "months": months,
            "teams": [t for t in self.env["travaux.equipe"].tv_teams() if not t["is_pool"]],
            "supervisors": self.env["travaux.equipe"].tv_supervisors(),
            "services": self.env["travaux.equipe"].tv_services(),
            "rows": rows, "lieu_rows": lieu_rows,
            "presence": presence, "weekly": weekly, "today_rows": today_rows, "absences": absences,
            "lieux": {l.id: {"id": l.id, "name": "#" + l.name, "adresse": l.adresse or ""}
                      for l in self.env["travaux.lieu"].with_context(active_test=False).search([])},
        }

    # ------------------------------------------------------------ Fiche : qui, quoi, où
    def _tv_set_demandeur(self, nom, tel, email):
        """Demandeur saisi directement dans la fiche : retrouve le contact par e-mail, sinon le crée ou le corrige."""
        self.ensure_one()
        nom, tel, email = (nom or "").strip(), (tel or "").strip(), (email or "").strip().lower()
        Partner = self.env["res.partner"].sudo()
        if not (nom or tel or email):
            self.demandeur_id = False
            return
        cur = self.demandeur_id.sudo()
        found = Partner.search([("email", "=ilike", email)], limit=1) if email else Partner
        if found and found != cur:
            self.demandeur_id = found.id
            cur = found
        vals = {"name": nom or email or tel, "phone": tel or False, "email": email or False}
        # on ne modifie pas la fiche d'un utilisateur de l'application ni d'un contact partagé avec d'autres demandes
        shared = cur and (cur.user_ids or self.sudo().search_count([("demandeur_id", "=", cur.id), ("id", "!=", self.id)]))
        if cur and not shared:
            cur.write(vals)
        elif cur and (cur.name, cur.phone or "", (cur.email or "").lower()) == (vals["name"], tel, email):
            pass
        elif cur and shared and found:
            cur.write({k: v for k, v in vals.items() if v and not cur[k]})
        else:
            self.demandeur_id = Partner.create(vals).id

    @api.model
    def tv_save_demande(self, rec_id, vals):
        """Crée ou corrige une demande depuis l'écran « Demandes à traiter » : qui, quoi, où."""
        if not (self._tv_is_sup() or self.env.user.has_group("service_travaux.group_secretariat")):
            raise AccessError(_("Réservé au secrétariat et aux superviseurs."))
        titre = (vals.get("titre") or "").strip()
        if not titre:
            raise UserError(_("Indiquez ce dont il s'agit (le titre de la demande)."))
        data = {"titre": titre, "description": (vals.get("description") or "").strip() or False,
                "lieu_precision": (vals.get("precision") or "").strip() or False,
                "categorie_id": int(vals.get("categorie_id") or 0) or False}
        if "urgent" in vals:
            data["priorite"] = "1" if vals.get("urgent") else "0"
        adresse = (vals.get("adresse") or "").strip()
        if rec_id:
            rec = self.browse(rec_id)
            if adresse != (rec.adresse or ""):
                data["adresse"] = adresse or False
            rec.write(data)
        else:
            rec = self.create(dict(data, adresse=adresse or False, state="recue", type_travail="demande",
                                   source=vals.get("source") or "guichet"))
        rec._tv_set_demandeur(vals.get("demandeur"), vals.get("demandeur_tel"), vals.get("demandeur_email"))
        _live(self.env, "demande", [rec.id])
        return rec.id

    # ------------------------------------------------------------ Photos et position
    def _tv_photos(self):
        """Toutes les photos de la demande, la photo principale en premier."""
        self.ensure_one()
        atts = self.env["ir.attachment"].search([
            ("res_model", "=", self._name), ("res_id", "=", self.id), ("mimetype", "=like", "image/%")], order="id")
        apres = atts.filtered(lambda a: "photo_apres" in (a.name or ""))
        main = self.photo_main_id if self.photo_main_id in atts else (atts - apres)[:1]
        if not self.photo_avant:
            main = atts.browse()
        out = [{"id": a.id, "url": "/web/image/%s" % a.id, "thumb": "/web/image/%s/160x160" % a.id,
                "main": a == main, "apres": a in apres} for a in (main | (atts - main))]
        if self.photo_avant and not main:  # photo encodée dans la fiche, sans pièce jointe
            out.insert(0, {"id": 0, "url": "/web/image/travaux.demande/%s/photo_avant" % self.id,
                           "thumb": "/web/image/travaux.demande/%s/photo_avant/160x160" % self.id,
                           "main": True, "apres": False})
        return out

    @api.model
    def _tv_center(self):
        """Centre par défaut de la carte : moyenne des dernières demandes localisées."""
        recs = self.sudo().search([("latitude", "!=", 0), ("longitude", "!=", 0)], limit=40, order="id desc")
        if recs:
            return [sum(recs.mapped("latitude")) / len(recs), sum(recs.mapped("longitude")) / len(recs)]
        lieu = self.env["travaux.lieu"].search([("latitude", "!=", 0)], limit=1)
        return [lieu.latitude, lieu.longitude] if lieu else [50.5039, 4.4699]

    @api.model
    def tv_set_main_photo(self, rec_id, att_id):
        """La photo choisie devient la photo principale (celle des listes et du kanban)."""
        rec = self.browse(rec_id)
        att = self.env["ir.attachment"].browse(int(att_id)).exists()
        if not att or att.res_model != rec._name or att.res_id != rec.id or not (att.mimetype or "").startswith("image/"):
            raise UserError(_("Cette photo n'appartient pas à la demande."))
        rec.with_context(travaux_no_photo_post=True).write({"photo_avant": att.datas, "photo_main_id": att.id})
        _live(self.env, "demande", [rec.id])
        return rec._tv_photos()

    @api.model
    def tv_add_photo(self, rec_id, data_b64):
        import base64
        rec = self.browse(rec_id)
        msg = rec.message_post(body=Markup("<p></p>"), message_type="comment", subtype_xmlid="mail.mt_comment",
                               attachments=[("photo_%s.jpg" % rec.name, base64.b64decode(data_b64))])
        if not rec.photo_avant and msg.attachment_ids:
            rec.with_context(travaux_no_photo_post=True).write({
                "photo_avant": data_b64, "photo_main_id": msg.attachment_ids[:1].id})
        return rec._tv_photos()

    @api.model
    def tv_set_position(self, rec_id, lat, lng, adresse=None):
        """Repositionne le point sur la carte et, si elle est donnée, corrige l'adresse."""
        if not (self._tv_is_sup() or self.env.user.has_group("service_travaux.group_secretariat")):
            raise AccessError(_("Réservé au secrétariat et aux superviseurs."))
        rec = self.browse(rec_id)
        vals = {"latitude": float(lat or 0), "longitude": float(lng or 0)}
        if adresse is not None and (adresse or "").strip() != (rec.adresse or ""):
            vals["adresse"] = (adresse or "").strip() or False
        rec.write(vals)
        _live(self.env, "demande", [rec.id])
        return True

    # ------------------------------------------------------------ # Lieux
    @api.model
    def tv_lieux(self):
        counts = {lieu.id: n for lieu, n in self.env["travaux.demande"]._read_group(
            [("lieu_id", "!=", False)], ["lieu_id"], ["__count"])}
        self.env.cr.execute("SELECT objet_id, COUNT(*) FROM travaux_demande_objet_rel GROUP BY 1")
        counts.update(dict(self.env.cr.fetchall()))
        return [{"id": l.id, "name": l.name, "adresse": l.adresse or "", "n": counts.get(l.id, 0), "kind": l.kind,
                 "lat": l.latitude or False, "lng": l.longitude or False, "state": l.state}
                for l in self.env["travaux.lieu"].search([])]

    @api.model
    def _tv_repeats(self):
        """Nombre de demandes par adresse (normalisée) qui n'ont pas encore de #."""
        out = {}
        for adresse, n in self._read_group(
                [("lieu_id", "=", False), ("adresse", "!=", False), ("type_travail", "=", "demande")],
                ["adresse"], ["__count"]):
            k = norm_adresse(adresse)
            out[k] = out.get(k, 0) + n
        return out

    @api.model
    def tv_set_lieu(self, rec_id, name, force=False, kind="lieu"):
        """Pose un # sur une demande : un seul # lieu (où), plusieurs # objets (quoi).

        Un # inconnu est créé : figé par un superviseur, sinon « proposé ». Tant que ``force`` est faux,
        un nom qui ressemble à un # existant (ou tout proche sur la carte) n'est pas créé : la liste des
        # ressemblants est renvoyée pour éviter les doublons.
        """
        rec = self.browse(rec_id)
        name = tag_format(name)
        Lieu = self.env["travaux.lieu"]
        if not name:
            if kind == "lieu":
                rec.lieu_id = False
            _live(self.env, "demande", [rec.id])
            return {"lieu": "", "tagged": 0}
        lieu = Lieu._by_name(name)
        created = False
        if not lieu:
            similar = Lieu._similar(name, rec.latitude, rec.longitude, kind)
            if similar and not force:
                return {"similar": similar, "name": name}
            vals = {"name": name, "kind": kind, "propose_par_id": self.env.uid,
                    "state": "valide" if self._tv_is_sup() else "propose"}
            if kind == "lieu":
                vals.update(adresse=rec.adresse or False, latitude=rec.latitude, longitude=rec.longitude)
            lieu = Lieu.sudo().create(vals)
            created = True
        if lieu.kind == "objet":
            if lieu not in rec.objet_ids:
                rec.objet_ids = [(4, lieu.id)]
            tagged = len(lieu.demande_objet_ids)
        else:
            if rec.lieu_id != lieu:
                rec.lieu_id = lieu.id
            tagged = self.search_count([("lieu_id", "=", lieu.id)])
        _live(self.env, "demande", [rec.id])
        return {"lieu": "#" + lieu.name, "lieu_id": lieu.id, "state": lieu.state, "created": created,
                "kind": lieu.kind, "tagged": tagged}

    @api.model
    def tv_remove_objet(self, rec_id, objet_id):
        self.browse(rec_id).objet_ids = [(3, int(objet_id))]
        _live(self.env, "demande", [rec_id])
        return True

    @api.model
    def tv_lieu_action(self, lieu_id, action, value=False):
        """Figer / renommer / grouper / supprimer un # : réservé aux superviseurs."""
        Lieu = self.env["travaux.lieu"]
        lieu = Lieu.browse(lieu_id).exists()
        if not lieu:
            raise UserError(_("Ce # n'existe plus."))
        lieu._check_sup()
        if action == "figer":
            lieu.action_figer()
        elif action == "renommer":
            other = Lieu._by_name(value)
            if other and other != lieu:
                raise UserError(_("#%s existe déjà : utilisez « Grouper » pour réunir les deux.", other.name))
            lieu.name = value
        elif action == "grouper":
            lieu.action_fusionner(Lieu.browse(int(value)).exists())
        elif action == "supprimer":
            lieu.unlink()
        else:
            raise UserError(_("Action inconnue."))
        _live(self.env, "demande", [])
        return True
