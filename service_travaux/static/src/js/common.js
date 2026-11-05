/** @odoo-module **/
import { onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { deserializeDateTime, deserializeDate } from "@web/core/l10n/dates";
import { loadJS, loadCSS } from "@web/core/assets";

/**
 * Temps réel : écoute le canal « travaux.live » (WebSocket Odoo) et rappelle
 * `callback(payload)` à chaque changement. Rafraîchissement de secours toutes les 60 s.
 */
export function useTravauxLive(callback, kinds = null) {
    const bus = useService("bus_service");
    const handler = (payload) => {
        if (!kinds || kinds.includes(payload.kind)) callback(payload);
    };
    let timer;
    onMounted(() => {
        bus.subscribe("travaux/update", handler);
        bus.addChannel("travaux.live");
        timer = setInterval(() => callback({ kind: "poll", ids: [] }), 60000);
    });
    onWillUnmount(() => {
        bus.unsubscribe("travaux/update", handler);
        clearInterval(timer);
    });
}

export function dt(value) {
    return value ? deserializeDateTime(value) : null;
}
export function d(value) {
    return value ? deserializeDate(value) : null;
}
const JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const JOURS_COURTS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"];

/** « 08:12 », « hier », « lundi », « 12/09 » comme dans une messagerie */
export function chatTime(value) {
    const t = dt(value);
    if (!t) return "";
    const now = luxon.DateTime.local();
    const days = Math.floor(now.startOf("day").diff(t.startOf("day"), "days").days);
    if (days <= 0) return t.toFormat("HH:mm");
    if (days === 1) return "hier";
    if (days < 7) return JOURS[t.weekday - 1];
    return t.year === now.year ? t.toFormat("dd/MM") : t.toFormat("dd/MM/yy");
}
export function dayLabel(value) {
    const t = dt(value);
    if (!t) return "";
    const now = luxon.DateTime.local();
    const days = Math.floor(now.startOf("day").diff(t.startOf("day"), "days").days);
    if (days <= 0) return "Aujourd'hui";
    if (days === 1) return "Hier";
    return JOURS[t.weekday - 1].replace(/^./, (c) => c.toUpperCase()) + " " + t.toFormat("dd/MM");
}
export function hhmm(value) {
    const t = dt(value);
    return t ? t.toFormat("HH:mm") : "";
}
export function shortDate(value) {
    // cette semaine : le jour (« Mar ») ; une autre semaine : la date (« 13/10 »)
    const x = d(value);
    if (!x) return "";
    const now = luxon.DateTime.local();
    if (x.weekNumber === now.weekNumber && x.weekYear === now.weekYear) {
        return JOURS_COURTS[x.weekday - 1].replace(/^./, (c) => c.toUpperCase());
    }
    return x.toFormat("dd/MM");
}
export function fmtH(x) {
    x = x || 0;
    const h = Math.floor(x);
    const m = Math.round((x - h) * 60);
    return m ? `${h} h ${String(m).padStart(2, "0")}` : `${h} h`;
}
export function fmtN(x) {
    return Math.round(x || 0).toLocaleString("fr-BE");
}
export function initials(name) {
    return (name || "?").trim().slice(0, 2).toUpperCase();
}
export function imgUrl(id, field, size = "") {
    return `/web/image/travaux.demande/${id}/${field}${size ? "/" + size : ""}`;
}
/** Lit un fichier image, le réduit (max 1600 px) et renvoie le base64 JPEG. */
export function readImage(file, max = 1600) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = reject;
        reader.onload = () => {
            const img = new Image();
            img.onload = () => {
                const r = Math.min(1, max / Math.max(img.width, img.height));
                const c = document.createElement("canvas");
                c.width = Math.round(img.width * r);
                c.height = Math.round(img.height * r);
                c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
                resolve(c.toDataURL("image/jpeg", 0.85).split(",")[1]);
            };
            img.onerror = reject;
            img.src = reader.result;
        };
        reader.readAsDataURL(file);
    });
}
/** Mêmes statuts et couleurs que les colonnes du kanban (maquette) */
export const STATES = [
    { key: "recue", title: "À attribuer · à planifier", short: "À attribuer", color: "#7C3AED" },
    { key: "planif", title: "Planifiées", short: "Planifiée", color: "#B45309" },
    { key: "cours", title: "En cours", short: "En cours", color: "#1D4ED8" },
    { key: "valider", title: "Terminées · à valider", short: "À valider", color: "#0F766E" },
    { key: "close", title: "Clôturées (7 jours)", short: "Clôturée", color: "#15803D" },
];
export const ST_STYLE = {
    recue: "background:#EDE4FF;color:#5B21B6",
    planif: "background:#FCEFD2;color:#854D0E",
    cours: "background:#DBE4FB;color:#1E3A8A",
    valider: "background:#CCEFE9;color:#115E59",
    close: "background:#DCF0E1;color:#14532D",
};
export const ST_RING = { recue: "#7C3AED", planif: "#B45309", cours: "#1D4ED8", valider: "#0F766E", close: "#15803D" };

/** Charge Leaflet (carte OpenStreetMap), fourni avec le module ; seules les tuiles viennent d'Internet. */
export function loadLeaflet(timeout = 8000) {
    if (window.L) return Promise.resolve(window.L);
    const load = Promise.all([
        loadCSS("/service_travaux/static/lib/leaflet/leaflet.css"),
        loadJS("/service_travaux/static/lib/leaflet/leaflet.js"),
    ]).then(() => window.L);
    const t = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), timeout));
    return Promise.race([load, t]).then((L) => {
        if (!L) throw new Error("leaflet");
        return L;
    });
}

// ---------------------------------------------------------------- # Lieux
/** Clé de comparaison d'un # : sans accents, majuscules, espaces ni ponctuation. */
export function tagKey(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");
}
function distM(lat1, lng1, lat2, lng2) {
    if (!lat1 || !lng1 || !lat2 || !lng2) return null;
    const rad = Math.PI / 180;
    const a = Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
        Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lng2 - lng1) * rad) / 2) ** 2;
    return 6371000 * 2 * Math.asin(Math.sqrt(a));
}
/**
 * # à suggérer : ceux dont le nom contient le texte tapé, sinon ceux aux alentours de la position
 * (du plus proche au plus loin), sinon les plus utilisés.
 */
export function suggestLieux(lieux, { q = "", lat = null, lng = null, rayon = 300, max = 6 } = {}) {
    const key = tagKey(q);
    const rows = (lieux || []).map((l) => {
        const dist = distM(lat, lng, l.lat, l.lng);
        return { ...l, dist, distLabel: dist === null ? "" : dist < 15 ? "ici" : `${Math.max(10, Math.round(dist / 10) * 10)} m` };
    });
    let list;
    if (key) {
        list = rows.filter((l) => tagKey(l.name).includes(key) || tagKey(l.adresse).includes(key));
    } else if (lat && lng) {
        list = rows.filter((l) => l.dist !== null && l.dist <= rayon);
    } else {
        list = rows.slice().sort((a, b) => b.n - a.n);
        return list.slice(0, max);
    }
    list.sort((a, b) => (a.dist === null) - (b.dist === null) || (a.dist || 0) - (b.dist || 0) || b.n - a.n);
    return list.slice(0, max);
}
