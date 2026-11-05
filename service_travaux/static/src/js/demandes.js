/** @odoo-module **/
import { Component, useState, useRef, onWillStart, onPatched, onMounted, onWillUnmount } from "@odoo/owl";
import { TvTimeline, TvHelp } from "./components";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useTravauxLive, chatTime, imgUrl, d, loadLeaflet, suggestLieux, tagKey, readImage } from "./common";

const JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."];

export class TravauxDemandes extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.mapRef = useRef("map");
        this.ui = useState(useService("ui"));
        this.imgUrl = imgUrl;
        this.help = {
            text: "Toutes les demandes arrivent ici (guichet, e-mail, téléphone, app terrain). Pour chacune :",
            steps: [
                "Lisez le message, regardez la photo et le lieu sur la carte.",
                "Posez les # : le lieu (#ÉcoleDuCentre) et ce qui est concerné (#Banc, #Chaufferie). Puis choisissez l'équipe, le jour et le temps prévu.",
                "Cliquez « Attribuer et envoyer » : l'équipe la reçoit aussitôt dans son app, le demandeur est prévenu.",
            ],
        };
        this.chatTime = chatTime;
        this.state = useState({
            data: { items: [], teams: [] },
            tab: "todo",
            refus: null,
            selId: null,
            mView: "list",
            form: {},
            busy: false,
            mapFailed: false,
            lieuEdit: false,   // false | "set" | "rename" | "group"
            lieuName: "",
            lieuSimilar: null,
            lieuTarget: "",
            objEdit: false,
            objName: "",
            objSimilar: null,
            drop: null,        // fenêtre « Déposer un document » : { files, text, busy, over, error, result }
            ia: { ready: false, can_config: false },
            reply: null,       // réponse au contact : { message, busy }
            transfert: null,   // fenêtre « hors compétence » : { contacts, contact_id, email, sujet, message, pdf_url }
            fusion: null,      // doublon à fusionner : { id, ref, titre, prevenir, pick }
            edit: null,        // correction ou création : { id, titre, description, adresse, … }
            photoIdx: 0,       // photo affichée dans la fiche
            lightbox: null,    // index de la photo agrandie
            pos: null,         // déplacement du point : { lat, lng, adresse, busy, searching, note }
        });
        this.fileRef = useRef("dropfile");
        this.photoRef = useRef("addphoto");
        this.onKey = (ev) => this.onLightboxKey(ev);
        onMounted(() => window.addEventListener("keydown", this.onKey));
        onWillUnmount(() => window.removeEventListener("keydown", this.onKey));
        onWillStart(async () => {
            await this.load();
            // ouverture depuis « Suivi et planning » : la demande à planifier est déjà sélectionnée
            const ctx = (this.props.action && this.props.action.context) || {};
            const r = ctx.active_id && this.state.data.items.find((x) => x.id === ctx.active_id);
            if (r) {
                this.state.tab = r.pret ? "pret" : ["recue", "precision"].includes(r.state) ? "todo" : "all";
                this.select(r.id);
            }
        });
        onMounted(() => this.renderMap());
        onPatched(() => this.renderMap());
        useTravauxLive(() => this.load(true), ["demande", "equipes", "poll"]);
    }
    // ------------------------------------------------------------ déposer un mail, un PDF, une photo
    async openDrop(files) {
        this.state.drop = { files: [], text: "", busy: false, over: false, error: "", result: null };
        this.orm.call("travaux.demande", "tv_ia_status", []).then((st) => (this.state.ia = st));
        if (files && files.length) await this.addDropFiles(files);
    }
    closeDrop() {
        if (this.state.drop && this.state.drop.busy) return;
        this.state.drop = null;
    }
    onPageDragOver(ev) {
        if (!ev.dataTransfer || ![...ev.dataTransfer.types].some((t) => t === "Files" || t === "text/plain")) return;
        ev.preventDefault();
        if (!this.state.drop) this.openDrop();
        if (this.state.drop) this.state.drop.over = true;
    }
    onDropLeave() {
        if (this.state.drop) this.state.drop.over = false;
    }
    async onDrop(ev) {
        ev.preventDefault();
        if (!this.state.drop) await this.openDrop();
        const drop = this.state.drop;
        drop.over = false;
        const dt = ev.dataTransfer;
        if (dt.files && dt.files.length) {
            await this.addDropFiles(dt.files);
        } else {
            const txt = dt.getData("text/plain");
            if (txt) drop.text = (drop.text ? drop.text + "\n\n" : "") + txt;
        }
    }
    pickDropFiles() {
        this.fileRef.el.click();
    }
    async onDropPicked(ev) {
        await this.addDropFiles(ev.target.files);
        ev.target.value = "";
    }
    async addDropFiles(fileList) {
        const drop = this.state.drop;
        drop.error = "";
        drop.result = null;
        for (const f of [...fileList].slice(0, 8)) {
            if (f.size > 15 * 1024 * 1024) {
                drop.error = `« ${f.name} » est trop lourd (15 Mo maximum).`;
                continue;
            }
            const data = await new Promise((resolve, reject) => {
                const rd = new FileReader();
                rd.onload = () => resolve(String(rd.result).split(",")[1] || "");
                rd.onerror = reject;
                rd.readAsDataURL(f);
            });
            drop.files.push({ name: f.name, type: f.type, size: f.size, data });
        }
    }
    removeDropFile(i) {
        this.state.drop.files.splice(i, 1);
    }
    dropIcon(f) {
        const n = f.name.toLowerCase();
        if (n.endsWith(".eml") || n.endsWith(".msg")) return "fa-envelope-o";
        if (n.endsWith(".pdf")) return "fa-file-pdf-o";
        if (f.type.startsWith("image/")) return "fa-picture-o";
        return "fa-file-o";
    }
    dropSize(f) {
        return f.size > 1048576 ? (f.size / 1048576).toFixed(1) + " Mo" : Math.max(1, Math.round(f.size / 1024)) + " ko";
    }
    async sendDrop(force = false) {
        const drop = this.state.drop;
        if (!drop.files.length && !drop.text.trim()) return;
        drop.busy = true;
        drop.error = "";
        try {
            const res = await this.orm.call("travaux.demande", "tv_ia_import",
                [drop.files.map((f) => ({ name: f.name, type: f.type, data: f.data })), drop.text],
                { context: force === true ? { tv_ia_force: true } : {} });
            if (res.not_request) {
                drop.result = res;
                return;
            }
            this.state.drop = null;
            this.state.tab = "todo";
            await this.load(true);
            this.select(res.id);
            this.notification.add(
                res.ia ? `${res.ref} créée par l'assistant : « ${res.titre} ». Vérifiez la fiche avant d'attribuer.`
                       : `${res.ref} importée sans assistant IA : complétez la fiche.`,
                { type: res.ia ? "success" : "warning" });
        } catch (e) {
            drop.error = (e && e.message && e.message.data && e.message.data.message) || (e && e.data && e.data.message) || "Le document n'a pas pu être lu.";
        } finally {
            if (this.state.drop) this.state.drop.busy = false;
        }
    }
    openIaConfig() {
        this.action.doAction("service_travaux.action_ia_config");
    }
    async markVerified() {
        await this.orm.call("travaux.demande", "tv_ia_verifie", [this.current.id]);
        await this.load(true);
    }
    async load(keepForm = false) {
        this.state.data = await this.orm.call("travaux.demande", "tv_queue", []);
        const cur = this.current;
        if (!cur) {
            const first = this.list[0] || this.state.data.items[0];
            if (first) this.select(first.id);
        } else if (!keepForm) {
            this.select(cur.id);
        }
    }
    get urgentCount() {
        return this.state.data.items.filter((r) => r.urgent && r.state === "recue").length;
    }
    get pending() {
        return this.state.data.items.filter((r) => ["recue", "precision"].includes(r.state));
    }
    get list() {
        const t = this.state.tab;
        return this.state.data.items.filter((r) =>
            t === "all" ? true : t === "urgent" ? r.urgent && r.state === "recue"
                : t === "pret" ? r.pret : !r.pret && (r.state === "recue" || r.state === "precision"));
    }
    get tabs() {
        const items = this.state.data.items;
        return [
            ["todo", "À attribuer", items.filter((r) => !r.pret && ["recue", "precision"].includes(r.state)).length],
            ["pret", "À planifier", items.filter((r) => r.pret).length],
            ["urgent", "Urgentes", items.filter((r) => r.urgent && r.state === "recue").length],
            ["all", "Toutes récentes", items.length],
        ];
    }
    get current() {
        return this.state.data.items.find((r) => r.id === this.state.selId);
    }
    get droits() {
        return this.state.data.droits || { organisation: "fusion", peut_attribuer: true, peut_envoyer: true, bureau: true };
    }
    get dayOptions() {
        const out = [{ v: "", label: "Sans date (à planifier)" }];
        let x = d(this.state.data.today);
        if (!x) return out;
        let i = 0;
        while (out.length < 7) {
            if (x.weekday <= 5) {
                const label = i === 0 ? "Aujourd'hui" : i === 1 ? "Demain" : JOURS[x.weekday - 1];
                out.push({ v: x.toISODate(), label: `${label} (${JOURS[x.weekday - 1]} ${x.day})` });
            }
            x = x.plus({ days: 1 });
            i++;
        }
        return out;
    }
    teamLabel(t) {
        return `${t.name} · ${t.presence}`;
    }
    stateChip(r) {
        if (r.state === "planif") return ["→ " + r.equipe, "background:#E3F1E8;color:#14532D"];
        if (r.pret) return [r.equipe + " · à envoyer", "background:#FFF4E5;color:#9A3412"];
        if (r.state === "transferee") return ["Transférée", "background:#EEF1F2;color:#3B454D"];
        if (r.state === "precision") return ["Précision demandée", "background:#FFF4E5;color:#9A3412"];
        return ["À attribuer", "background:#F1E9FB;color:#5B21B6"];
    }
    setTab(t) {
        this.state.tab = t;
    }
    /** Appui sur une demande de la liste : sur smartphone, on passe de la liste à la fiche. */
    openItem(id) {
        this.select(id);
        this.state.mView = "detail";
    }
    backToList() {
        this.state.mView = "list";
    }
    get mClass() {
        if (!this.ui.isSmall) return "";
        return this.state.mView === "detail" && this.state.selId ? "o_tv_m_detail" : "o_tv_m_list";
    }
    select(id) {
        const r = this.state.data.items.find((x) => x.id === id);
        if (!r) return;
        const opts = this.dayOptions;
        if (this.state.selId !== id) this.state.refus = null;
        this.state.selId = id;
        this.state.form = {
            equipe_id: String(r.equipe_id || r.suggest_id || ""),
            // rôles séparés : le secrétariat attribue sans date, le superviseur planifie
            date: r.date_prevue || (r.pret || !this.droits.peut_envoyer || opts.length < 2 ? "" : opts[r.urgent ? 1 : Math.min(2, opts.length - 1)].v),
            priorite: r.urgent ? "1" : "0",
            consigne: r.consigne || "",
            h: r.duree_prevue ? String(r.duree_prevue) : "2",
        };
        this.cancelLieu();
        this.cancelObj();
        this.state.photoIdx = 0;
        this.state.lightbox = null;
        this.state.pos = null;
        if (this.state.edit && this.state.edit.id) this.state.edit = null;
        this.state.fusion = null;
        this.state.reply = null;
        if (this.mapRef.el && this.mapRef.el._tvMap) {
            this.mapRef.el._tvMap.remove();
            this.mapRef.el._tvMap = null;
        }
    }
    isTeamSel(id) {
        return String(id) === String(this.state.form.equipe_id);
    }
    onTeam(ev) {
        this.state.form.equipe_id = ev.target.value;
    }
    // ------------------------------------------------------------ # lieu
    get isSup() {
        return !!this.state.data.is_sup;
    }
    get pendingTags() {
        return (this.state.data.lieux || []).filter((l) => l.state === "propose").length;
    }
    /** # proposés : ceux qui correspondent au texte tapé, sinon ceux aux alentours de la demande. */
    get lieuSuggest() {
        const r = this.current;
        if (!r) return [];
        return suggestLieux(this.lieuxOnly, {
            q: this.state.lieuEdit === "set" ? this.state.lieuName : "", lat: r.lat, lng: r.lng,
            rayon: this.state.data.rayon, max: 5,
        }).filter((l) => l.id !== r.lieu_id);
    }
    get lieuIsNew() {
        const key = tagKey(this.state.lieuName);
        return !!key && !(this.state.data.lieux || []).some((l) => tagKey(l.name) === key);
    }
    get lieuxOnly() {
        return (this.state.data.lieux || []).filter((l) => l.kind === "lieu");
    }
    get otherLieux() {
        const r = this.current;
        return this.lieuxOnly.filter((l) => l.id !== (r && r.lieu_id));
    }
    // ------------------------------------------------------------ # objets (plusieurs par demande)
    get objSuggest() {
        const r = this.current;
        if (!r) return [];
        const have = (r.objets || []).map((o) => o.id);
        const key = tagKey(this.state.objName);
        return (this.state.data.lieux || [])
            .filter((l) => l.kind === "objet" && !have.includes(l.id) && (!key || tagKey(l.name).includes(key)))
            .sort((a, b) => b.n - a.n)
            .slice(0, 8);
    }
    get objIsNew() {
        const key = tagKey(this.state.objName);
        return !!key && !(this.state.data.lieux || []).some((l) => tagKey(l.name) === key);
    }
    editObj() {
        this.state.objEdit = true;
        this.state.objName = "";
        this.state.objSimilar = null;
    }
    cancelObj() {
        this.state.objEdit = false;
        this.state.objName = "";
        this.state.objSimilar = null;
    }
    onObjInput(ev) {
        this.state.objName = ev.target.value.replace(/^#+/, "");
        this.state.objSimilar = null;
    }
    onObjKey(ev) {
        if (ev.key === "Enter") this.addObj(this.state.objName);
        if (ev.key === "Escape") this.cancelObj();
    }
    async addObj(name, force = false) {
        const r = this.current;
        if (!r || !(name || "").trim()) return;
        const res = await this.orm.call("travaux.demande", "tv_set_lieu", [r.id, name, force, "objet"]);
        if (res.similar) {
            this.state.objName = res.name;
            this.state.objSimilar = res.similar;
            return;
        }
        this.state.objName = "";
        this.state.objSimilar = null;
        if (res.created && res.state === "propose") {
            this.notification.add(`${res.lieu} est proposé : un superviseur doit le figer.`, { type: "info" });
        }
        await this.load(true);
    }
    async removeObj(id) {
        await this.orm.call("travaux.demande", "tv_remove_objet", [this.current.id, id]);
        await this.load(true);
    }
    async figerObj(id) {
        await this.orm.call("travaux.demande", "tv_lieu_action", [id, "figer"]);
        await this.load(true);
    }
    editLieu(mode) {
        const r = this.current;
        this.state.lieuSimilar = null;
        this.state.lieuTarget = "";
        this.state.lieuName = mode === "rename" && r ? r.lieu.replace("#", "") : "";
        this.state.lieuEdit = mode || "set";
    }
    cancelLieu() {
        this.state.lieuEdit = false;
        this.state.lieuName = "";
        this.state.lieuSimilar = null;
    }
    onLieuInput(ev) {
        this.state.lieuName = ev.target.value.replace(/^#+/, "");
        this.state.lieuSimilar = null;
    }
    onLieuKey(ev) {
        if (ev.key === "Enter") this.saveLieu();
        if (ev.key === "Escape") this.cancelLieu();
    }
    onLieuTarget(ev) {
        this.state.lieuTarget = ev.target.value;
    }
    async saveLieu(force = false) {
        const r = this.current;
        const name = (this.state.lieuName || "").trim();
        if (!r || !name) return;
        if (this.state.lieuEdit === "rename") return this.lieuAction("renommer", name);
        await this.applyLieu(name, force === true);
    }
    async applyLieu(name, force = false) {
        const r = this.current;
        const res = await this.orm.call("travaux.demande", "tv_set_lieu", [r.id, name, force]);
        if (res.similar) {
            this.state.lieuEdit = "set";
            this.state.lieuName = res.name;
            this.state.lieuSimilar = res.similar;
            return;
        }
        this.cancelLieu();
        const msg = res.state === "propose"
            ? `${res.lieu} est proposé : un superviseur doit le figer avant qu'il soit suggéré aux autres.`
            : `${res.lieu} : ${res.tagged} ${res.tagged > 1 ? "travaux rattachés" : "travail rattaché"}. ` +
              "Les prochaines demandes à cette adresse le recevront automatiquement.";
        this.notification.add(msg, { type: res.state === "propose" ? "info" : "success" });
        await this.load(true);
    }
    async clearLieu() {
        const r = this.current;
        if (!r) return;
        await this.orm.call("travaux.demande", "tv_set_lieu", [r.id, ""]);
        this.cancelLieu();
        await this.load(true);
    }
    async lieuAction(action, value = false) {
        const r = this.current;
        if (!r || !r.lieu_id) return;
        if (action === "supprimer" && !window.confirm(`Supprimer ${r.lieu} ? Les travaux restent, sans #.`)) return;
        if (action === "grouper" && !value) return;
        await this.orm.call("travaux.demande", "tv_lieu_action", [r.lieu_id, action, value]);
        this.cancelLieu();
        await this.load(true);
    }
    openLieux() {
        this.action.doAction("service_travaux.action_lieux", { additionalContext: { search_default_a_confirmer: 1 } });
    }
    onH(ev) {
        this.state.form.h = ev.target.value;
    }
    get teamLoad() {
        const t = this.state.data.teams.find((x) => String(x.id) === String(this.state.form.equipe_id));
        return t ? t.load : null;
    }
    onDate(ev) {
        this.state.form.date = ev.target.value;
    }
    setPrio(p) {
        this.state.form.priorite = p;
    }
    next() {
        const n = this.pending.find((r) => r.id !== this.state.selId);
        if (n) this.select(n.id);
    }
    startRefus() {
        this.state.refus = { motif: "" };
    }
    async confirmRefus() {
        const r = this.current;
        this.state.busy = true;
        try {
            await this.orm.call("travaux.demande", "tv_refuser", [r.id, this.state.refus.motif]);
            this.state.refus = null;
            this.notification.add(`${r.ref} refusée : le demandeur est prévenu.`, { type: "success" });
            await this.load(true);
        } finally {
            this.state.busy = false;
        }
    }
    async run(action) {
        const r = this.current;
        const f = this.state.form;
        const vals = action === "attribuer" || action === "preparer"
            ? { equipe_id: parseInt(f.equipe_id) || false, date_prevue: f.date || false, priorite: f.priorite, consigne: f.consigne,
                duree_prevue: parseFloat(String(f.h).replace(",", ".")) || 0 }
            : {};
        if ((action === "attribuer" || action === "preparer") && !vals.equipe_id) {
            this.notification.add("Choisissez une équipe.", { type: "warning" });
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("travaux.demande", "tv_do", [r.id, action, vals]);
            await this.load(true);
            if (action === "preparer") {
                const t = this.state.data.teams.find((x) => x.id === vals.equipe_id);
                this.notification.add(`${r.ref} est attribuée à ${t ? t.name : "l'équipe"}` +
                    (this.droits.peut_envoyer ? ", pas encore envoyée." : " : le superviseur la planifie et l'envoie."), { type: "success" });
            }
        } finally {
            this.state.busy = false;
        }
    }
    get doneInfo() {
        const r = this.current;
        if (!r || r.state === "recue") return null;
        if (r.state === "planif") {
            const day = this.dayOptions.find((o) => o.v === r.date_prevue);
            return {
                title: "Envoyée à l'équipe " + r.equipe,
                text: `L'équipe la reçoit tout de suite dans son application, avec la photo, l'adresse et la consigne. ` +
                    `Intervention prévue ${day ? day.label.toLowerCase() : r.date_prevue}${r.urgent ? ", en priorité" : ""}. ` +
                    (r.demandeur ? "Le demandeur est prévenu que sa demande est prise en charge." : ""),
            };
        }
        if (r.state === "annulee") {
            return { title: "Demande refusée", text: (r.motif ? "Motif : " + r.motif + ". " : "") + "Le demandeur reçoit le motif par message." };
        }
        if (r.state === "transferee") {
            return { title: "Transférée au service compétent", text: (r.service ? r.service + ". " : "") + "Le demandeur reçoit un message expliquant quel service va traiter sa demande." };
        }
        if (r.state === "precision") {
            return { title: "Précision demandée", text: "Un message est envoyé au demandeur ; la demande reste dans cette liste en attendant sa réponse.", again: true };
        }
        return null;
    }
    openForm(id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "travaux.demande", res_id: id || false,
            views: [[false, "form"]], target: "current",
            context: { default_state: "recue", default_type_travail: "demande", default_source: "guichet" },
        });
    }
    openTerrain() {
        this.action.doAction("service_travaux.action_client_terrain", { additionalContext: { active_id: this.state.selId } });
    }
    // ------------------------------------------------------------ hors compétence : PDF + mail
    async openTransfert() {
        const r = this.current;
        const p = await this.orm.call("travaux.demande", "tv_transfert_prepare", [r.id]);
        this.state.transfert = { ...p, id: r.id, contact_id: p.contact_id || "", email: "", busy: false };
    }
    closeTransfert() {
        if (this.state.transfert && this.state.transfert.busy) return;
        this.state.transfert = null;
    }
    onTransfertContact(ev) {
        const v = ev.target.value;
        this.state.transfert.contact_id = v === "other" ? "other" : parseInt(v) || "";
    }
    isTransfertContact(id) {
        return this.state.transfert.contact_id === id;
    }
    get transfertReady() {
        const tr = this.state.transfert;
        return tr.contact_id === "other" ? /\S+@\S+\.\S+/.test(tr.email) : !!tr.contact_id;
    }
    openTransfertConfig() {
        this.state.transfert = null;
        this.action.doAction("service_travaux.action_transfert_contacts");
    }
    async sendTransfert(send) {
        const tr = this.state.transfert;
        tr.busy = true;
        try {
            const res = await this.orm.call("travaux.demande", "tv_transfert_send", [tr.id, {
                contact_id: tr.contact_id === "other" ? false : tr.contact_id, email: tr.contact_id === "other" ? tr.email : "",
                sujet: tr.sujet, message: tr.message, send,
            }]);
            this.state.transfert = null;
            await this.load(true);
            if (res.asked && !res.sent) {
                this.notification.add(`Demande transférée à ${res.service}, mais le mail n'est pas parti (envoi de mails non configuré ?). Le PDF est dans le fil de la demande.`, { type: "warning", sticky: true });
            } else {
                this.notification.add(res.sent ? `Mail envoyé à ${res.service} avec le PDF de la demande.` : `Demande transférée à ${res.service}. Le PDF est dans le fil de la demande.`, { type: "success" });
            }
        } finally {
            if (this.state.transfert) this.state.transfert.busy = false;
        }
    }

    // ------------------------------------------------------------ répondre au contact
    startReply() {
        this.state.reply = { message: "", busy: false };
    }
    cancelReply() {
        this.state.reply = null;
    }
    async sendReply() {
        const rp = this.state.reply;
        if (!rp || !rp.message.trim()) return;
        rp.busy = true;
        try {
            const res = await this.orm.call("travaux.demande", "tv_repondre", [this.current.id, rp.message]);
            this.state.reply = null;
            if (res.sent < res.asked) {
                this.notification.add("Réponse enregistrée dans la demande, mais le mail n'est pas parti (envoi de mails non configuré ?).", { type: "warning", sticky: true });
            } else {
                this.notification.add("Réponse envoyée au contact.", { type: "success" });
            }
        } finally {
            if (this.state.reply) this.state.reply.busy = false;
        }
    }

    // ------------------------------------------------------------ doublons
    otherContactsTitle(r) {
        return (r.contacts || []).map((p) => [p.name, p.tel, p.email].filter(Boolean).join(" · ")).join("\n");
    }
    askFusion(d) {
        this.state.fusion = { id: d.id, ref: d.ref, titre: d.titre, prevenir: true, pick: false };
    }
    pickFusion() {
        this.state.fusion = { id: false, ref: "", titre: "", prevenir: true, pick: true };
    }
    get fusionChoices() {
        const r = this.current;
        return this.state.data.items.filter((o) => o.id !== r.id && o.state !== "transferee");
    }
    onFusionTarget(ev) {
        const o = this.state.data.items.find((x) => x.id === parseInt(ev.target.value));
        this.state.fusion.id = o ? o.id : false;
        this.state.fusion.ref = o ? o.ref : "";
        this.state.fusion.titre = o ? o.titre : "";
    }
    cancelFusion() {
        this.state.fusion = null;
    }
    async doFusion() {
        const r = this.current;
        const f = this.state.fusion;
        if (!f || !f.id) return;
        this.state.busy = true;
        try {
            const res = await this.orm.call("travaux.demande", "tv_fusionner", [r.id, f.id, !!f.prevenir]);
            this.state.fusion = null;
            await this.load(true);
            this.select(res.id);
            this.notification.add(`${r.ref} est fusionnée dans ${res.ref}.` + (res.informed ? " Le signaleur est prévenu par mail." : ""), { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }

    // ------------------------------------------------------------ qui, quoi, où
    startEdit() {
        const r = this.current;
        this.state.edit = {
            id: r.id, titre: r.titre, description: r.description, categorie_id: r.categorie_id || "", urgent: r.urgent,
            adresse: r.adresse, precision: r.precision, demandeur: r.demandeur, demandeur_tel: r.demandeur_tel,
            demandeur_email: r.demandeur_email, busy: false,
        };
    }
    newDemande() {
        this.state.edit = {
            id: false, titre: "", description: "", categorie_id: "", urgent: false, adresse: "", precision: "",
            demandeur: "", demandeur_tel: "", demandeur_email: "", busy: false,
        };
    }
    cancelEdit() {
        this.state.edit = null;
    }
    isEditCat(id) {
        return !!this.state.edit && String(id) === String(this.state.edit.categorie_id);
    }
    onEditCat(ev) {
        this.state.edit.categorie_id = ev.target.value;
    }
    async saveEdit() {
        const f = this.state.edit;
        if (!f || !f.titre.trim()) return;
        f.busy = true;
        try {
            const id = await this.orm.call("travaux.demande", "tv_save_demande", [f.id || false, {
                titre: f.titre, description: f.description, categorie_id: f.categorie_id, urgent: f.urgent,
                adresse: f.adresse, precision: f.precision, demandeur: f.demandeur, demandeur_tel: f.demandeur_tel,
                demandeur_email: f.demandeur_email,
            }]);
            const isNew = !f.id;
            this.state.edit = null;
            if (isNew) this.state.tab = "todo";
            await this.load(true);
            if (isNew) this.select(id);
            else this.select(id);
        } finally {
            if (this.state.edit) this.state.edit.busy = false;
        }
    }

    // ------------------------------------------------------------ photos
    get photos() {
        const r = this.current;
        return (r && r.photos) || [];
    }
    get photo() {
        return this.photos[Math.min(this.state.photoIdx, this.photos.length - 1)];
    }
    get lightPhoto() {
        return this.state.lightbox === null ? null : this.photos[this.state.lightbox];
    }
    showPhoto(i) {
        this.state.photoIdx = i;
    }
    openLightbox(i) {
        if (this.photos.length) this.state.lightbox = i;
    }
    closeLightbox() {
        this.state.lightbox = null;
    }
    lightStep(n) {
        const len = this.photos.length;
        this.state.lightbox = (this.state.lightbox + n + len) % len;
    }
    onLightboxKey(ev) {
        if (this.state.lightbox === null) return;
        if (ev.key === "Escape") this.closeLightbox();
        if (ev.key === "ArrowRight") this.lightStep(1);
        if (ev.key === "ArrowLeft") this.lightStep(-1);
    }
    async setMainPhoto(p) {
        if (!p || !p.id) return;
        await this.orm.call("travaux.demande", "tv_set_main_photo", [this.current.id, p.id]);
        this.state.photoIdx = 0;
        if (this.state.lightbox !== null) this.state.lightbox = 0;
        await this.load(true);
        this.notification.add("Photo principale mise à jour : elle apparaît en premier, dans les listes et pour l'équipe.", { type: "success" });
    }
    pickPhoto() {
        this.photoRef.el.click();
    }
    async onPhotoPicked(ev) {
        const files = [...ev.target.files];
        ev.target.value = "";
        for (const f of files) {
            const data = await readImage(f);
            await this.orm.call("travaux.demande", "tv_add_photo", [this.current.id, data]);
        }
        await this.load(true);
    }

    // ------------------------------------------------------------ carte : point déplaçable
    get canMove() {
        return !this.state.mapFailed;
    }
    pinIcon(L, edit) {
        return L.divIcon({
            className: "o_tv_pin" + (edit ? " edit" : ""), iconSize: [30, 40], iconAnchor: [15, 38],
            html: '<svg viewBox="0 0 30 40" width="30" height="40"><path d="M15 39C15 39 28 24 28 14A13 13 0 0 0 2 14C2 24 15 39 15 39Z" fill="' +
                (edit ? "#1D4ED8" : "#C2410C") + '" stroke="#fff" stroke-width="2"/><circle cx="15" cy="14" r="5" fill="#fff"/></svg>',
        });
    }
    async renderMap() {
        const el = this.mapRef.el;
        const r = this.current;
        if (!el || !r || this.state.mapFailed) return;
        if (!el._tvMap) {
            if (el._tvLoading) return;
            el._tvLoading = true;
            try {
                await loadLeaflet();
            } catch {
                this.state.mapFailed = true;
                return;
            }
            if (!this.mapRef.el || this.mapRef.el !== el) return;
            const L = window.L;
            const c = r.lat ? [r.lat, r.lng] : this.state.data.center || [50.5, 4.47];
            const map = L.map(el, { zoomControl: true, attributionControl: false }).setView(c, r.lat ? 17 : 14);
            L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
            map.on("click", (ev) => this.state.pos && this.movePin(ev.latlng.lat, ev.latlng.lng, true));
            el._tvMap = map;
            setTimeout(() => map.invalidateSize(), 250);
        }
        const L = window.L;
        const map = el._tvMap;
        const pos = this.state.pos;
        const lat = pos ? pos.lat : r.lat;
        const lng = pos ? pos.lng : r.lng;
        if (lat && lng) {
            if (!el._tvPin) {
                el._tvPin = L.marker([lat, lng], { draggable: false, icon: this.pinIcon(L, false) }).addTo(map);
                el._tvPin.on("dragend", () => {
                    const ll = el._tvPin.getLatLng();
                    this.movePin(ll.lat, ll.lng, true);
                });
            } else {
                const cur = el._tvPin.getLatLng();
                if (Math.abs(cur.lat - lat) > 1e-7 || Math.abs(cur.lng - lng) > 1e-7) el._tvPin.setLatLng([lat, lng]);
            }
            const edit = !!pos;
            if (el._tvEdit !== edit) {
                el._tvEdit = edit;
                el._tvPin.setIcon(this.pinIcon(L, edit));
                if (edit) el._tvPin.dragging.enable();
                else {
                    el._tvPin.dragging.disable();
                    map.setView([lat, lng], Math.max(map.getZoom(), 16));
                }
            }
        } else if (el._tvPin) {
            el._tvPin.remove();
            el._tvPin = null;
            el._tvEdit = undefined;
        }
    }
    startMove() {
        const r = this.current;
        const c = this.state.data.center || [50.5, 4.47];
        const el = this.mapRef.el;
        const mc = el && el._tvMap ? el._tvMap.getCenter() : null;
        this.state.pos = {
            lat: r.lat || (mc ? mc.lat : c[0]), lng: r.lng || (mc ? mc.lng : c[1]),
            adresse: r.adresse || "", typed: false, busy: false, searching: false, note: "",
        };
    }
    cancelMove() {
        this.state.pos = null;
    }
    onPosAdresse(ev) {
        this.state.pos.adresse = ev.target.value;
        this.state.pos.typed = true;
    }
    onPosKey(ev) {
        if (ev.key === "Enter") this.searchAdresse();
    }
    /** Le point a bougé : on propose l'adresse correspondante (service OpenStreetMap). */
    async movePin(lat, lng, reverse) {
        const pos = this.state.pos;
        if (!pos) return;
        pos.lat = lat;
        pos.lng = lng;
        pos.note = "";
        if (!reverse) return;
        try {
            const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=18&accept-language=fr&lat=${lat}&lon=${lng}`);
            const a = (await res.json()).address || {};
            const street = a.road || a.pedestrian || a.footway || a.path || "";
            if (street && this.state.pos) {
                this.state.pos.adresse = street + (a.house_number ? " " + a.house_number : "");
                this.state.pos.typed = false;
                this.state.pos.note = "Adresse proposée d'après la position : corrigez-la si besoin.";
            }
        } catch {
            if (this.state.pos) this.state.pos.note = "Adresse non trouvée automatiquement : écrivez-la à la main.";
        }
    }
    /** L'adresse a été tapée : on place le point dessus. */
    async searchAdresse() {
        const pos = this.state.pos;
        if (!pos || !pos.adresse.trim()) return;
        pos.searching = true;
        pos.note = "";
        try {
            const c = this.state.data.center || [50.5, 4.47];
            const box = `${c[1] - 0.15},${c[0] + 0.1},${c[1] + 0.15},${c[0] - 0.1}`;
            const q = encodeURIComponent(pos.adresse + (this.state.data.ville ? ", " + this.state.data.ville : ""));
            const res = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&accept-language=fr&countrycodes=be&viewbox=${box}&q=${q}`);
            const hit = (await res.json())[0];
            if (hit) {
                pos.lat = parseFloat(hit.lat);
                pos.lng = parseFloat(hit.lon);
                const el = this.mapRef.el;
                if (el && el._tvMap) el._tvMap.setView([pos.lat, pos.lng], 17);
                pos.note = "Point placé sur l'adresse : glissez-le pour l'ajuster.";
            } else {
                pos.note = "Adresse introuvable sur la carte : placez le point à la main.";
            }
        } catch {
            pos.note = "Recherche indisponible : placez le point à la main.";
        } finally {
            pos.searching = false;
        }
    }
    async savePos() {
        const pos = this.state.pos;
        const r = this.current;
        pos.busy = true;
        try {
            await this.orm.call("travaux.demande", "tv_set_position", [r.id, pos.lat, pos.lng, pos.adresse]);
            this.state.pos = null;
            await this.load(true);
        } finally {
            if (this.state.pos) this.state.pos.busy = false;
        }
    }
    osmUrl(r) {
        return r.lat
            ? `https://www.openstreetmap.org/?mlat=${r.lat}&mlon=${r.lng}#map=18/${r.lat}/${r.lng}`
            : `https://www.openstreetmap.org/search?query=${encodeURIComponent(r.adresse + (this.state.data.ville ? ", " + this.state.data.ville : ""))}`;
    }
}
TravauxDemandes.template = "service_travaux.Demandes";
TravauxDemandes.components = { TvTimeline, TvHelp };
registry.category("actions").add("travaux_demandes", TravauxDemandes);
