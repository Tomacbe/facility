/** @odoo-module **/
import { Component, useState, useRef, onWillStart, onPatched, onWillUnmount } from "@odoo/owl";
import { TvTimeline, TvHelp } from "./components";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import {
    useTravauxLive, chatTime, dayLabel, hhmm, fmtH, imgUrl, readImage, ST_STYLE, ST_RING, STATES, dt, d, shortDate,
    loadLeaflet,
    suggestLieux, tagKey,
} from "./common";

const FILTERS = [
    ["all", "Toutes"],
    ["recue", "À attribuer"],
    ["planif", "Planifiées"],
    ["cours", "En cours"],
    ["valider", "À valider"],
    ["close", "Clôturées"],
];

export class TravauxTerrain extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.ui = useState(useService("ui"));
        this.fileRef = useRef("file");
        this.docRef = useRef("doc");
        onWillUnmount(() => this.stopRecording(true));
        this.chatRef = useRef("chat");
        this.mapRef = useRef("map");
        this.FILTERS = FILTERS;
        this.ST_STYLE = ST_STYLE;
        this.ST_RING = ST_RING;
        this.chatTime = chatTime;
        this.hhmm = hhmm;
        this.fmtH = fmtH;
        this.shortDate = shortDate;
        this.imgUrl = imgUrl;
        const ctx = (this.props.action && this.props.action.context) || {};
        this.state = useState({
            data: { items: [], teams: [], categories: [] },
            filter: "all",
            search: "",
            team: "",
            selId: ctx.active_id || null,
            conv: null,
            draft: "",
            tab: "chats",
            viewer: null,
            hours: null,
            busy: false,
            sig: null,
            fileMode: "message",
            finishAfterUpload: false,
            imp: null,
            impOpen: false,
            rec: null,
            zones: { circuits: [] },
            zonePhoto: null,
            fin: null,
            headOpen: false,
            pos: null,        // position du téléphone : sert à trier, jamais envoyée au serveur
            near: false,
            convTab: "zones",
        });
        this.scrollNext = true;
        onWillStart(async () => {
            await Promise.all([this.loadList(), this.loadZones()]);
            if (this.state.selId) {
                await this.loadConv(this.state.selId);
            }
        });
        onPatched(() => {
            if (this.scrollNext && this.chatRef.el) {
                this.chatRef.el.scrollTop = this.chatRef.el.scrollHeight;
                this.scrollNext = false;
            }
            if (this.state.tab === "map" && this.mapRef.el && !this.mapRef.el._tvMap) {
                this.renderMap();
            }
        });
        useTravauxLive(async (payload) => {
            if (payload.kind === "zones") {
                return this.loadZones();
            }
            await this.loadList();
            if (this.state.selId && (payload.kind === "poll" || (payload.ids || []).includes(this.state.selId))) {
                const before = this.state.conv ? this.state.conv.messages.length : 0;
                await this.loadConv(this.state.selId, true);
                if (this.state.conv && this.state.conv.messages.length !== before) {
                    this.scrollNext = true;
                }
            }
            if (payload.kind !== "demande") await this.loadZones();
        }, ["demande", "poll", "zones"]);
    }

    setFilter(f) {
        this.state.filter = f;
    }
    closeViewer() {
        this.state.viewer = null;
    }
    selectCat(id) {
        this.state.sig.categorie_id = id;
        if (!this.state.sig.titre) {
            const cat = this.state.data.categories.find((c) => c.id === id);
            this.state.sig.titre = cat ? cat.name : "";
        }
    }

    // ------------------------------------------------------------ données
    async loadList() {
        this.state.data = await this.orm.call("travaux.demande", "tv_terrain_list", []);
    }
    async loadConv(id, silent = false) {
        try {
            this.state.conv = await this.orm.call("travaux.demande", "tv_conversation", [id]);
            if (!silent) {
                this.scrollNext = true;
            }
        } catch {
            this.state.conv = null;
            this.state.selId = null;
        }
    }
    // ------------------------------------------------------------ zones (tournées)
    async loadZones() {
        try {
            this.state.zones = await this.orm.call("travaux.circuit", "tv_zones", []);
        } catch {
            this.state.zones = { circuits: [] };
        }
    }
    get hasZones() {
        return this.state.zones.circuits.length > 0;
    }
    get zonesLeft() {
        return 0;   // une zone non faite n'est pas un retard : pas de compteur
    }
    zonesTriees(c) {
        // feuille de passage : la zone entretenue il y a le plus longtemps (ou jamais) en premier
        const age = (z) => (z.days === null || z.days === undefined ? 1e9 : z.days);
        return [...c.zones].sort((a, b) => age(b) - age(a));
    }
    // ------------------------------------------------------------ position du téléphone (« Près de moi »)
    /** Demande la position au téléphone. Elle reste sur le téléphone : elle ne sert qu'à trier les listes. */
    locateMe(silent = false) {
        return new Promise((resolve) => {
            if (!navigator.geolocation) {
                if (!silent) this.notification.add("Ce téléphone ne donne pas sa position.", { type: "warning" });
                return resolve(null);
            }
            navigator.geolocation.getCurrentPosition(
                (p) => {
                    this.state.pos = { lat: p.coords.latitude, lng: p.coords.longitude };
                    resolve(this.state.pos);
                },
                () => {
                    if (!silent) {
                        this.notification.add("Position indisponible : autorisez la localisation pour ce site (il doit être en https).", { type: "warning" });
                    }
                    resolve(null);
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
            );
        });
    }
    async toggleNear() {
        if (this.state.near) {
            this.state.near = false;
            return;
        }
        if (await this.locateMe()) this.state.near = true;
    }
    dist(lat, lng) {
        const p = this.state.pos;
        if (!p || !lat || !lng) return null;
        const r = Math.PI / 180;
        const a = Math.sin(((lat - p.lat) * r) / 2) ** 2 +
            Math.cos(p.lat * r) * Math.cos(lat * r) * Math.sin(((lng - p.lng) * r) / 2) ** 2;
        return 12742000 * Math.asin(Math.sqrt(a));
    }
    fmtDist(m) {
        if (m === null || m === undefined) return "";
        return m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1).replace(".", ",")} km`;
    }
    itemDist(it) {
        if ((it.pts || []).length) {
            const all = it.pts.map((p) => this.dist(p[0], p[1])).filter((x) => x !== null);
            return all.length ? Math.min(...all) : null;
        }
        return this.dist(it.lat, it.lng);
    }
    // ------------------------------------------------------------ tournée : les zones dans la discussion
    /** En cours d'abord, puis à faire (la plus proche, ou la plus ancienne), puis les zones passées. */
    get tzZones() {
        const zones = (this.state.conv && this.state.conv.tz) || [];
        const rank = { cours: 0, todo: 1, fait: 2, inutile: 2 };
        const age = (z) => (z.days === null || z.days === undefined ? 1e9 : z.days);
        return [...zones].map((z) => ({ ...z, m: this.dist(z.lat, z.lng) })).sort((a, b) => {
            if (rank[a.status] !== rank[b.status]) return rank[a.status] - rank[b.status];
            if (a.status === "todo") {
                if (a.m !== null || b.m !== null) return (a.m === null ? 1e12 : a.m) - (b.m === null ? 1e12 : b.m);
                return age(b) - age(a);
            }
            return 0;
        });
    }
    get tzLeft() {
        return ((this.state.conv && this.state.conv.tz) || []).filter((z) => z.status === "todo" || z.status === "cours").length;
    }
    get tzNearest() {
        const z = this.tzZones.find((x) => x.status === "todo" && x.m !== null);
        return z ? z.id : null;
    }
    async tzCall(method, z, extra = []) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            this.state.conv = await this.orm.call("travaux.demande", method, [this.state.conv.id, z.id, ...extra]);
            await this.loadList();
        } finally {
            this.state.busy = false;
        }
    }
    tzDemarrer(z) {
        return this.tzCall("tv_tz_demarrer", z);
    }
    async tzStop(z) {
        if (this.state.fin && this.state.fin.zone === z.id) this.state.fin = null;
        await this.tzCall("tv_tz_stop", z);
    }
    tzInutile(z) {
        return this.tzCall("tv_tz_inutile", z);
    }
    tzAnnuler(z) {
        return this.tzCall("tv_tz_annuler", z);
    }
    async tzFin(z) {
        const fin = await this.orm.call("travaux.demande", "tv_tz_presents", [this.state.conv.id, z.id]);
        this.state.fin = { ...fin, pct: null, more: false, zone: z.id, tz: true, duree: "" };
        this.state.fin.duree = String(this.quart(this.finClock));
    }
    tzMeta(z) {
        if (z.status === "cours") return `en cours depuis ${hhmm(z.debut)}`;
        if (z.status === "fait") return `faite ${shortDate(z.date)} · ${fmtH(z.h)}`;
        if (z.status === "inutile") return `pas nécessaire · ${shortDate(z.date)}`;
        return this.zoneAge(z);
    }
    quart(h) {
        return Math.max(0.25, Math.round((h || 0) * 4) / 4);
    }
    get finDuree() {
        return parseFloat(String((this.state.fin && this.state.fin.duree) || "").replace(",", ".")) || 0;
    }
    get finMax() {
        return (this.state.fin && this.state.fin.max_jour) || 7.5;
    }
    get finCount() {
        const f = this.state.fin;
        return f ? [...f.people, ...f.others].filter((e) => e.on).length : 0;
    }
    get finClock() {
        const debut = this.state.fin && this.state.fin.debut;
        if (!debut) return 0;
        const start = new Date(debut.replace(" ", "T") + "Z").getTime();
        return Math.max(0, (Date.now() - start) / 3600000);
    }
    async confirmerFin() {
        const f = this.state.fin;
        if (!f || this.state.busy) return;
        const ids = [...f.people, ...f.others].filter((e) => e.on).map((e) => e.id);
        this.state.busy = true;
        try {
            if (f.tz) {
                this.state.conv = await this.orm.call("travaux.demande", "tv_tz_fait", [this.state.conv.id, f.zone, ids, this.finDuree]);
                this.state.fin = null;
                await this.loadList();
                return;
            }
            if (f.zone) {
                this.state.zones = await this.orm.call("travaux.circuit.zone", "tv_zone_fait", [f.zone, ids, this.finDuree]);
                this.state.fin = null;
                await this.loadList();
                return;
            }
            this.state.conv = await this.orm.call("travaux.demande", "tv_terminer", [this.state.conv.id, ids, f.pct || false, this.finDuree]);
            this.state.fin = null;
            this.scrollNext = true;
            await this.loadList();
        } finally {
            this.state.busy = false;
        }
    }
    // zones « une par une » : Démarrer, puis Fait (présents) ; tournée « en lot » : un seul bon
    async zoneDemarrer(z) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            this.state.zones = await this.orm.call("travaux.circuit.zone", "tv_zone_demarrer", [z.id]);
        } finally {
            this.state.busy = false;
        }
    }
    async zoneStop(z) {
        this.state.zones = await this.orm.call("travaux.circuit.zone", "tv_zone_stop", [z.id]);
        if (this.state.fin && this.state.fin.zone === z.id) this.state.fin = null;
    }
    async zoneFin(z) {
        const fin = await this.orm.call("travaux.circuit.zone", "tv_zone_presents", [z.id]);
        this.state.fin = { ...fin, pct: null, more: false, zone: z.id, duree: "" };
        this.state.fin.duree = String(this.quart(this.finClock));
    }
    async lotDemarrer(c) {
        const id = c.lot_id || (await this.orm.call("travaux.circuit", "tv_lot_demarrer", [c.id]));
        await Promise.all([this.loadList(), this.loadZones()]);
        await this.open(id);
    }
    zoneDebut(z) {
        return z.debut ? hhmm(z.debut) : "";
    }
    zoneAge(z) {
        if (z.days === null || z.days === undefined) return "jamais fait";
        if (z.days === 0) return "fait aujourd'hui";
        if (z.days === 1) return "fait hier";
        return `il y a ${z.days} jours`;
    }
    async zoneFait(z) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            const tours = Object.fromEntries(this.state.zones.circuits.map((c) => [c.id, c.tour]));
            this.state.zones = await this.orm.call("travaux.circuit.zone", "tv_fait", [z.id]);
            const boucle = this.state.zones.circuits.find((c) => tours[c.id] && c.tour > tours[c.id]);
            if (boucle) {
                this.notification.add(`Tour terminé pour « ${boucle.name} ». Un nouveau tour commence.`, { type: "success" });
            }
        } finally {
            this.state.busy = false;
        }
    }
    async zoneAnnuler(z) {
        this.state.zones = await this.orm.call("travaux.circuit.zone", "tv_annuler", [z.id]);
    }
    zonePick(z) {
        this.state.zonePhoto = z.id;
        this.fileRef.el.value = "";
        this.fileRef.el.click();
    }
    zoneView(z) {
        this.state.viewer = { single: z.photo, tag: z.name };
    }

    get items() {
        const q = this.state.search.trim().toLowerCase();
        return this.state.data.items.filter((it) => {
            const f = this.state.filter;
            if (f !== "all" && it.st !== f) return false;
            if (!this.inOrgFilter(it)) return false;
            if (q && !`${it.titre} ${it.ref} ${it.adresse} ${it.lieu} ${(it.objets || []).map((o) => o.name).join(" ")} ${it.equipe}`.toLowerCase().includes(q)) return false;
            return true;
        });
    }
    count(f) {
        return this.state.data.items.filter((it) => f === "all" || it.st === f).length;
    }
    /** Liste groupée par statut, dans l'ordre des colonnes du kanban */
    get sections() {
        const items = this.items;
        if (this.state.near && this.state.pos) {
            // « Près de moi » : dans chaque statut, la plus proche en premier
            const m = (it) => {
                const x = this.itemDist(it);
                return x === null ? 1e12 : x;
            };
            items.sort((a, b) => m(a) - m(b));
        }
        if (this.state.filter !== "all") {
            return [{ key: this.state.filter, title: "", items }];
        }
        return STATES.map((s) => ({ key: s.key, title: s.title, color: s.color, items: items.filter((it) => it.st === s.key) }))
            .filter((sec) => sec.items.length);
    }
    get statutChoices() {
        return [
            ["recue:0", "À attribuer (sans équipe)"], ["recue:1", "À planifier (garde l'équipe)"], ["planif", "Planifiée"],
            ["cours", "En cours"], ["valider", "Terminée · à valider"], ["close", "Clôturée"],
        ];
    }
    async onStatutSelect(ev) {
        const v = ev.target.value;
        ev.target.value = "";
        if (!v) return;
        const [state, keep] = v.split(":");
        this.state.conv = await this.orm.call("travaux.demande", "tv_set_state", [this.state.conv.id, state, keep !== "0"]);
        await this.loadList();
    }
    get subtitle() {
        const t = this.state.data.my_team;
        return (this.state.data.company || "") + (t ? " · équipe " + t.name : "");
    }

    // ------------------------------------------------------------ conversation
    async open(id) {
        this.state.selId = id;
        this.state.tab = "chats";
        this.state.draft = "";
        this.state.convTab = "zones";
        this.state.fin = null;
        await this.loadConv(id);
        if (this.state.conv && this.state.conv.tz && !this.state.pos) {
            this.locateMe(true);   // pour proposer la zone la plus proche, sans insister si refusé
        }
    }
    back() {
        this.state.selId = null;
        this.state.conv = null;
    }
    get groupedMessages() {
        const out = [];
        let lastDay = null;
        for (const m of (this.state.conv && this.state.conv.messages) || []) {
            const day = dt(m.date).toISODate();
            if (day !== lastDay) {
                out.push({ key: "d" + m.id, kind: "day", text: dayLabel(m.date) });
                lastDay = day;
            }
            out.push(Object.assign({ key: "m" + m.id }, m));
        }
        return out;
    }
    authorColor(name) {
        const palette = ["#B4531A", "#0F766E", "#1D4ED8", "#7C3AED", "#9A3412", "#BE185D", "#15803D"];
        let h = 0;
        for (const c of name || "") h = (h * 31 + c.charCodeAt(0)) % 997;
        return palette[h % palette.length];
    }
    async send(ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        const body = this.state.draft.trim();
        if (!body || !this.state.conv) return;
        this.state.draft = "";
        this.state.conv = await this.orm.call("travaux.demande", "tv_post", [this.state.conv.id, body]);
        this.scrollNext = true;
    }
    onKey(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            this.send(ev);
        }
    }
    // ---------------------------------------------------------------- organigramme
    get orgTeams() {
        const out = {};
        for (const g of this.state.data.org || []) {
            for (const t of g.teams) out[t.id] = g.sup_id;
        }
        return out;
    }
    inOrgFilter(it) {
        const f = this.state.team;
        if (!f) return true;
        const [kind, id] = f.split(":");
        if (kind === "team") return String(it.equipe_id) === id;
        if (kind === "sup") return !it.equipe_id ? true : String(this.orgTeams[it.equipe_id]) === id;
        if (kind === "dept") {
            const t = (this.state.data.teams || []).find((x) => x.id === it.equipe_id);
            return !!t && (t.dept_ids || []).includes(parseInt(id));
        }
        if (kind === "none") return !it.equipe_id;
        return true;
    }
    onOrg(ev) {
        this.state.team = ev.target.value;
    }
    isOrg(v) {
        return this.state.team === v;
    }
    get showOrg() {
        const org = this.state.data.org || [];
        return this.state.data.scope !== "equipe" && (org.length > 1 || (org[0] && org[0].teams.length > 1));
    }
    // ---------------------------------------------------------------- pièces jointes et vocal
    pickDoc() {
        this.docRef.el.value = "";
        this.docRef.el.click();
    }
    async onDoc(ev) {
        const file = ev.target.files && ev.target.files[0];
        if (!file) return;
        if (file.size > 20 * 1024 * 1024) {
            this.notification.add("Fichier trop lourd (20 Mo maximum).", { type: "warning" });
            return;
        }
        if (file.type.startsWith("image/")) {
            return this.onFile(ev);
        }
        const b64 = await new Promise((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve(String(r.result).split(",")[1]);
            r.onerror = reject;
            r.readAsDataURL(file);
        });
        await this.sendAttachment(file.name, file.type, b64);
    }
    async sendAttachment(name, mimetype, b64) {
        this.state.busy = true;
        try {
            const text = this.state.draft.trim();
            this.state.draft = "";
            this.state.conv = await this.orm.call("travaux.demande", "tv_attach", [this.state.conv.id, name, mimetype, b64, text]);
            this.scrollNext = true;
        } finally {
            this.state.busy = false;
        }
    }
    async toggleRecording() {
        if (this.state.rec) {
            return this.stopRecording(false);
        }
        if (!navigator.mediaDevices || !window.MediaRecorder) {
            this.notification.add("L'enregistrement vocal n'est pas disponible sur cet appareil.", { type: "warning" });
            return;
        }
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch {
            this.notification.add("Micro refusé : autorisez le micro dans le navigateur.", { type: "warning" });
            return;
        }
        const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
        const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
        const chunks = [];
        rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
        rec.onstop = async () => {
            stream.getTracks().forEach((t) => t.stop());
            clearInterval(this._recTimer);
            if (this._recCancel || !chunks.length) return;
            const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
            const ext = (rec.mimeType || "").includes("mp4") ? "m4a" : (rec.mimeType || "").includes("ogg") ? "ogg" : "webm";
            const b64 = await new Promise((resolve) => {
                const r = new FileReader();
                r.onload = () => resolve(String(r.result).split(",")[1]);
                r.readAsDataURL(blob);
            });
            const stamp = luxon.DateTime.local().toFormat("HH'h'mm");
            await this.sendAttachment(`message-vocal-${stamp}.${ext}`, (rec.mimeType || "audio/webm").split(";")[0], b64);
        };
        this._recCancel = false;
        this._rec = rec;
        rec.start();
        this.state.rec = { sec: 0 };
        this._recTimer = setInterval(() => {
            if (this.state.rec) this.state.rec.sec++;
        }, 1000);
    }
    stopRecording(cancel) {
        if (!this._rec) return;
        this._recCancel = cancel;
        if (this._rec.state !== "inactive") this._rec.stop();
        this._rec = null;
        this.state.rec = null;
    }
    get recTime() {
        const s = (this.state.rec && this.state.rec.sec) || 0;
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }
    fileSize(n) {
        if (!n) return "";
        return n > 1048576 ? (n / 1048576).toFixed(1).replace(".", ",") + " Mo" : Math.max(1, Math.round(n / 1024)) + " ko";
    }
    fileIcon(f) {
        const m = f.mimetype || "";
        if (m.includes("pdf")) return "fa-file-pdf-o";
        if (m.includes("sheet") || m.includes("excel") || f.name.endsWith(".csv")) return "fa-file-excel-o";
        if (m.includes("word") || m.includes("document")) return "fa-file-word-o";
        if (m.startsWith("video/")) return "fa-file-video-o";
        return "fa-file-o";
    }

    pickPhoto(mode) {
        this.state.fileMode = mode || (this.state.conv && this.state.conv.state === "cours" ? "apres" : "message");
        this.fileRef.el.value = "";
        this.fileRef.el.click();
    }
    async onFile(ev) {
        const file = ev.target.files && ev.target.files[0];
        if (!file) return;
        const b64 = await readImage(file);
        if (this.state.zonePhoto) {
            const zid = this.state.zonePhoto;
            this.state.zonePhoto = null;
            this.state.zones = await this.orm.call("travaux.circuit.zone", "tv_fait", [zid, b64]);
            return;
        }
        if (this.state.sig) {
            this.state.sig.photo = b64;
            return;
        }
        this.state.busy = true;
        try {
            const text = this.state.draft.trim();
            this.state.draft = "";
            this.state.conv = await this.orm.call("travaux.demande", "tv_photo",
                [this.state.conv.id, b64, this.state.fileMode, text]);
            this.scrollNext = true;
            if (this.state.finishAfterUpload) {
                this.state.finishAfterUpload = false;
                await this.doAction("terminer");
            }
        } finally {
            this.state.busy = false;
        }
    }
    async doAction(name) {
        if (name === "terminer" && this.state.conv.type === "demande" && !this.state.conv.has_apres) {
            this.notification.add("Prenez la photo « après » : la durée sera calculée ensuite.", { type: "info" });
            this.state.finishAfterUpload = true;
            this.pickPhoto("apres");
            return;
        }
        if (name === "terminer") {
            // avant de terminer : l'équipe confirme qui était présent (et la part du circuit pour une tournée)
            const fin = await this.orm.call("travaux.demande", "tv_presents", [this.state.conv.id]);
            this.state.fin = { ...fin, pct: null, more: false, debut: this.state.conv.date_debut, duree: "" };
            this.state.fin.duree = String(this.quart(this.finClock));
            return;
        }
        this.state.busy = true;
        try {
            this.state.conv = await this.orm.call("travaux.demande", "tv_do", [this.state.conv.id, name]);
            this.scrollNext = true;
            await this.loadList();
        } finally {
            this.state.busy = false;
        }
    }
    async corrigerDuree() {
        const c = this.state.conv;
        const cur = (c.nb_presents ? c.duree_horloge : c.duree_mesuree) || 0;
        const quoi = c.nb_presents > 1 ? `Durée sur place en heures (elle sera multipliée par ${c.nb_presents} personnes)` : "Durée réelle en heures (ex. 1.5 pour 1 h 30)";
        const val = window.prompt(quoi, String(cur).replace(".", ","));
        if (val === null) return;
        const h = parseFloat(val.replace(",", "."));
        if (isNaN(h) || h < 0) return;
        this.state.conv = await this.orm.call("travaux.demande", "tv_set_duree", [this.state.conv.id, h]);
    }
    openBureau() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "travaux.demande", res_id: this.state.conv.id,
            views: [[false, "form"]], target: "current",
        });
    }
    viewPhotos(m) {
        const c = this.state.conv;
        this.state.viewer = {
            avant: c.has_avant ? imgUrl(c.id, "photo_avant") : null,
            apres: c.has_apres ? imgUrl(c.id, "photo_apres") : null,
            single: m && m.images && m.images[0] ? m.images[0].url : null,
            tag: m && m.tag,
        };
    }

    // ------------------------------------------------------------ onglets
    async loadHours() {
        this.state.hours = await this.orm.call("travaux.demande", "tv_heures", []);
        if (!this.state.imp || !this.state.imp.task) {
            const t = this.state.hours.tasks[0];
            this.state.imp = { task: t ? String(t.id) : "", h: "", date: this.state.hours.today };
        }
    }
    isImpTask(id) {
        return this.state.imp && String(id) === String(this.state.imp.task);
    }
    onImp(field, ev) {
        this.state.imp[field] = ev.target.value;
    }
    async addImputation(recId = null) {
        const imp = this.state.imp || {};
        const id = recId || parseInt(imp.task);
        const h = parseFloat(String(imp.h || "").replace(",", "."));
        if (!id || !h) {
            this.notification.add("Choisissez le travail et le nombre d'heures.", { type: "warning" });
            return;
        }
        const conv = await this.orm.call("travaux.demande", "tv_add_pointage", [id, { h, date: imp.date }]);
        if (recId) {
            this.state.conv = conv;
            this.state.impOpen = false;
        }
        this.state.imp.h = "";
        this.notification.add(`${fmtH(h)} imputées à l'équipe.`, { type: "success" });
        if (this.state.tab === "hours") await this.loadHours();
    }
    toggleImp() {
        this.state.impOpen = !this.state.impOpen;
        if (!this.state.imp) this.state.imp = { task: "", h: "", date: luxon.DateTime.local().toISODate() };
    }
    async showTab(tab) {
        this.state.tab = tab;
        if (tab === "hours") {
            await this.loadHours();
        }
        if (tab === "zones") {
            await this.loadZones();
        }
        // sur ordinateur aussi : la carte prend la place de la conversation ouverte, sinon l'icône semble ne rien faire
        if (this.ui.isSmall || tab !== "chats") {
            this.state.selId = null;
            this.state.conv = null;
        }
    }
    get hoursByDay() {
        const out = [];
        let last = null;
        const JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
        for (const r of (this.state.hours && this.state.hours.rows) || []) {
            if (r.date !== last) {
                const x = d(r.date);
                const label = r.date === this.state.hours.today ? "Aujourd'hui" : `${JOURS[x.weekday - 1]} ${x.day}/${x.month}`;
                out.push({ key: "d" + r.date, day: label, rows: [], total: 0 });
                last = r.date;
            }
            out[out.length - 1].rows.push(r);
            out[out.length - 1].total += r.h;
        }
        return out;
    }
    get hoursLoad() {
        const h = this.state.hours;
        if (!h || !h.cap) return null;
        const pct = Math.round((h.planned / h.cap) * 100);
        return {
            pct, bar: Math.min(100, pct),
            label: pct > 95 ? "Semaine chargée" : pct < 60 ? "Marge disponible" : "Charge normale",
            color: pct > 95 ? "#B91C1C" : pct < 60 ? "#15803D" : "#4A514D",
        };
    }
    async renderMap() {
        const el0 = this.mapRef.el;
        if (!el0 || el0._tvMap || el0._tvLoading) return;
        el0._tvLoading = true;
        try {
            await loadLeaflet();
        } catch {
            el0.innerHTML = '<div class="o_tv_empty">Carte indisponible : pas d\'accès à OpenStreetMap depuis ce poste.</div>';
            return;
        }
        const el = this.mapRef.el;
        if (!el || el._tvMap) return;
        const L = window.L;
        const map = L.map(el).setView([50.7106, 4.529], 15);
        el._tvMap = map;
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19, attribution: "© OpenStreetMap",
        }).addTo(map);
        const ring = ST_RING;
        const pts = [];
        for (const it of this.state.data.items) {
            if (!it.lat) continue;
            const num = (it.ref || "").split("-").pop();
            const html = `<div class="o_tv_pinnum" style="border-color:${ring[it.st]}">${num}</div>`;
            const m = L.marker([it.lat, it.lng], { icon: L.divIcon({ html, className: "", iconSize: [52, 26], iconAnchor: [26, 13] }) })
                .addTo(map).bindTooltip(`${it.ref} · ${it.titre} – ${it.lieu || it.adresse}`);
            m.on("click", () => this.open(it.id));
            pts.push([it.lat, it.lng]);
        }
        if (pts.length > 1) map.fitBounds(pts, { padding: [40, 40] });
        setTimeout(() => map.invalidateSize(), 200);
    }
    get noPosition() {
        return this.state.data.items.filter((it) => !it.lat).length;
    }

    // ------------------------------------------------------------ signaler
    startSignaler() {
        this.state.sig = { photo: null, categorie_id: null, titre: "", adresse: "", lieu: "", objet: "", objets: [], description: "", lat: null, lng: null, locating: false };
    }
    get sigLieux() {
        const s = this.state.sig;
        const valid = (this.state.data.lieux || []).filter((l) => l.state === "valide" && l.kind === "lieu");
        return suggestLieux(valid, { q: s.lieu, lat: s.lat, lng: s.lng, rayon: this.state.data.rayon, max: 6 })
            .map((l) => ({ ...l, key: tagKey(l.name) }));
    }
    get sigLieuKey() {
        return tagKey(this.state.sig.lieu);
    }
    get sigLieuNew() {
        const key = this.sigLieuKey;
        return !!key && !(this.state.data.lieux || []).some((l) => tagKey(l.name) === key);
    }
    get sigObjets() {
        const s = this.state.sig;
        const key = tagKey(s.objet);
        const have = s.objets.map(tagKey);
        return (this.state.data.lieux || [])
            .filter((l) => l.kind === "objet" && l.state === "valide" && !have.includes(tagKey(l.name)) && (!key || tagKey(l.name).includes(key)))
            .sort((a, b) => b.n - a.n)
            .slice(0, 8);
    }
    onSigObj(ev) {
        this.state.sig.objet = ev.target.value.replace(/^#+/, "");
    }
    onSigObjKey(ev) {
        if (ev.key === "Enter") this.sigAddObj(this.state.sig.objet);
    }
    sigAddObj(name) {
        const s = this.state.sig;
        name = (name || "").trim();
        // un # déjà connu garde son écriture officielle
        const known = (this.state.data.lieux || []).find((l) => l.kind === "objet" && tagKey(l.name) === tagKey(name));
        if (known) name = known.name;
        if (name && !s.objets.map(tagKey).includes(tagKey(name))) s.objets.push(name);
        s.objet = "";
    }
    sigDelObj(name) {
        const s = this.state.sig;
        s.objets = s.objets.filter((o) => o !== name);
    }
    onSigLieu(ev) {
        this.state.sig.lieu = ev.target.value.replace(/^#+/, "");
    }
    pickLieu(l) {
        const s = this.state.sig;
        s.lieu = l.name;
        if (!s.adresse || s.adresse === "Position GPS") s.adresse = l.adresse || s.adresse;
    }
    cancelSignaler() {
        this.state.sig = null;
    }
    locate() {
        if (!navigator.geolocation) return;
        this.state.sig.locating = true;
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                this.state.sig.lat = pos.coords.latitude;
                this.state.sig.lng = pos.coords.longitude;
                this.state.sig.locating = false;
                if (!this.state.sig.adresse) this.state.sig.adresse = "Position GPS";
            },
            () => {
                this.state.sig.locating = false;
                this.notification.add("Position indisponible : indiquez l'adresse.", { type: "warning" });
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }
    async sendSignaler() {
        const s = this.state.sig;
        if (!s.photo && !s.description && !s.titre) {
            this.notification.add("Ajoutez au moins une photo ou un message.", { type: "warning" });
            return;
        }
        this.state.busy = true;
        try {
            const id = await this.orm.call("travaux.demande", "tv_signaler", [{
                photo: s.photo, categorie_id: s.categorie_id, titre: s.titre, adresse: s.adresse, lieu: s.lieu,
                objets: s.objet ? s.objets.concat([s.objet]) : s.objets,
                description: s.description, lat: s.lat, lng: s.lng,
            }]);
            this.state.sig = null;
            await this.loadList();
            await this.open(id);
            this.notification.add("Signalement envoyé au superviseur.", { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
}
TravauxTerrain.template = "service_travaux.Terrain";
TravauxTerrain.components = { TvTimeline, TvHelp };

registry.category("actions").add("travaux_terrain", TravauxTerrain);
