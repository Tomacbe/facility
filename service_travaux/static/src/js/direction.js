/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useTravauxLive, fmtN, fmtH, d } from "./common";
import { TvHelp } from "./components";

const MOIS = ["jan", "fév", "mar", "avr", "mai", "jun", "jul", "aoû", "sep", "oct", "nov", "déc"];
const MOIS_LONG = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
const TYPES = [
    ["tournee", "Tournées", "#2A78D6"],
    ["maintenance", "Maintenance planifiée", "#EB6834"],
    ["demande", "Demandes", "#1BAF7A"],
];
const RAMP = ["#CDE2FB", "#9EC5F4", "#6DA7EC", "#3987E5", "#256ABF", "#184F95"];

function arc(cx, cy, r0, r1, a0, a1) {
    const p = (r, a) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const A = p(r1, a0), B = p(r1, a1), C = p(r0, a1), D = p(r0, a0);
    return `M${A[0].toFixed(2)} ${A[1].toFixed(2)} A${r1} ${r1} 0 ${large} 1 ${B[0].toFixed(2)} ${B[1].toFixed(2)} ` +
        `L${C[0].toFixed(2)} ${C[1].toFixed(2)} A${r0} ${r0} 0 ${large} 0 ${D[0].toFixed(2)} ${D[1].toFixed(2)} Z`;
}

export class TravauxDirection extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.fmtN = fmtN;
        this.fmtH = fmtH;
        this.help = {
            text: "Ce tableau sert à vérifier que le service fonctionne normalement.",
            steps: [
                "Aujourd'hui : quelles équipes ont de la marge, lesquelles sont surchargées.",
                "Cohérence : le temps déclaré sur les travaux correspond-il aux heures de présence du personnel ?",
                "Alertes : semaines anormales (surcharge ou heures non déclarées) et équipements à renouveler.",
            ],
        };
        this.TYPES = TYPES;
        this.state = useState({
            data: null, period: "12", supOff: {}, teamOff: {}, dept: "", focus: null, hoverLoad: null, hoverMonth: null,
        });
        onWillStart(() => this.load());
        useTravauxLive(() => this.load(), ["poll"]);
    }
    async load() {
        this.state.data = await this.orm.call("travaux.demande", "tv_dashboard", []);
    }
    // ---------------------------------------------------------------- filtres
    get n() {
        return parseInt(this.state.period);
    }
    get months() {
        return this.state.data.months.slice(12 - this.n);
    }
    get visible() {
        const dept = parseInt(this.state.dept) || 0;
        return this.state.data.teams.filter((t) => !this.state.supOff[t.sup_id] && !this.state.teamOff[t.id] &&
            (!dept || (t.dept_ids || []).includes(dept)));
    }
    isDeptSel(id) {
        return String(id) === String(this.state.dept);
    }
    onDept(ev) {
        this.state.dept = ev.target.value;
        this.state.focus = null;
    }
    deptLabel(d) {
        return "\u00A0\u00A0".repeat(d.depth) + d.name.split(" / ").pop();
    }
    get scoped() {
        return this.state.focus ? this.visible.filter((t) => t.id === this.state.focus) : this.visible;
    }
    toggleSup(id) {
        this.state.supOff[id] = !this.state.supOff[id];
        this.state.focus = null;
    }
    toggleTeam(id) {
        this.state.teamOff[id] = !this.state.teamOff[id];
        this.state.focus = null;
    }
    reset() {
        this.state.supOff = {};
        this.state.dept = "";
        this.state.teamOff = {};
        this.state.focus = null;
        this.state.period = "12";
    }
    focus(id) {
        this.state.focus = this.state.focus === id ? null : id;
    }
    hoverLoad(id) {
        this.state.hoverLoad = id;
    }
    hoverMonth(i) {
        this.state.hoverMonth = i;
    }
    // ---------------------------------------------------------------- agrégats
    agg(teamIds) {
        const months = this.months;
        const res = { tournee: 0, maintenance: 0, demande: 0, n: 0, byMonth: months.map(() => 0) };
        for (const r of this.state.data.rows) {
            if (!teamIds.includes(r.team)) continue;
            const mi = months.indexOf(r.month);
            if (mi < 0) continue;
            res[r.type] += r.h;
            if (r.type === "demande") {
                res.n += r.n;
                res.byMonth[mi] += r.n;
            }
        }
        res.total = res.tournee + res.maintenance + res.demande;
        return res;
    }
    presenceOf(teamIds) {
        const months = this.months;
        let h = 0;
        for (const r of this.state.data.presence || []) {
            if (teamIds.includes(r.team) && months.includes(r.month)) h += r.h;
        }
        return h;
    }
    statusOf(ratio, presence) {
        if (!presence) return { label: "Pas de présences", color: "#55606A", bg: "#EEF1F2" };
        if (ratio > 1.05) return { label: "Surcharge", color: "#B91C1C", bg: "#FDE2E2" };
        if (ratio < 0.5) return { label: "Heures non déclarées ?", color: "#9A3412", bg: "#FFF4E5" };
        if (ratio < 0.75) return { label: "Disponible", color: "#15803D", bg: "#DCF0E1" };
        return { label: "Normal", color: "#1E3A8A", bg: "#E4EDFB" };
    }
    /** Absentéisme par équipe : part des journées-agents non présentes, par type d'absence. */
    get absences() {
        const months = this.months;
        const KINDS = [["absent", "Absents", "#C2410C"], ["conge", "Congés", "#8FA3AA"], ["recup", "Récupérations", "#B9C6CB"], ["formation", "Formations", "#5B8DEF"]];
        const rows = this.visible.map((t) => {
            const acc = { membres: 0, presents: 0, absent: 0, conge: 0, recup: 0, formation: 0, sous_min: 0, jours: 0 };
            for (const r of this.state.data.absences || []) {
                if (r.team !== t.id || !months.includes(r.month)) continue;
                for (const k of Object.keys(acc)) acc[k] += r[k] || 0;
            }
            const off = Math.max(0, acc.membres - acc.presents);
            const known = acc.absent + acc.conge + acc.recup + acc.formation;
            return { t, acc, off, other: Math.max(0, off - known), rate: acc.membres ? off / acc.membres : 0 };
        });
        const maxR = Math.max(0.2, ...rows.map((x) => x.rate)) * 1.05;
        return {
            kinds: KINDS.map(([k, label, color]) => ({ k, label, color })).concat([{ k: "other", label: "Non détaillé", color: "#D9E0E3" }]),
            rows: rows.map((x) => ({
                id: x.t.id, name: x.t.name, pct: Math.round(x.rate * 100),
                opacity: this.state.focus && this.state.focus !== x.t.id ? 0.35 : 1,
                segs: KINDS.map(([k, label, color]) => ({ k, color, title: `${label} : ${x.acc[k]} journée(s)`, w: x.acc.membres ? ((x.acc[k] / x.acc.membres / maxR) * 100).toFixed(2) : 0 }))
                    .concat([{ k: "other", color: "#D9E0E3", title: `Non détaillé : ${x.other} journée(s)`, w: x.acc.membres ? ((x.other / x.acc.membres / maxR) * 100).toFixed(2) : 0 }]),
                jours: x.off, sousMin: x.acc.sous_min, nbJours: x.acc.jours,
                sousPct: x.acc.jours ? Math.round((x.acc.sous_min / x.acc.jours) * 100) : 0,
            })),
            total: (() => {
                const m = rows.reduce((a, x) => a + x.acc.membres, 0);
                return m ? Math.round((rows.reduce((a, x) => a + x.off, 0) / m) * 100) : 0;
            })(),
        };
    }
    get coherence() {
        const rows = this.visible.map((t) => {
            const a = this.agg([t.id]);
            const p = this.presenceOf([t.id]);
            const ratio = p ? a.total / p : 0;
            return { t, declared: a.total, presence: p, ratio, st: this.statusOf(ratio, p) };
        });
        const maxR = Math.max(1, ...rows.map((x) => x.ratio)) * 1.06;
        return rows.map((x) => ({
            id: x.t.id, name: x.t.name, pct: Math.round(x.ratio * 100),
            w: ((x.ratio / maxR) * 100).toFixed(1), cap: ((1 / maxR) * 100).toFixed(1),
            color: x.ratio > 1.05 ? "#E8590C" : x.ratio < 0.75 ? "#15803D" : "#276577",
            opacity: this.state.focus && this.state.focus !== x.t.id ? 0.35 : 1,
            declared: fmtN(x.declared), presence: fmtN(x.presence), st: x.st,
            gap: fmtN(x.declared - x.presence),
        }));
    }
    get occupation() {
        const ids = this.scoped.map((t) => t.id);
        const p = this.presenceOf(ids);
        const a = this.agg(ids);
        return p ? Math.round((a.total / p) * 100) : 0;
    }
    get todayCards() {
        const ids = this.visible.map((t) => t.id);
        return (this.state.data.today_rows || []).filter((r) => ids.includes(r.team)).map((r) => {
            let st;
            if (!r.presents) st = { label: "Personne", color: "#55606A", bg: "#EEF1F2" };
            else if (r.reste < 0) st = { label: `Surchargée · ${fmtH(-r.reste)} de trop`, color: "#B91C1C", bg: "#FDE2E2" };
            else if (r.reste >= 3) st = { label: `Disponible · ${fmtH(r.reste)} libres`, color: "#15803D", bg: "#DCF0E1" };
            else st = { label: "Journée remplie", color: "#1E3A8A", bg: "#E4EDFB" };
            return Object.assign({}, r, { st, bar: r.dispo ? Math.min(100, Math.round((r.prevu / r.dispo) * 100)) : 100 });
        }).sort((a, b) => a.reste - b.reste);
    }
    get alerts() {
        const names = {};
        for (const t of this.state.data.teams) names[t.id] = t.name;
        const ids = this.visible.map((t) => t.id);
        const out = [];
        const thisWeek = luxon.DateTime.local().startOf("week").toISODate();
        for (const w of this.state.data.weekly || []) {
            if (!ids.includes(w.team) || !w.presence || w.week >= thisWeek) continue;
            const r = w.declared / w.presence;
            const wk = d(w.week);
            const label = wk ? `Semaine du ${wk.day}/${wk.month}` : w.week;
            if (r > 1.15) out.push({ key: w.team + w.week, level: "danger", icon: "fa-exclamation-triangle", date: w.week,
                text: `${label} : ${names[w.team]} a déclaré ${fmtN(w.declared)} h pour ${fmtN(w.presence)} h de présence (${Math.round(r * 100)} %).` });
            else if (r < 0.5) out.push({ key: w.team + w.week, level: "warn", icon: "fa-question-circle", date: w.week,
                text: `${label} : ${names[w.team]} n'a déclaré que ${fmtN(w.declared)} h sur ${fmtN(w.presence)} h de présence : heures non imputées ?` });
        }
        return out.sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 7);
    }
    capOf(t) {
        return t.cap * 52 * 0.8 * (this.n / 12);
    }
    get kpis() {
        const a = this.agg(this.scoped.map((t) => t.id));
        const over = this.visible.filter((t) => this.agg([t.id]).total / this.capOf(t) > 1).length;
        return [
            { value: fmtN(a.n), label: "Demandes traitées", color: "#252423" },
            { value: fmtN(a.total) + " h", label: "Heures mesurées (toutes activités)", color: "#252423" },
            { value: (a.n ? a.demande / a.n : 0).toFixed(1).replace(".", ",") + " h", label: "Temps moyen par demande", color: "#252423" },
            { value: this.occupation + " %", label: "Temps déclaré / heures de présence", color: this.occupation > 105 ? "#C2410C" : "#252423" },
            { value: String(over), label: "Équipe(s) au-delà de leur capacité", color: over ? "#C2410C" : "#107C10" },
        ];
    }
    get loads() {
        const rows = this.visible.map((t) => {
            const a = this.agg([t.id]);
            return { t, a, r: this.capOf(t) ? a.total / this.capOf(t) : 0 };
        });
        const maxR = Math.max(1, ...rows.map((x) => x.r)) * 1.06;
        return rows.map((x) => ({
            id: x.t.id, name: x.t.name, pct: Math.round(x.r * 100),
            w: ((x.r / maxR) * 100).toFixed(1), cap: ((1 / maxR) * 100).toFixed(1),
            color: x.r > 1 ? "#E8590C" : "#276577", text: x.r > 1 ? "#C2410C" : "#252423",
            opacity: this.state.focus && this.state.focus !== x.t.id ? 0.35 : 1,
            hours: fmtN(x.a.total), capH: fmtN(this.capOf(x.t)), n: fmtN(x.a.n),
        }));
    }
    get loadTip() {
        return this.loads.find((l) => l.id === this.state.hoverLoad);
    }
    get donut() {
        const a = this.agg(this.scoped.map((t) => t.id));
        const vals = TYPES.map(([k]) => a[k]);
        const S = vals.reduce((x, y) => x + y, 0) || 1;
        let ang = 0;
        const paths = vals.map((v, i) => {
            const b = ang + (v / S) * Math.PI * 2;
            const dd = v > 0 ? arc(85, 85, 52, 80, ang, Math.min(b, ang + Math.PI * 2 - 0.0001)) : "";
            ang = b;
            return { d: dd, color: TYPES[i][2], key: TYPES[i][0] };
        });
        return {
            paths, total: fmtN(S),
            legend: TYPES.map(([k, label, color], i) => ({ k, label, color, pct: Math.round((vals[i] / S) * 100) })),
        };
    }
    get line() {
        const a = this.agg(this.scoped.map((t) => t.id));
        const series = a.byMonth;
        const n = series.length;
        const top = Math.max(10, Math.ceil(Math.max(...series, 1) / 20) * 20);
        const X0 = 36, X1 = 284, Y0 = 10, Y1 = 180;
        const xOf = (i) => (n === 1 ? (X0 + X1) / 2 : X0 + (i * (X1 - X0)) / (n - 1));
        const yOf = (v) => Y0 + (1 - v / top) * (Y1 - Y0);
        const grid = [];
        for (let g = 0; g <= top; g += top / 4) grid.push({ y: yOf(g).toFixed(1), ty: (yOf(g) + 3).toFixed(1), label: Math.round(g) });
        const hw = (X1 - X0) / Math.max(1, n - 1);
        const hm = this.state.hoverMonth != null && this.state.hoverMonth < n ? this.state.hoverMonth : null;
        const labels = this.months.map((m) => MOIS[parseInt(m.slice(5)) - 1]);
        return {
            grid,
            pts: series.map((v, i) => `${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`).join(" "),
            area: `${xOf(0).toFixed(1)},${Y1} ` + series.map((v, i) => `${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`).join(" ") + ` ${xOf(n - 1).toFixed(1)},${Y1}`,
            months: labels.map((m, i) => ({ i, label: m, lx: xOf(i).toFixed(1), hx: (xOf(i) - hw / 2).toFixed(1), hw: hw.toFixed(1) })),
            hover: hm != null ? {
                x: xOf(hm).toFixed(1), y: yOf(series[hm]).toFixed(1),
                tipX: xOf(hm) > 170 ? xOf(hm) - 150 : xOf(hm) + 10,
                text: `${MOIS_LONG[parseInt(this.months[hm].slice(5)) - 1]} : ${Math.round(series[hm])} demandes`,
            } : null,
            Y1,
        };
    }
    get matrix() {
        const rows = this.scoped.map((t) => ({ t, a: this.agg([t.id]) }));
        const vmax = Math.max(1, ...rows.flatMap((r) => TYPES.map(([k]) => r.a[k])));
        return rows.map((r) => ({
            id: r.t.id, name: r.t.name, total: fmtN(r.a.total),
            cells: TYPES.map(([k]) => {
                const x = r.a[k];
                const step = x <= 0 ? -1 : Math.min(5, Math.floor((x / vmax) * 6));
                return { k, v: fmtN(x), bg: step < 0 ? "#FFFFFF" : RAMP[step], fg: step >= 3 ? "#FFFFFF" : "#252423" };
            }),
        }));
    }
    get lieux() {
        const keys = this.scoped.map((t) => t.id);
        const months = this.months;
        const stats = {};
        for (const r of this.state.data.lieu_rows || []) {
            if (!months.includes(r.month) || !keys.includes(r.team)) continue;
            const st = (stats[r.lieu] = stats[r.lieu] || { n: 0, h: 0, teams: {} });
            st.n += r.n;
            st.h += r.h;
            st.teams[r.team] = (st.teams[r.team] || 0) + r.h;
        }
        const names = Object.fromEntries(this.state.data.teams.map((t) => [t.id, t.name]));
        const list = Object.entries(stats)
            .map(([id, st]) => {
                const l = (this.state.data.lieux || {})[id] || { name: "?", adresse: "" };
                const top = Object.entries(st.teams).sort((a, b) => b[1] - a[1]).slice(0, 2).map((x) => names[x[0]]).filter(Boolean);
                return { id: parseInt(id), name: l.name, adresse: l.adresse, n: st.n, h: st.h, teams: top.join(", ") };
            })
            .sort((a, b) => b.h - a.h)
            .slice(0, 8);
        const hmax = Math.max(1, ...list.map((e) => e.h));
        return list.map((e) => ({ ...e, bar: ((e.h / hmax) * 100).toFixed(1), hours: fmtN(e.h) + " h" }));
    }
    openLieu(id) {
        this.action.doAction({
            type: "ir.actions.act_window", name: "Travaux à ce lieu", res_model: "travaux.demande",
            views: [[false, "list"], [false, "form"]], domain: [["lieu_id", "=", id]],
        });
    }
    get periodLabel() {
        return this.n === 1 ? "dernier mois" : `${this.n} derniers mois`;
    }
}
TravauxDirection.template = "service_travaux.Direction";
TravauxDirection.components = { TvHelp };
registry.category("actions").add("travaux_direction", TravauxDirection);
