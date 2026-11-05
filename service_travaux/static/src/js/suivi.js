/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useTravauxLive, imgUrl, shortDate, fmtH, hhmm, d, ST_STYLE, STATES } from "./common";
import { TvTimeline, TvHelp } from "./components";

const KIND = {
    tournee: ["fa-refresh", "Tournée récurrente", "Tournée"],
    maintenance: ["fa-wrench", "Maintenance planifiée", "Maintenance"],
    demande: ["fa-map-marker", "Demande", "Demande"],
};
const KIND_FILTERS = [["all", "Tout", "fa-bars"], ["tournee", "Tournées récurrentes", "fa-refresh"], ["maintenance", "Maintenance planifiée", "fa-wrench"], ["demande", "Demandes", "fa-map-marker"]];
const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
const JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];

export class TravauxSuivi extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.COLS = STATES;
        this.KIND = KIND;
        this.KIND_FILTERS = KIND_FILTERS;
        this.imgUrl = imgUrl;
        this.shortDate = shortDate;
        this.fmtH = fmtH;
        this.hhmm = hhmm;
        this.ST_STYLE = ST_STYLE;
        this.state = useState({
            data: { jobs: [], tasks: [], teams: [], supervisors: [], declared: {} },
            sup: null, off: {}, view: "kanban", week: 0, kind: "all",
            selId: null, conv: null, showAfter: false, dragId: null, planPick: null,
            add: null, imp: null, viewId: "mine", kdrag: null, kover: null, fiche: null,
            zones: { circuits: [] },
            kmode: "week", koff: 0, cancel: null,
        });
        this.KIND = KIND;
        this.help = {
            kanban: {
                text: "Le kanban montre où en est chaque demande. Les demandes reçues arrivent dans la colonne « À attribuer ».",
                steps: [
                    "Attribuez une demande à une équipe avec le menu de sa carte.",
                    "Suivez-la de Planifiée à En cours, puis Terminée : ouvrez la carte pour voir la chronologie, les photos et les heures.",
                    "Validez et clôturez : le demandeur est informé que le problème est résolu.",
                ],
            },
            zones: {
                text: "Les tournées : une liste de zones prédéfinie pour une équipe, validée zone par zone (espaces verts) ou en lot (poubelles).",
                steps: [
                    "Créez la liste avec « Gérer les tournées » : un nom, une équipe, le mode de validation, puis les zones.",
                    "Une par une : sur le terrain, l'équipe fait « Démarrer » puis « Fait » sur chaque zone ; chaque zone donne un bon de travail.",
                    "En lot : l'équipe démarre la tournée et indique 50, 80 ou 100 % en terminant. Une zone non faite n'est pas reportée.",
                ],
            },
            planning: {
                text: "Le planning organise la semaine de vos équipes et montre leur charge.",
                steps: [
                    "Glissez une demande de la pile (à gauche) dans la case équipe × jour.",
                    "Déplacez une tâche d'un jour ou d'une équipe par glisser-déposer ; « + » ajoute une tâche.",
                    "Surveillez « Prévu / capacité » : rouge = équipe surchargée, vert = marge disponible.",
                ],
            },
        };
        onWillStart(async () => {
            await this.load();
            const mine = this.state.data.teams.some((t) => t.mine);
            this.state.sup = mine ? "mine" : "all";
            this.state.viewId = mine ? "mine" : "all";
            const ctx = (this.props.action && this.props.action.context) || {};
            if (ctx.travaux_view) this.state.view = ctx.travaux_view;
            if (this.state.view === "zones") await this.loadZones();
        });
        useTravauxLive(async (p) => {
            if (p.kind === "zones" || (p.kind === "poll" && this.state.view === "zones")) {
                await this.loadZones();
                if (p.kind === "zones") return;
            }
            await this.load();
            if (this.state.selId && (p.kind === "poll" || (p.ids || []).includes(this.state.selId))) {
                await this.openDetail(this.state.selId, true);
            }
        }, ["demande", "equipes", "poll", "zones"]);
    }
    async load() {
        // une seule période pour le kanban et le planning ; en vue « mois », le planning reste sur la semaine en cours
        const week = this.state.kmode === "week" ? this.state.koff : 0;
        this.state.data = await this.orm.call("travaux.demande", "tv_suivi", [week, this.state.kmode, this.state.koff]);
    }
    // ---------------------------------------------------------------- filtres
    get supName() {
        const v = this.views.find((x) => x.id === this.state.viewId);
        if (v) return v.name;
        if (this.state.viewId === "mine") return "Mes équipes";
        if (this.state.sup === "all") return "Toutes les équipes";
        if (this.isDept(this.state.sup)) {
            const d = (this.state.data.services || []).find((x) => "d" + x.id === this.state.sup);
            return d ? d.name : "";
        }
        const s = this.state.data.supervisors.find((x) => x.id === this.state.sup);
        return s ? s.name : "";
    }
    get supTeams() {
        const v = this.state.sup;
        // « Mes équipes » : celles dont je suis superviseur ou responsable de service
        if (v === "mine") return this.state.data.teams.filter((t) => t.mine);
        if (this.isDept(v)) {
            const id = parseInt(v.slice(1));
            return this.state.data.teams.filter((t) => (t.dept_ids || []).includes(id));
        }
        return this.state.data.teams.filter((t) => v === "all" || t.sup_id === v);
    }
    isDept(v) {
        return typeof v === "string" && v[0] === "d";
    }
    deptLabel(d) {
        return "\u00A0\u00A0".repeat(d.depth) + d.name.split(" / ").pop();
    }
    get shownTeams() {
        return this.supTeams.filter((t) => !this.state.off[t.id]);
    }
    get shownIds() {
        return this.shownTeams.map((t) => t.id);
    }
    // ---------------------------------------------------------------- vues personnalisées
    get views() {
        return this.state.data.views || [];
    }
    get hasMine() {
        return this.state.data.teams.some((t) => t.mine);
    }
    selectView(v) {
        if (v === "mine") {
            this.state.viewId = "mine";
            this.state.sup = "mine";
            this.state.off = {};
        } else if (v === "all") {
            this.state.viewId = "all";
            this.state.sup = "all";
            this.state.off = {};
        } else {
            this.state.viewId = v.id;
            this.state.sup = "all";
            const off = {};
            for (const t of this.state.data.teams) off[t.id] = !v.teams.includes(t.id);
            this.state.off = off;
        }
    }
    async saveView() {
        const ids = this.shownIds;
        if (!ids.length) return;
        const name = window.prompt("Nom de la vue (ex. « Espaces publics », « Bâtiments et écoles ») :",
            this.shownTeams.map((t) => t.name).slice(0, 3).join(" + "));
        if (!name) return;
        const shared = window.confirm("Partager cette vue avec tous les utilisateurs ?\n(OK = partagée, Annuler = pour moi seulement)");
        const id = await this.orm.call("travaux.demande", "tv_save_view", [name, ids, shared]);
        await this.load();
        const v = this.views.find((x) => x.id === id);
        if (v) this.selectView(v);
        this.notification.add(`Vue « ${name} » enregistrée.`, { type: "success" });
    }
    async deleteView(v) {
        if (!window.confirm(`Supprimer la vue « ${v.name} » ?`)) return;
        await this.orm.call("travaux.demande", "tv_del_view", [v.id]);
        if (this.state.viewId === v.id) this.selectView(this.hasMine ? "mine" : "all");
        await this.load();
    }
    isSupSel(id) {
        return this.state.sup === id;
    }
    onSup(ev) {
        const v = ev.target.value;
        this.state.sup = v === "all" || v === "mine" || v[0] === "d" ? v : parseInt(v);
        this.state.off = {};
        this.state.viewId = v === "all" || v === "mine" ? v : null;
    }
    toggleTeam(id) {
        this.state.viewId = null;
        const on = !this.state.off[id];
        if (on && this.shownTeams.length === 1) return;
        this.state.off[id] = on;
    }
    async setView(v) {
        if (v === "zones") {
            this.closeDetailIfAny();
            await this.loadZones();
        }
        this.state.view = v;

    }
    closeDetailIfAny() {
        this.state.selId = null;
        this.state.conv = null;
    }
    // ---------------------------------------------------------------- zones (tournées)
    async loadZones() {
        this.state.zones = await this.orm.call("travaux.circuit", "tv_zones", [true]);
    }
    get circuits() {
        const shown = this.shownTeams.map((t) => t.id);
        return this.state.zones.circuits.filter((c) => shown.includes(c.team_id));
    }
    zoneAge(z) {
        if (z.days === null || z.days === undefined) return "jamais";
        if (z.days === 0) return "aujourd'hui";
        if (z.days === 1) return "hier";
        return `il y a ${z.days} j`;
    }
    zoneDate(z) {
        return z.last ? shortDate(z.last) : "";
    }
    async zoneFait(z) {
        this.state.zones = await this.orm.call("travaux.circuit.zone", "tv_fait", [z.id]);
        await this.loadZones();
    }
    async zoneAnnuler(z) {
        await this.orm.call("travaux.circuit.zone", "tv_annuler", [z.id]);
        await this.loadZones();
    }
    zoneView(z) {
        window.open(z.photo, "_blank");
    }
    openCircuits() {
        this.action.doAction("service_travaux.action_circuits");
    }
    openCircuit(c) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "travaux.circuit", res_id: c.id, views: [[false, "form"]], target: "current" });
    }
    openPassages(c) {
        this.action.doAction({
            type: "ir.actions.act_window", name: "Passages – " + c.name, res_model: "travaux.passage",
            views: [[false, "list"]], domain: [["circuit_id", "=", c.id]], target: "current",
        });
    }
    setKind(k) {
        this.state.kind = k;
    }
    async shiftWeek(n) {
        await this.shiftK(n);
    }
    get weekInfo() {
        const m = d(this.state.data.monday);
        if (!m) return "";
        const f = m.plus({ days: 4 });
        const tasks = this.state.data.tasks.filter((t) => this.shownIds.includes(t.equipe_id));
        const done = tasks.filter((t) => ["valider", "close"].includes(t.state)).length;
        return `Semaine ${m.weekNumber} · du ${m.day} ${MOIS[m.month - 1]} au ${f.day} ${MOIS[f.month - 1]} ${f.year} · ${tasks.length} tâches cette semaine, ${done} faites`;
    }
    get pile() {
        // d'abord ce qui est déjà attribué à mes équipes et reste à planifier, puis ce qui n'a pas encore d'équipe
        return this.state.data.jobs
            .filter((j) => ["recue", "precision"].includes(j.state) && (!j.pret || this.shownIds.includes(j.equipe_id)))
            .sort((a, b) => (b.pret ? 1 : 0) - (a.pret ? 1 : 0));
    }
    get pilePret() {
        return this.pile.filter((j) => j.pret).length;
    }
    /** Même fiche que « Demandes à traiter » : compléter, planifier, envoyer à l'équipe. */
    openFiche(id) {
        this.action.doAction("service_travaux.action_client_demandes", { additionalContext: { active_id: id } });
    }
    openCard(j) {
        if (["recue", "precision"].includes(j.state)) this.openFiche(j.id);
        else this.openDetail(j.id);
    }
    column(key) {
        if (key === "recue") return this.pile;
        // demandes, puis entretiens de la période (circuits, actions récurrentes, zones)
        const jobs = this.state.data.jobs.filter((j) => j.state === key && this.shownIds.includes(j.equipe_id));
        const ent = (this.state.data.entretiens || []).filter((j) => j.state === key && this.shownIds.includes(j.equipe_id));
        return jobs.concat(ent);
    }
    // ---------------------------------------------------------------- période du kanban
    async setKMode(mode) {
        this.state.kmode = mode;
        this.state.koff = 0;
        await this.load();
    }
    async shiftK(n) {
        this.state.koff = n === 0 ? 0 : this.state.koff + n;
        await this.load();
    }
    get kLabel() {
        const k = this.state.data.kanban;
        if (!k) return "";
        const a = d(k.start), b = d(k.end);
        if (k.mode === "month") return `${MOIS[a.month - 1].replace(/^./, (c) => c.toUpperCase())} ${a.year} · semaines ${a.weekNumber} à ${b.weekNumber}`;
        return `Semaine ${k.week} · du ${a.day}${a.month !== b.month ? " " + MOIS[a.month - 1] : ""} au ${b.day} ${MOIS[b.month - 1]}`;
    }
    // ---------------------------------------------------------------- attribution (pile → équipe)
    defaultDay(job) {
        const today = d(this.state.data.today);
        let x = job && job.urgent ? today : today.plus({ days: 1 });
        if (x.weekday > 5) x = x.plus({ days: 8 - x.weekday });
        return x.toISODate();
    }
    async assignFromCard(job, ev) {
        const teamId = parseInt(ev.target.value);
        if (!teamId) return;
        await this.orm.call("travaux.demande", "tv_place", [job.id, teamId, this.defaultDay(job), job.duree_prevue || 2]);
        const t = this.state.data.teams.find((x) => x.id === teamId);
        this.notification.add(`${job.ref} envoyée à l'équipe ${t ? t.name : ""}.`, { type: "success" });
        await this.load();
        await this.openDetail(job.id);
    }
    pick(job) {
        this.state.planPick = this.state.planPick === job.id ? null : job.id;
        this.openDetail(job.id);
    }
    get pickRef() {
        const j = this.pile.find((x) => x.id === this.state.planPick);
        return j ? j.ref : "";
    }
    async place(teamId, iso) {
        const id = this.state.planPick;
        this.state.planPick = null;
        if (!id) return;
        const j = this.pile.find((x) => x.id === id);
        await this.orm.call("travaux.demande", "tv_place", [id, teamId, iso, (j && j.duree_prevue) || 2]);
        await this.load();
        await this.openDetail(id);
    }
    // ---------------------------------------------------------------- détail
    async openDetail(id, silent = false) {
        if (!silent) this.state.cancel = null;
        if (!silent) {
            this.state.showAfter = false;
            this.state.imp = null;
        }
        this.state.selId = id;
        this.state.conv = await this.orm.call("travaux.demande", "tv_conversation", [id]);
    }
    closeDetail() {
        this.state.selId = null;
        this.state.conv = null;
    }
    get thread() {
        const msgs = (this.state.conv && this.state.conv.messages) || [];
        return msgs.filter((m) => m.kind === "bubble" || m.kind === "sys").slice(-6);
    }
    setAfter(v) {
        this.state.showAfter = v;
    }
    async annulerBon() {
        const c = this.state.conv;
        this.state.conv = await this.orm.call("travaux.demande", "tv_annuler_bt", [c.id, this.state.cancel.motif]);
        this.state.cancel = null;
        this.notification.add(`${c.ref} annulé.`, { type: "success" });
        await this.load();
    }
    async act(name) {
        this.state.conv = await this.orm.call("travaux.demande", "tv_do", [this.state.conv.id, name]);
        await this.load();
    }
    async changeTeam(ev) {
        const c = this.state.conv;
        const teamId = parseInt(ev.target.value);
        if (["recue", "precision"].includes(c.state)) {
            await this.orm.call("travaux.demande", "tv_place", [c.id, teamId, this.defaultDay(c), c.duree_prevue || 2]);
        } else {
            await this.orm.call("travaux.demande", "tv_move_task", [c.id, teamId, c.date_prevue]);
        }
        await this.load();
        await this.openDetail(c.id, true);
    }
    async changeDay(ev) {
        const c = this.state.conv;
        await this.orm.call("travaux.demande", "tv_move_task", [c.id, c.equipe_id, ev.target.value]);
        await this.load();
        await this.openDetail(c.id, true);
    }
    async setPrevu(ev) {
        const h = parseFloat(String(ev.target.value).replace(",", "."));
        if (isNaN(h)) return;
        this.state.conv = await this.orm.call("travaux.demande", "tv_set_prevu", [this.state.conv.id, h]);
        await this.load();
    }
    openImp() {
        const c = this.state.conv;
        this.state.imp = { equipe_id: String(c.equipe_id || ""), date: this.state.data.today, h: "" };
    }
    onImp(field, ev) {
        this.state.imp[field] = ev.target.value;
    }
    isImpTeam(id) {
        return this.state.imp && String(id) === String(this.state.imp.equipe_id);
    }
    async addImp() {
        const i = this.state.imp;
        const h = parseFloat(String(i.h).replace(",", "."));
        if (!h || !i.equipe_id) {
            this.notification.add("Indiquez l'équipe et les heures.", { type: "warning" });
            return;
        }
        this.state.conv = await this.orm.call("travaux.demande", "tv_add_pointage", [this.state.conv.id, { h, equipe_id: parseInt(i.equipe_id), date: i.date }]);
        this.state.imp = null;
        await this.load();
    }
    async delImp(pid) {
        this.state.conv = await this.orm.call("travaux.demande", "tv_del_pointage", [pid]);
        await this.load();
    }
    get dayOptions() {
        const ref = (this.state.conv && d(this.state.conv.date_prevue)) || d(this.state.data.monday);
        const m = ref ? ref.minus({ days: ref.weekday - 1 }) : null;
        const out = [];
        if (!m) return out;
        for (let i = 0; i < 10; i++) {
            const x = m.plus({ days: i + (i >= 5 ? 2 : 0) });
            out.push({ v: x.toISODate(), label: `${JOURS[x.weekday - 1]} ${x.day}/${x.month}` });
        }
        return out;
    }
    impDate(iso) {
        const x = d(iso);
        return x ? `${JOURS[x.weekday - 1]} ${x.day}/${x.month}` : "";
    }
    openTerrain() {
        this.action.doAction("service_travaux.action_client_terrain", { additionalContext: { active_id: this.state.selId } });
    }
    openDemandes() {
        this.action.doAction("service_travaux.action_client_demandes");
    }
    openForm() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "travaux.demande", res_id: this.state.selId, views: [[false, "form"]] });
    }
    // ---------------------------------------------------------------- planning
    get days() {
        const m = d(this.state.data.monday);
        if (!m) return [];
        return [0, 1, 2, 3, 4].map((i) => {
            const x = m.plus({ days: i });
            const today = x.toISODate() === this.state.data.today;
            return { i, iso: x.toISODate(), label: `${JOURS[i]} ${x.day}${today ? " · auj." : ""}`, today };
        });
    }
    // ---------------------------------------------------------------- colonnes : les jours de la semaine, ou les semaines du mois
    get isMonth() {
        return this.state.kmode === "month";
    }
    get planTasks() {
        return this.isMonth ? this.state.data.tasks_mois || [] : this.state.data.tasks;
    }
    get cols() {
        if (!this.isMonth) return this.days.map((x) => ({ ...x, from: x.iso, to: x.iso }));
        const k = this.state.data.kanban;
        if (!k) return [];
        const start = d(k.start), end = d(k.end), today = this.state.data.today;
        const out = [];
        let m = start.minus({ days: start.weekday - 1 });
        while (m <= end) {
            const from = m < start ? start : m;
            const fri = m.plus({ days: 4 });
            const to = fri > end ? end : fri;
            if (from <= to) {
                out.push({ iso: from.toISODate(), from: from.toISODate(), to: to.toISODate(),
                           label: `Sem. ${m.weekNumber} · ${from.day}–${to.day}/${to.month}`,
                           today: today >= from.toISODate() && today <= m.plus({ days: 6 }).toISODate() });
            }
            m = m.plus({ days: 7 });
        }
        return out;
    }
    colOf(iso) {
        return this.cols.find((c) => c.iso === iso) || { from: iso, to: iso };
    }
    get gridStyle() {
        return `grid-template-columns: 170px repeat(${this.cols.length || 5}, minmax(0, 1fr));`;
    }
    cell(teamId, iso) {
        const c = this.colOf(iso);
        return this.planTasks.filter((t) => t.equipe_id === teamId && t.date_prevue >= c.from && t.date_prevue <= c.to &&
            (this.state.kind === "all" || t.type === this.state.kind));
    }
    cellHours(team, iso) {
        const c = this.colOf(iso);
        const tasks = this.planTasks.filter((t) => t.equipe_id === team.id && t.date_prevue >= c.from && t.date_prevue <= c.to);
        const h = tasks.reduce((a, t) => a + (t.duree_prevue || 0), 0);
        const cap = this.isMonth ? team.cap : team.cap / 5;
        return { h, over: cap && h > cap * 1.05 };
    }
    taskSub(k) {
        if (this.isMonth) return `${d(k.date_prevue).toFormat("dd/MM")} · ` + this.taskSubBase(k);
        return this.taskSubBase(k);
    }
    taskSubBase(k) {
        if (["valider", "close"].includes(k.state)) return "Fait · " + fmtH(k.duree_mesuree || k.duree_prevue);
        if (k.state === "cours") return "En cours · " + fmtH(k.duree_prevue);
        return KIND[k.type][2] + " · " + (k.duree_prevue ? fmtH(k.duree_prevue) : "à estimer");
    }
    rowInfo(team) {
        const tasks = this.planTasks.filter((t) => t.equipe_id === team.id);
        const planned = tasks.reduce((a, t) => a + (t.duree_prevue || 0), 0);
        let declared = 0;
        if (this.isMonth) {
            declared = tasks.filter((t) => ["valider", "close"].includes(t.state)).reduce((a, t) => a + (t.duree_mesuree || 0), 0);
        } else {
            for (const dd of this.days) declared += this.state.data.declared[`${team.id}|${dd.iso}`] || 0;
        }
        const cap = this.isMonth ? team.cap * this.cols.length : team.cap;
        const ratio = cap ? planned / cap : 0;
        return {
            planned, declared, cap: Math.round(cap),
            label: ratio > 0.95 ? (this.isMonth ? "Mois chargé" : "Semaine chargée") : ratio < 0.6 ? "Marge disponible" : "Charge normale",
            color: ratio > 0.95 ? "#B91C1C" : ratio < 0.6 ? "#15803D" : "#4A514D",
            pct: Math.round(ratio * 100),
        };
    }
    onDragStart(id) {
        this.state.dragId = id;
    }
    noop() {}
    async onDrop(teamId, iso) {
        const id = this.state.dragId;
        this.state.dragId = null;
        if (!id) return;
        if (this.pile.some((j) => j.id === id)) {
            this.state.planPick = id;
            return this.place(teamId, iso);
        }
        const t = this.planTasks.find((x) => x.id === id);
        if (t && t.equipe_id === teamId && t.date_prevue === iso) return;
        await this.orm.call("travaux.demande", "tv_move_task", [id, teamId, iso]);
        await this.load();
        if (this.state.selId === id) await this.openDetail(id, true);
    }
    startAdd(teamId, iso) {
        this.state.add = { team: teamId, iso, titre: "", type: "maintenance", h: "2" };
    }
    isAdd(teamId, iso) {
        return this.state.add && this.state.add.team === teamId && this.state.add.iso === iso;
    }
    onAdd(field, ev) {
        this.state.add[field] = ev.target.value;
    }
    cancelAdd() {
        this.state.add = null;
    }
    async saveAdd() {
        const a = this.state.add;
        const id = await this.orm.call("travaux.demande", "tv_quick_task", [{ titre: a.titre, type: a.type, equipe_id: a.team, date: a.iso, h: a.h }]);
        this.state.add = null;
        await this.load();
        await this.openDetail(id);
    }
    get statutChoices() {
        return [
            ["recue:0", "À attribuer (sans équipe)"], ["recue:1", "À planifier (garde l'équipe)"], ["planif", "Planifiée"],
            ["cours", "En cours"], ["valider", "Terminée · à valider"], ["close", "Clôturée"],
        ];
    }
    /** Changer le statut à la main : liste de la fiche, ou glisser la carte dans une autre colonne. */
    async setStatut(id, value) {
        if (!value) return;
        const [state, keep] = value.split(":");
        await this.orm.call("travaux.demande", "tv_set_state", [id, state, keep !== "0"]);
        await this.load();
        if (this.state.selId === id) {
            if (state === "recue") this.closeDetail();
            else await this.openDetail(id, true);
        }
    }
    onStatutSelect(ev) {
        const v = ev.target.value;
        ev.target.value = "";
        this.setStatut(this.state.conv.id, v);
    }
    onCardDrag(j) {
        this.state.kdrag = j.id;
    }
    onColOver(ev, key) {
        if (!this.state.kdrag) return;
        ev.preventDefault();
        this.state.kover = key;
    }
    async onColDrop(key) {
        const id = this.state.kdrag;
        this.state.kdrag = null;
        this.state.kover = null;
        const j = this.state.data.jobs.find((x) => x.id === id);
        if (!j || j.st === key) return;
        if (key !== "recue" && !j.equipe_id) {
            this.notification.add("Choisissez d'abord une équipe pour cette demande.", { type: "warning" });
            return;
        }
        await this.setStatut(id, key === "recue" ? "recue:1" : key);
    }
    onCardDragEnd() {
        this.state.kdrag = null;
        this.state.kover = null;
    }
    pdfUrl(id) {
        return "/report/pdf/service_travaux.report_demande/" + id;
    }
    // ---------------------------------------------------------------- fiche de travail directe
    newFiche() {
        const t = this.shownTeams[0];
        this.state.fiche = {
            titre: "", description: "", type: "demande", equipe_id: t ? String(t.id) : "", date: this.state.data.today || "",
            h: "2", adresse: "", consigne: "", urgent: false, busy: false,
        };
    }
    closeFiche() {
        this.state.fiche = null;
    }
    onFicheTeam(ev) {
        this.state.fiche.equipe_id = ev.target.value;
    }
    onFicheType(ev) {
        this.state.fiche.type = ev.target.value;
    }
    isFicheTeam(id) {
        return !!this.state.fiche && String(id) === String(this.state.fiche.equipe_id);
    }
    get ficheTournees() {
        const f = this.state.fiche;
        const all = this.state.data.tournees || [];
        return f && f.equipe_id ? all.filter((c) => String(c.team_id) === String(f.equipe_id)) : all;
    }
    // ---------------------------------------------------------------- lancer une tournée
    get launchDays() {
        const today = d(this.state.data.today);
        if (!today) return [];
        const out = [{ v: today.toISODate(), label: "Aujourd'hui" }];
        let x = today;
        for (let i = 0; i < 6; i++) {
            x = x.plus({ days: 1 });
            if (x.weekday > 5) continue;
            out.push({ v: x.toISODate(), label: `${JOURS[x.weekday - 1]} ${x.day}/${x.month}` });
        }
        return out;
    }
    setLaunchDay(id, ev) {
        this.launchDay = { ...(this.launchDay || {}), [id]: ev.target.value };
    }
    async lancerTournee(id, date) {
        const day = date || (this.launchDay && this.launchDay[id]) || this.state.data.today;
        await this.orm.call("travaux.circuit", "tv_lancer", [id, day]);
        this.notification.add("Tournée lancée : le bon de la première zone est envoyé à l'équipe.", { type: "success" });
        await this.load();
        if (this.state.view === "zones") await this.loadZones();
    }
    async arreterTournee(c) {
        await this.orm.call("travaux.circuit", "tv_arreter", [c.id]);
        this.notification.add(`Tournée « ${c.name} » arrêtée.`, { type: "info" });
        await this.load();
        await this.loadZones();
    }
    async saveFiche() {
        const f = this.state.fiche;
        if (f.type === "lancer") {
            if (!f.circuit_id) {
                this.notification.add("Choisissez la tournée à lancer.", { type: "warning" });
                return;
            }
            f.busy = true;
            try {
                await this.lancerTournee(parseInt(f.circuit_id), f.date || false);
                this.state.fiche = null;
            } finally {
                if (this.state.fiche) this.state.fiche.busy = false;
            }
            return;
        }
        if (!f.titre.trim() || !f.equipe_id) {
            this.notification.add("Indiquez un titre et une équipe.", { type: "warning" });
            return;
        }
        f.busy = true;
        try {
            const id = await this.orm.call("travaux.demande", "tv_fiche_travail", [{
                titre: f.titre, description: f.description, type: f.type, equipe_id: parseInt(f.equipe_id), date: f.date || false,
                h: parseFloat(String(f.h).replace(",", ".")) || 0, adresse: f.adresse, consigne: f.consigne, urgent: f.urgent,
            }]);
            this.state.fiche = null;
            await this.load();
            await this.openDetail(id);
            this.notification.add("Fiche de travail envoyée à l'équipe.", { type: "success" });
        } finally {
            if (this.state.fiche) this.state.fiche.busy = false;
        }
    }
    openRecurrences() {
        this.action.doAction("service_travaux.action_recurrences");
    }
}
TravauxSuivi.template = "service_travaux.Suivi";
TravauxSuivi.components = { TvTimeline, TvHelp };
registry.category("actions").add("travaux_suivi", TravauxSuivi);
