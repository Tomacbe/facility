/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useTravauxLive, initials, d } from "./common";
import { TvHelp } from "./components";

const ST = {
    present: { label: "Présent", bg: "#E3F1E8", fg: "#14532D", dot: "#15803D" },
    conge: { label: "Congé", bg: "#EEF1F2", fg: "#3B454D", dot: "#9AAEB4" },
    formation: { label: "Formation", bg: "#E4EDFB", fg: "#1E3A8A", dot: "#9AAEB4" },
    recup: { label: "Récupération", bg: "#EEF1F2", fg: "#3B454D", dot: "#9AAEB4" },
    absent: { label: "Absent", bg: "#FDE8E4", fg: "#9A3412", dot: "#C2410C" },
};
const JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];

export class TravauxEquipes extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.initials = initials;
        this.help = {
            text: "Chaque matin, vérifiez qui est là. Les présences servent à contrôler la charge et le temps déclaré.",
            steps: [
                "Cliquez sur un agent pour indiquer congé, formation ou récupération.",
                "Si une équipe passe sous son minimum, le bandeau orange propose un renfort : un clic suffit.",
                "Pour prêter un agent pour la journée, sélectionnez-le puis « Prêter ici » sur l'équipe qui en a besoin.",
            ],
        };
        this.ST = ST;
        this.state = useState({
            data: { teams: [], people: [], supervisors: [], alert: null },
            sup: "all", dept: "", sel: null, create: false,
            form: { name: "", sup: "", picked: {} },
        });
        onWillStart(() => this.load());
        useTravauxLive(() => this.load(), ["equipes", "poll"]);
    }
    async load() {
        this.state.data = await this.orm.call("travaux.equipe", "tv_equipes_jour", []);
        if (!this.state.form.sup && this.state.data.supervisors.length) {
            this.state.form.sup = String(this.state.data.supervisors[0].id);
        }
    }
    get dateLabel() {
        const x = d(this.state.data.date);
        return x ? `${JOURS[x.weekday - 1]} ${x.day} ${MOIS[x.month - 1]}` : "";
    }
    get people() {
        return this.state.data.people;
    }
    teamById(id) {
        return this.state.data.teams.find((t) => t.id === id);
    }
    where(p) {
        return p.lent || p.home;
    }
    get counts() {
        const ps = this.people;
        const present = ps.filter((p) => p.status === "present").length;
        return { present, absent: ps.length - present, lent: ps.filter((p) => p.lent).length };
    }
    isDeptSel(id) {
        return String(id) === String(this.state.dept);
    }
    onDept(ev) {
        this.state.dept = ev.target.value;
    }
    onFormDept(ev) {
        this.state.form.dept = ev.target.value;
    }
    deptLabel(d) {
        return "\u00A0\u00A0".repeat(d.depth) + d.name.split(" / ").pop();
    }
    get supTabs() {
        return [{ id: "all", name: "Toutes" }].concat(this.state.data.supervisors);
    }
    get cards() {
        const sel = this.state.sel && this.people.find((p) => p.id === this.state.sel);
        const dept = parseInt(this.state.dept) || 0;
        const teams = this.state.data.teams.filter((t) =>
            (this.state.sup === "all" || t.is_pool || String(t.sup_id) === String(this.state.sup)) &&
            (!dept || t.is_pool || (t.dept_ids || []).includes(dept)));
        return teams.map((t) => {
            const mem = this.people.filter((p) => this.where(p) === t.id || (p.home === t.id && p.lent));
            const here = mem.filter((p) => this.where(p) === t.id && p.status === "present");
            const total = this.people.filter((p) => p.home === t.id).length;
            const low = !t.is_pool && t.min && here.length < t.min;
            return {
                t, low,
                count: t.is_pool ? `${here.length} disponible(s)` : `${here.length} / ${Math.max(total, here.length)} présent(s)`,
                sub: t.is_pool ? "Renforts à prêter ou à affecter" : low ? `Sous l'effectif minimum (${t.min})` : "Effectif suffisant",
                pips: t.is_pool ? [] : mem.map((p) => ({ id: p.id, on: this.where(p) === t.id && p.status === "present" })),
                members: mem.map((p) => this.memberView(p, t)),
                canDrop: sel && !t.is_pool && this.where(sel) !== t.id && sel.status === "present",
            };
        });
    }
    memberView(p, t) {
        const lentOut = p.lent && p.home === t.id;
        const lentIn = p.lent === t.id;
        const st = ST[p.status];
        const home = this.teamById(p.home);
        const lentTo = p.lent && this.teamById(p.lent);
        const present = p.status === "present" && !lentOut;
        return {
            p,
            status: lentOut ? "Prêté à " + (lentTo ? lentTo.name : "") : lentIn ? "Renfort de " + (home && !home.is_pool ? home.name : "la réserve") : st.label,
            chip: lentIn ? "background:#DCEEF2;color:#1D4E5C" : lentOut ? "background:#EEF1F2;color:#55606A" : `background:${st.bg};color:${st.fg}`,
            av: present ? `background:${t.color};color:#fff` : "background:#E3E8EA;color:#55606A",
            dim: !present,
        };
    }
    get selected() {
        const p = this.state.sel && this.people.find((x) => x.id === this.state.sel);
        if (!p) return null;
        const home = this.teamById(p.home);
        return {
            p, home,
            color: p.status === "present" ? home.color : "#E3E8EA",
            lendOptions: this.state.data.teams.filter((t) => t.id !== p.home && !t.is_pool),
        };
    }
    get pool() {
        const poolTeam = this.state.data.teams.find((t) => t.is_pool);
        return this.people.filter((p) => poolTeam && p.home === poolTeam.id);
    }
    get stKeys() {
        return Object.keys(ST);
    }
    onFormSup(ev) {
        this.state.form.sup = ev.target.value;
    }
    isFormSup(id) {
        return String(this.state.form.sup) === String(id);
    }
    isSup(id) {
        return String(this.state.sup) === String(id);
    }
    // ---------------------------------------------------------------- actions
    setSup(id) {
        this.state.sup = id;
    }
    pick(id) {
        this.state.sel = this.state.sel === id ? null : id;
        this.state.create = false;
    }
    openCreate() {
        this.state.create = true;
        this.state.sel = null;
    }
    closePanel() {
        this.state.sel = null;
        this.state.create = false;
    }
    async set(vals, id = this.state.sel) {
        await this.orm.call("hr.employee", "tv_set", [id, vals]);
        await this.load();
    }
    setStatus(k) {
        return this.set({ statut_jour: k });
    }
    lend(ev) {
        return this.set({ equipe_pret_id: parseInt(ev.target.value) || false });
    }
    move(ev) {
        return this.set({ equipe_id: parseInt(ev.target.value) });
    }
    back() {
        return this.set({ equipe_pret_id: false });
    }
    async drop(team) {
        const p = this.people.find((x) => x.id === this.state.sel);
        await this.set({ equipe_pret_id: p.home === team.id ? false : team.id });
    }
    async fix() {
        await this.orm.call("travaux.equipe", "tv_lend_suggestion", [this.state.data.alert.team_id]);
        await this.load();
    }
    togglePick(id) {
        this.state.form.picked[id] = !this.state.form.picked[id];
    }
    get canCreate() {
        return this.state.form.name.trim().length > 1 && Object.values(this.state.form.picked).some(Boolean);
    }
    async createTeam() {
        if (!this.canCreate) return;
        const ids = Object.entries(this.state.form.picked).filter(([, v]) => v).map(([k]) => parseInt(k));
        await this.orm.call("travaux.equipe", "tv_create_team", [this.state.form.name, parseInt(this.state.form.sup), ids, parseInt(this.state.form.dept) || false]);
        this.state.form = { name: "", sup: this.state.form.sup, picked: {} };
        this.state.create = false;
        this.notification.add("Équipe créée.", { type: "success" });
        await this.load();
    }
}
TravauxEquipes.template = "service_travaux.Equipes";
TravauxEquipes.components = { TvHelp };
registry.category("actions").add("travaux_equipes", TravauxEquipes);
