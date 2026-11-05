/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadLeaflet } from "@service_travaux/js/common";

const WALLONIE = "https://geoservices.wallonie.be/arcgis/rest/services";
const ETATS = { fait: "#2E8B57", todo: "#D07A1F", lieu: "#276577" };
const PICC_ZOOM = 17;
const DRAW_TOOLS = ["point", "ligne", "surface", "lieu"];
const GLABEL = { point: "Points", ligne: "Lignes", surface: "Surfaces" };

/** Tuiles 256 px tirées d'un service ArcGIS du SPW (export reprojeté). */
export function arcgisLayer(L, url, layers, options = {}) {
    const Layer = L.TileLayer.extend({
        getTileUrl(coords) {
            const size = this.getTileSize();
            const nw = coords.scaleBy(size);
            const se = nw.add(size);
            const a = L.CRS.EPSG3857.project(this._map.unproject(nw, coords.z));
            const b = L.CRS.EPSG3857.project(this._map.unproject(se, coords.z));
            const p = new URLSearchParams({
                bbox: `${a.x},${b.y},${b.x},${a.y}`, bboxSR: "3857", imageSR: "3857",
                size: `${size.x},${size.y}`, format: "png32", transparent: "true", f: "image",
            });
            if (layers) p.set("layers", `show:${layers}`);
            return `${url}/export?${p.toString()}`;
        },
    });
    return new Layer("", { tileSize: 256, maxZoom: 21, ...options });
}

// ---------------------------------------------------------------- géométrie (GeoJSON [lng, lat] ↔ Leaflet [lat, lng])
const toLL = (c) => [c[1], c[0]];
const toXY = (ll) => [Math.round(ll.lng * 1e7) / 1e7, Math.round(ll.lat * 1e7) / 1e7];

export function lengthM(lls) {
    let d = 0;
    for (let i = 1; i < lls.length; i++) d += window.L.latLng(lls[i - 1]).distanceTo(lls[i]);
    return d;
}
export function areaM2(lls) {
    if (lls.length < 3) return 0;
    const R = 6371008.8, lat0 = (lls[0].lat ?? lls[0][0]) * Math.PI / 180;
    const pts = lls.map((p) => {
        const lat = p.lat ?? p[0], lng = p.lng ?? p[1];
        return [lng * Math.PI / 180 * R * Math.cos(lat0), lat * Math.PI / 180 * R];
    });
    let s = 0;
    for (let i = 0; i < pts.length; i++) {
        const a = pts[i], b = pts[(i + 1) % pts.length];
        s += a[0] * b[1] - b[0] * a[1];
    }
    return Math.abs(s / 2);
}
const fmt = (n) => Math.round(n).toLocaleString("fr-BE");

/** Partie modifiable d'une géométrie : le premier anneau extérieur ou la première ligne. */
function editable(geom) {
    const t = geom.type, c = geom.coordinates;
    if (t === "Polygon") {
        return { g: "surface", pts: c[0].slice(0, -1).map(toLL), rebuild: (pts) => ({ type: t, coordinates: [close(pts), ...c.slice(1)] }) };
    }
    if (t === "MultiPolygon") {
        return { g: "surface", pts: c[0][0].slice(0, -1).map(toLL),
            rebuild: (pts) => ({ type: t, coordinates: [[close(pts), ...c[0].slice(1)], ...c.slice(1)] }) };
    }
    if (t === "LineString") return { g: "ligne", pts: c.map(toLL), rebuild: (pts) => ({ type: t, coordinates: pts.map(xy) }) };
    if (t === "MultiLineString") {
        return { g: "ligne", pts: c[0].map(toLL), rebuild: (pts) => ({ type: t, coordinates: [pts.map(xy), ...c.slice(1)] }) };
    }
    return null;
}
const xy = (p) => (Array.isArray(p) ? [p[1], p[0]] : toXY(p));
function close(pts) {
    const r = pts.map(xy);
    r.push(r[0]);
    return r;
}
function translate(geom, dlat, dlng) {
    const mv = (c) => (typeof c[0] === "number" ? [c[0] + dlng, c[1] + dlat] : c.map(mv));
    return { type: geom.type, coordinates: mv(geom.coordinates) };
}

export class TravauxCarte extends Component {
    static template = "service_travaux_carte.Carte";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.mapRef = useRef("map");
        this.ETATS = ETATS;
        this.GLABEL = GLABEL;
        this.state = useState({
            data: null, panel: null, hidden: {}, zonesOn: true, fond: "auto", fondEff: "picc", error: false,
            tool: null, draft: [], pending: null, edit: null, confirm: false, typeEdit: null, geoTarget: null,
            moving: false, busy: false, zoom: 18,
        });
        this.onKey = this.onKey.bind(this);
        onWillStart(async () => {
            try {
                await loadLeaflet();
            } catch {
                this.state.error = true;
            }
            await this.load();
        });
        onMounted(() => {
            this.initMap();
            document.addEventListener("keydown", this.onKey);
        });
        onWillUnmount(() => {
            this.dead = true;
            document.removeEventListener("keydown", this.onKey);
            if (this.map) this.map.remove();
        });
    }

    get L() {
        return window.L;
    }
    get data() {
        return this.state.data;
    }
    /** # affichés dans la colonne : ceux qui ont des objets ou ont été configurés pour la carte. */
    get typesCarte() {
        return (this.data && this.data.types.filter((t) => t.carte)) || [];
    }
    typesOf(g) {
        return this.typesCarte.filter((t) => t.g === g);
    }
    get typesAutres() {
        return (this.data && this.data.types.filter((t) => !t.carte)) || [];
    }
    get propositions() {
        return ((this.data && this.data.objets) || []).filter((o) => o.prop);
    }
    get nbFait() {
        return ((this.data && this.data.zones) || []).filter((z) => z.etat === "fait").length;
    }
    get nbTodo() {
        return ((this.data && this.data.zones) || []).filter((z) => z.etat === "todo").length;
    }
    get isDrawing() {
        return DRAW_TOOLS.includes(this.state.tool) && !this.state.pending;
    }
    get draftInfo() {
        const d = this.state.draft, t = this.state.tool;
        let s = `${d.length} sommet${d.length > 1 ? "s" : ""}`;
        if (t === "ligne" && d.length > 1) s += ` · ${fmt(lengthM(d))} m`;
        if ((t === "surface" || t === "lieu") && d.length > 2) s += ` · ${fmt(areaM2(d))} m²`;
        return s;
    }
    get draftMin() {
        return this.state.tool === "ligne" ? 2 : 3;
    }
    get editInfo() {
        const e = this.state.edit;
        if (!e) return "";
        return `${e.pts.length} sommets · ` + (e.g === "ligne" ? `${fmt(lengthM(e.pts))} m` : `${fmt(areaM2(e.pts))} m²`);
    }
    get pendingInfo() {
        const p = this.state.pending;
        if (!p) return "";
        if (p.g === "ligne") return `${fmt(lengthM(p.pts))} m`;
        if (p.g === "surface" || p.g === "lieu") return `${fmt(areaM2(p.pts))} m²`;
        return "";
    }
    get pendingTypes() {
        const p = this.state.pending;
        return p ? this.data.types.filter((t) => t.g === p.g).sort((a, b) => (b.carte - a.carte) || a.name.localeCompare(b.name)) : [];
    }
    get selectedTypeEdit() {
        const te = this.state.typeEdit;
        if (!te) return null;
        return te.id ? this.data.types.find((t) => t.id === te.id) : null;
    }

    async load() {
        const d = await this.orm.call("travaux.carte", "carte_data", []);
        // valeurs par défaut : écran tolérant si le serveur n'a pas encore été mis à jour
        this.state.data = { objets: [], types: [], zones: [], lieux_points: [], sans_geo: [], nb_sans_geo: 0, circuits: [], ...d };
    }

    // ------------------------------------------------------------ carte
    initMap() {
        if (this.state.error || !this.mapRef.el) return;
        const L = this.L;
        const d = this.data;
        this.map = L.map(this.mapRef.el, { maxZoom: 21, zoomControl: true, doubleClickZoom: false }).setView(d.centre || [50.7124, 4.5213], 18);
        this.fonds = {
            osm: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 21, maxNativeZoom: 19, attribution: "© OpenStreetMap" }),
            picc: arcgisLayer(L, `${WALLONIE}/TOPOGRAPHIE/PICC_VDIFF/MapServer`, null, { attribution: "PICC © SPW" }),
            ortho: arcgisLayer(L, `${WALLONIE}/IMAGERIE/ORTHO_LAST/MapServer`, null, { attribution: "Orthophoto © SPW" }),
        };
        this.zoneLayer = L.layerGroup().addTo(this.map);
        this.objLayer = L.layerGroup().addTo(this.map);
        this.selLayer = L.layerGroup().addTo(this.map);
        this.drawLayer = L.layerGroup().addTo(this.map);
        this.editLayer = L.layerGroup().addTo(this.map);
        this.map.on("click", (ev) => this.onMapClick(ev.latlng));
        this.map.on("dblclick", () => {
            if (this.isDrawing) this.finishDraw();
        });
        this.map.on("zoomend", () => this.updateFond());
        this.updateFond();
        this.draw();
        const p = (this.props.action && this.props.action.params) || {};
        if (p.objet_id) this.openObjet(p.objet_id, true);
        else if (p.lieu_id) this.openZone(p.lieu_id, true);
        else this.fitAll();
    }

    updateFond() {
        if (!this.map) return;
        const z = this.map.getZoom();
        this.state.zoom = z;
        const eff = this.state.fond === "ortho" ? "ortho" : z >= PICC_ZOOM ? "picc" : "osm";
        if (eff === this._fondEff) return;
        if (this._fondEff) this.fonds[this._fondEff].remove();
        this.fonds[eff].addTo(this.map);
        this.fonds[eff].bringToBack();
        this._fondEff = eff;
        this.state.fondEff = eff;
        this.mapRef.el.classList.toggle("o_tvc_bg_picc", eff === "picc");
    }
    setFond(f) {
        this.state.fond = f;
        this.updateFond();
    }

    fitAll() {
        const pts = this.data.objets.map((o) => [o.lat, o.lng]);
        for (const z of this.data.zones) {
            const rings = z.geojson.type === "Polygon" ? [z.geojson.coordinates[0]] : z.geojson.coordinates.map((p) => p[0]);
            rings.forEach((r) => r.forEach((c) => pts.push(toLL(c))));
        }
        if (pts.length) this.map.fitBounds(pts, { padding: [30, 30], maxZoom: 18 });
    }

    draw() {
        if (!this.map) return;
        const L = this.L;
        const st = this.state;
        const editId = st.edit && `${st.edit.kind}:${st.edit.id}`;
        this.zoneLayer.clearLayers();
        this.objLayer.clearLayers();
        if (st.zonesOn) {
            for (const z of this.data.zones) {
                if (editId === `zone:${z.id}`) continue;
                const c = ETATS[z.etat];
                L.geoJSON(z.geojson, {
                    style: { color: c, weight: 2, fillColor: c, fillOpacity: z.etat === "lieu" ? 0.08 : 0.22, dashArray: z.etat === "fait" ? null : "6 4" },
                    onEachFeature: (f, layer) => {
                        layer.bindTooltip(`#${z.name}`);
                        layer.on("click", (ev) => {
                            L.DomEvent.stopPropagation(ev);
                            this.featureClick(ev, "zone", z.id);
                        });
                    },
                }).addTo(this.zoneLayer);
            }
            for (const p of this.data.lieux_points) {
                if (editId === `zone:${p.id}`) continue;
                L.marker([p.lat, p.lng], { icon: L.divIcon({ className: "o_tvc_lieupt", iconSize: [14, 14] }) })
                    .bindTooltip(`#${p.name}`)
                    .on("click", (ev) => {
                        L.DomEvent.stopPropagation(ev);
                        this.featureClick(ev, "zone", p.id);
                    }).addTo(this.zoneLayer);
            }
        }
        const order = { surface: 0, ligne: 1, point: 2 };
        const objets = [...this.data.objets].sort((a, b) => order[a.g] - order[b.g]);
        for (const o of objets) {
            if (st.hidden[o.type_id] || editId === `objet:${o.id}`) continue;
            let layer;
            if (o.g !== "point" && o.geo) {
                const weight = o.g === "ligne" ? 6 : 1.8;
                layer = L.geoJSON(o.geo, {
                    style: {
                        color: o.open ? "#C2410C" : o.c, weight: o.open ? Math.max(weight, 3) : weight, fillColor: o.c,
                        fillOpacity: o.prop ? 0.12 : 0.35, dashArray: o.prop ? (o.g === "ligne" ? "8 6" : "5 4") : null, lineCap: "round",
                    },
                });
            } else {
                layer = L.circleMarker([o.lat, o.lng], {
                    radius: o.type === "Poteau" ? 5 : 7, color: o.open ? "#C2410C" : o.prop ? o.c : "#ffffff",
                    weight: o.open ? 3 : o.prop ? 2.5 : 1.5, fillColor: o.prop ? "#ffffff" : o.c, fillOpacity: 1,
                    dashArray: o.prop ? "3 2" : null,
                });
            }
            layer.bindTooltip(`#${o.type} ${o.ref}` + (o.prop ? " · proposé" : "") + (o.open ? ` · ${o.open} ouverte${o.open > 1 ? "s" : ""}` : ""));
            layer.on("click", (ev) => {
                L.DomEvent.stopPropagation(ev);
                this.featureClick(ev, "objet", o.id);
            });
            layer.addTo(this.objLayer);
        }
    }

    // ------------------------------------------------------------ sélection et panneau
    /** En cours de dessin, un clic sur un objet existant pose un sommet ; sinon il le sélectionne. */
    featureClick(ev, kind, id) {
        const st = this.state;
        if (this.isDrawing || (st.tool === "point" && !st.pending)) this.onMapClick(ev.latlng);
        else this.onFeatureClick(kind, id);
    }
    onFeatureClick(kind, id) {
        const st = this.state;
        if (this.isDrawing || st.pending || st.edit) return;
        st.confirm = st.tool === "supprimer";
        if (kind === "objet") this.openObjet(id).then(() => this.afterSelect());
        else this.openZone(id).then(() => this.afterSelect());
    }
    afterSelect() {
        const st = this.state, p = st.panel;
        if (!p) return;
        if (st.tool === "forme") this.startForme();
        else if (st.tool === "deplacer") this.startMove();
    }
    async openObjet(id, zoom = false) {
        const f = await this.orm.call("travaux.carte", "carte_objet", [id]);
        if (this.dead) return;
        this.state.typeEdit = null;
        this.state.panel = f;
        this.selLayer.clearLayers();
        if (!f) return;
        const L = this.L;
        if (f.geo) {
            const g = L.geoJSON(f.geo, { style: { color: "#1A1A1A", weight: f.g === "ligne" ? 10 : 3, opacity: f.g === "ligne" ? 0.35 : 1, fill: false }, interactive: false }).addTo(this.selLayer);
            if (zoom) this.map.fitBounds(g.getBounds(), { maxZoom: 19, padding: [60, 60] });
        } else {
            L.circleMarker([f.lat, f.lng], { radius: 13, color: "#1A1A1A", weight: 2.5, fill: false, interactive: false }).addTo(this.selLayer);
            if (zoom) this.map.setView([f.lat, f.lng], 19);
        }
    }
    async openZone(id, zoom = false) {
        const z = await this.orm.call("travaux.carte", "carte_zone", [id]);
        if (this.dead) return;
        this.state.typeEdit = null;
        this.state.panel = z;
        this.selLayer.clearLayers();
        if (!z) return;
        const L = this.L;
        if (z.geojson) {
            const g = L.geoJSON(z.geojson, { style: { color: "#1A1A1A", weight: 3, fill: false }, interactive: false }).addTo(this.selLayer);
            if (zoom) this.map.fitBounds(g.getBounds(), { maxZoom: 19, padding: [60, 60] });
        } else if (z.point) {
            L.circleMarker(z.point, { radius: 12, color: "#1A1A1A", weight: 2.5, fill: false, interactive: false }).addTo(this.selLayer);
            if (zoom) this.map.setView(z.point, 19);
        }
    }
    closePanel() {
        this.state.panel = null;
        this.state.confirm = false;
        this.selLayer.clearLayers();
    }
    async refresh(reopen = true) {
        const p = this.state.panel;
        await this.load();
        this.draw();
        if (reopen && p) {
            if (p.kind === "objet") await this.openObjet(p.id);
            else if (p.kind === "zone") await this.openZone(p.id);
        }
    }

    // ------------------------------------------------------------ couches
    toggleType(id) {
        this.state.hidden[id] = !this.state.hidden[id];
        this.draw();
    }
    toggleZones() {
        this.state.zonesOn = !this.state.zonesOn;
        this.draw();
    }

    // ------------------------------------------------------------ outils
    setTool(t) {
        const st = this.state;
        const prev = st.tool;
        this.cancelEdit(true);
        st.tool = prev === t ? null : t;
        st.draft = [];
        st.pending = null;
        st.confirm = false;
        st.typeEdit = null;
        if (!st.tool) st.geoTarget = null;
        this.drawLayer.clearLayers();
        if (DRAW_TOOLS.includes(st.tool)) {
            if (!st.geoTarget) this.closePanel();
            const msg = {
                point: "Touchez l'emplacement du point.",
                ligne: "Cliquez les sommets de la ligne, double-cliquez pour terminer.",
                surface: "Cliquez les sommets de la surface, double-cliquez pour la fermer.",
                lieu: "Dessinez le contour du # lieu, double-cliquez pour le fermer.",
            }[st.tool];
            this.notification.add(msg, { type: "info" });
        } else if (st.tool && st.panel) {
            if (st.tool === "supprimer") st.confirm = true;
            else this.afterSelect();
        } else if (st.tool) {
            this.notification.add({ forme: "Touchez la ligne, la surface ou le # lieu à modifier.", deplacer: "Touchez l'objet à déplacer.",
                supprimer: "Touchez l'objet ou le # lieu à supprimer." }[st.tool], { type: "info" });
        }
    }
    resetTool() {
        const st = this.state;
        st.tool = null;
        st.draft = [];
        st.pending = null;
        st.geoTarget = null;
        st.moving = false;
        this.drawLayer.clearLayers();
        this.editLayer.clearLayers();
    }
    onKey(ev) {
        if (/INPUT|SELECT|TEXTAREA/.test((ev.target && ev.target.tagName) || "")) return;
        const st = this.state;
        if (ev.key === "Escape") {
            if (st.edit) this.cancelEdit(true);
            else if (st.tool || st.pending) this.resetTool();
            else this.closePanel();
        } else if (ev.key === "Enter" && this.isDrawing) {
            this.finishDraw();
        } else if ((ev.key === "Delete" || ev.key === "Backspace") && st.panel && !st.edit && !st.pending && this.canDelete) {
            st.confirm = true;
        }
    }

    onMapClick(latlng) {
        const st = this.state;
        if (st.edit || st.moving) return;
        if (st.tool === "point" && !st.pending) {
            if (st.geoTarget) {
                this.saveLieuGeo({ lat: latlng.lat, lng: latlng.lng });
                return;
            }
            st.pending = { g: "point", pts: [latlng], type: (this.typesOf("point")[0] || {}).id };
            this.drawDraft();
            return;
        }
        if (this.isDrawing) {
            const last = st.draft[st.draft.length - 1];
            if (last && this.map.latLngToContainerPoint(last).distanceTo(this.map.latLngToContainerPoint(latlng)) < 4) return;
            st.draft.push(latlng);
            this.drawDraft();
            return;
        }
        if (!st.tool && !st.pending && !st.typeEdit) this.closePanel();
    }
    drawDraft() {
        const L = this.L, st = this.state;
        this.drawLayer.clearLayers();
        const pts = st.pending ? st.pending.pts : st.draft;
        const g = st.pending ? st.pending.g : st.tool;
        if (g === "point" && pts.length) {
            L.circleMarker(pts[0], { radius: 9, color: "#276577", weight: 3, fillColor: "#fff", fillOpacity: 1, interactive: false }).addTo(this.drawLayer);
            return;
        }
        if (pts.length > 1) {
            const opts = { color: "#276577", weight: 3, dashArray: "6 4", interactive: false };
            (g === "ligne" ? L.polyline(pts, opts) : L.polygon(pts, { ...opts, fillOpacity: 0.15 })).addTo(this.drawLayer);
        }
        for (const p of pts) L.circleMarker(p, { radius: 4, color: "#276577", fillColor: "#276577", fillOpacity: 1, interactive: false }).addTo(this.drawLayer);
    }
    undoDraft() {
        this.state.draft.pop();
        this.drawDraft();
    }
    finishDraw() {
        const st = this.state;
        if (st.draft.length < this.draftMin) {
            this.notification.add(`Il faut au moins ${this.draftMin} sommets.`, { type: "warning" });
            return;
        }
        const pts = st.draft.slice();
        if (st.tool === "lieu" && st.geoTarget) {
            this.saveLieuGeo({ geojson: { type: "Polygon", coordinates: [close(pts)] } });
            return;
        }
        const g = st.tool;
        st.pending = { g, pts, type: g === "lieu" ? null : (this.typesOf(g)[0] || this.pendingTypesFor(g)[0] || {}).id,
            name: "", circuit: "" };
        st.draft = [];
        this.drawDraft();
    }
    pendingTypesFor(g) {
        return this.data.types.filter((t) => t.g === g);
    }
    cancelPending() {
        this.state.pending = null;
        this.drawLayer.clearLayers();
    }
    async savePending() {
        const st = this.state, p = st.pending;
        if (!p || st.busy) return;
        st.busy = true;
        try {
            let msg;
            if (p.g === "lieu") {
                if (!p.name) {
                    this.notification.add("Donnez un nom au # lieu.", { type: "warning" });
                    return;
                }
                const id = await this.orm.call("travaux.carte", "carte_lieu_creer", [p.name], {
                    geojson: { type: "Polygon", coordinates: [close(p.pts)] }, circuit_id: p.circuit ? parseInt(p.circuit) : false });
                this.resetTool();
                await this.refresh(false);
                await this.openZone(id);
                msg = "# lieu créé.";
            } else {
                if (!p.type) {
                    this.notification.add("Choisissez le # de l'objet, ou créez-en un.", { type: "warning" });
                    return;
                }
                const kw = p.g === "point" ? { lat: p.pts[0].lat, lng: p.pts[0].lng }
                    : { geojson: p.g === "ligne" ? { type: "LineString", coordinates: p.pts.map(xy) } : { type: "Polygon", coordinates: [close(p.pts)] } };
                const id = await this.orm.call("travaux.carte", "carte_ajouter", [p.type], kw);
                const g = p.g;
                this.resetTool();
                if (["ligne", "surface", "point"].includes(g)) st.tool = null;
                await this.refresh(false);
                await this.openObjet(id);
                msg = this.data.is_sup ? "Objet enregistré." : "Objet proposé : un superviseur doit le figer.";
            }
            this.notification.add(msg, { type: "success" });
        } finally {
            st.busy = false;
        }
    }

    // ------------------------------------------------------------ modifier la forme
    startForme() {
        const st = this.state, p = st.panel, L = this.L;
        if (!p) return;
        const geom = p.kind === "objet" ? p.geo : p.geojson;
        if (!geom) {
            if (p.kind === "objet") this.notification.add("Un point se déplace avec l'outil Déplacer.", { type: "info" });
            return;
        }
        if ((p.kind === "objet" && !p.can_edit) || (p.kind === "zone" && !this.data.is_sup)) {
            this.notification.add("Seul un superviseur peut modifier cet élément.", { type: "warning" });
            return;
        }
        const e = editable(geom);
        if (!e) return;
        st.edit = { kind: p.kind, id: p.id, g: e.g, pts: e.pts.map((x) => L.latLng(x)), rebuild: e.rebuild };
        st.tool = "forme";
        this.selLayer.clearLayers();
        this.draw();
        this.drawEdit();
    }
    drawEdit() {
        const L = this.L, e = this.state.edit;
        this.editLayer.clearLayers();
        if (!e) return;
        const style = { color: "#1A1A1A", weight: e.g === "ligne" ? 5 : 2.5, fillColor: "#276577", fillOpacity: 0.18, interactive: false };
        this._editShape = (e.g === "ligne" ? L.polyline(e.pts, style) : L.polygon(e.pts, style)).addTo(this.editLayer);
        const closed = e.g !== "ligne";
        const n = e.pts.length;
        for (let i = 0; i < n - (closed ? 0 : 1); i++) {
            const a = e.pts[i], b = e.pts[(i + 1) % n];
            const mid = L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2);
            const m = L.marker(mid, { draggable: true, icon: L.divIcon({ className: "o_tvc_mid", iconSize: [12, 12] }), title: "Glisser pour ajouter un sommet" });
            m.on("dragstart", () => {
                e.pts.splice(i + 1, 0, m.getLatLng());
                this._dragIdx = i + 1;
            });
            m.on("drag", () => {
                e.pts[this._dragIdx] = m.getLatLng();
                this._editShape.setLatLngs(e.pts);
            });
            m.on("dragend", () => this.drawEdit());
            m.addTo(this.editLayer);
        }
        e.pts.forEach((p, i) => {
            const v = L.marker(p, { draggable: true, icon: L.divIcon({ className: "o_tvc_vtx", iconSize: [14, 14] }), title: "Double-clic : retirer ce sommet" });
            v.on("drag", () => {
                e.pts[i] = v.getLatLng();
                this._editShape.setLatLngs(e.pts);
            });
            v.on("dragend", () => this.drawEdit());
            v.on("dblclick", (ev) => {
                L.DomEvent.stopPropagation(ev);
                const min = e.g === "ligne" ? 2 : 3;
                if (e.pts.length > min) {
                    e.pts.splice(i, 1);
                    this.drawEdit();
                } else {
                    this.notification.add(`Il faut au moins ${min} sommets.`, { type: "warning" });
                }
            });
            v.addTo(this.editLayer);
        });
    }
    cancelEdit(redraw = false) {
        const had = !!this.state.edit || this.state.moving;
        this.state.edit = null;
        this.state.moving = false;
        if (this.editLayer) this.editLayer.clearLayers();
        if (had && this.state.tool !== null && (this.state.tool === "forme" || this.state.tool === "deplacer")) this.state.tool = null;
        if (had && redraw) {
            this.draw();
            this.refreshSel();
        }
    }
    refreshSel() {
        const p = this.state.panel;
        if (p && p.kind === "objet") this.openObjet(p.id);
        else if (p && p.kind === "zone") this.openZone(p.id);
    }
    async saveEdit() {
        const st = this.state, e = st.edit;
        if (!e || st.busy) return;
        st.busy = true;
        try {
            const geojson = e.rebuild(e.pts);
            if (e.kind === "objet") await this.orm.call("travaux.carte", "carte_forme", [e.id, geojson]);
            else await this.orm.call("travaux.carte", "carte_lieu_geometrie", [e.id], { geojson });
            st.edit = null;
            st.tool = null;
            this.editLayer.clearLayers();
            await this.refresh();
            this.notification.add("Forme enregistrée.", { type: "success" });
        } finally {
            st.busy = false;
        }
    }

    // ------------------------------------------------------------ déplacer
    startMove() {
        const st = this.state, p = st.panel, L = this.L;
        if (!p) return;
        if ((p.kind === "objet" && !p.can_edit) || (p.kind === "zone" && !this.data.is_sup)) {
            this.notification.add("Seul un superviseur peut déplacer cet élément.", { type: "warning" });
            return;
        }
        let start, geom;
        if (p.kind === "objet") {
            start = L.latLng(p.lat, p.lng);
            geom = p.geo;
        } else if (p.geojson) {
            geom = p.geojson;
            start = L.geoJSON(geom).getBounds().getCenter();
        } else if (p.point) {
            start = L.latLng(p.point);
        } else {
            return;
        }
        st.tool = "deplacer";
        st.moving = true;
        this.editLayer.clearLayers();
        this.selLayer.clearLayers();
        const ghost = geom ? L.geoJSON(geom, { style: { color: "#1A1A1A", weight: 2.5, dashArray: "5 4", fillOpacity: 0.12 }, interactive: false }).addTo(this.editLayer) : null;
        const handle = L.marker(start, { draggable: true, icon: L.divIcon({ className: "o_tvc_move", iconSize: [30, 30], html: '<i class="fa fa-arrows"/>' }) }).addTo(this.editLayer);
        handle.on("drag", () => {
            if (!ghost) return;
            const ll = handle.getLatLng();
            ghost.clearLayers();
            ghost.addData(translate(geom, ll.lat - start.lat, ll.lng - start.lng));
        });
        handle.on("dragend", () => this.saveMove(p, start, handle.getLatLng(), geom));
        this.notification.add("Faites glisser la poignée à la nouvelle place.", { type: "info" });
    }
    async saveMove(p, start, end, geom) {
        const st = this.state;
        st.busy = true;
        try {
            if (p.kind === "objet") {
                await this.orm.call("travaux.carte", "carte_deplacer", [p.id, p.lat + end.lat - start.lat, p.lng + end.lng - start.lng]);
            } else if (geom) {
                await this.orm.call("travaux.carte", "carte_lieu_geometrie", [p.id], { geojson: translate(geom, end.lat - start.lat, end.lng - start.lng) });
            } else {
                await this.orm.call("travaux.carte", "carte_lieu_geometrie", [p.id], { lat: end.lat, lng: end.lng });
            }
            st.moving = false;
            st.tool = null;
            this.editLayer.clearLayers();
            await this.refresh();
            this.notification.add("Position enregistrée.", { type: "success" });
        } finally {
            st.busy = false;
        }
    }

    // ------------------------------------------------------------ supprimer, figer, refuser
    get canDelete() {
        const p = this.state.panel;
        if (!p) return false;
        return p.kind === "objet" ? p.can_edit : this.data.is_sup;
    }
    askDelete() {
        this.state.confirm = true;
    }
    cancelDelete() {
        this.state.confirm = false;
        if (this.state.tool === "supprimer") this.state.tool = null;
    }
    async doDelete() {
        const st = this.state, p = st.panel;
        if (!p || st.busy) return;
        st.busy = true;
        try {
            const res = p.kind === "objet"
                ? await this.orm.call("travaux.carte", "carte_supprimer", [p.id])
                : await this.orm.call("travaux.carte", "carte_lieu_supprimer", [p.id]);
            st.confirm = false;
            st.tool = null;
            this.closePanel();
            await this.refresh(false);
            this.notification.add(res === "archive" ? "Retiré de la carte : archivé, son historique est conservé." : "Supprimé.", { type: "success" });
        } finally {
            st.busy = false;
        }
    }
    async figer() {
        await this.orm.call("travaux.carte", "carte_figer", [this.state.panel.id]);
        await this.refresh();
        this.notification.add("Objet figé.", { type: "success" });
    }
    async refuser() {
        await this.orm.call("travaux.carte", "carte_refuser", [this.state.panel.id]);
        this.closePanel();
        await this.refresh(false);
        this.notification.add("Proposition refusée.", { type: "success" });
    }
    openProp(o) {
        this.resetTool();
        this.openObjet(o.id, true);
    }

    // ------------------------------------------------------------ # lieux sans géométrie
    openSansGeo(l) {
        this.resetTool();
        this.openZone(l.id);
    }
    drawLieuGeo(mode) {
        const st = this.state, p = st.panel;
        this.resetTool();
        st.geoTarget = p.id;
        st.tool = mode === "point" ? "point" : "lieu";
        this.notification.add(mode === "point" ? `Touchez l'emplacement de #${p.name}.` : `Dessinez le contour de #${p.name}, double-cliquez pour le fermer.`, { type: "info" });
    }
    async saveLieuGeo(kw) {
        const st = this.state, id = st.geoTarget;
        st.busy = true;
        try {
            await this.orm.call("travaux.carte", "carte_lieu_geometrie", [id], kw);
            this.resetTool();
            await this.refresh(false);
            await this.openZone(id);
            this.notification.add("Géométrie enregistrée.", { type: "success" });
        } finally {
            st.busy = false;
        }
    }

    // ------------------------------------------------------------ éditeur de #
    openTypeEdit(t, g) {
        this.cancelEdit();
        const st = this.state;
        st.typeEdit = t
            ? { id: t.id, name: t.name, g: t.g, c: t.c, pre: t.pre, proposable: t.proposable, n: t.n, fromPending: false }
            : { id: false, name: "", g: g || "surface", c: "#0F766E", pre: "", proposable: true, n: 0, fromPending: !!st.pending };
    }
    setTypeGeom(g) {
        this.state.typeEdit.g = g;
    }
    closeTypeEdit() {
        this.state.typeEdit = null;
    }
    async saveType() {
        const st = this.state, te = st.typeEdit;
        if (!te || st.busy) return;
        if (!te.name) {
            this.notification.add("Donnez un nom au #.", { type: "warning" });
            return;
        }
        st.busy = true;
        try {
            const vals = { name: te.name, geom_type: te.g, couleur: te.c, prefixe: te.pre || false, proposable: te.proposable };
            const t = await this.orm.call("travaux.carte", "carte_type", [vals], { type_id: te.id || false });
            st.typeEdit = null;
            await this.load();
            this.draw();
            if (te.fromPending && st.pending && t.g === st.pending.g) st.pending.type = t.id;
            this.notification.add(te.id ? `#${t.name} enregistré.` : `#${t.name} créé.`, { type: "success" });
        } finally {
            st.busy = false;
        }
    }
    async deleteType() {
        const te = this.state.typeEdit;
        await this.orm.call("travaux.carte", "carte_type_supprimer", [te.id]);
        this.state.typeEdit = null;
        await this.load();
        this.draw();
        this.notification.add("# supprimé.", { type: "success" });
    }
    onPendingType(ev) {
        const v = ev.target.value;
        if (v === "new") {
            this.openTypeEdit(null, this.state.pending.g);
            ev.target.value = this.state.pending.type || "";
            return;
        }
        this.state.pending.type = parseInt(v);
    }

    // ------------------------------------------------------------ fiches Odoo
    nouvelleDemande() {
        const p = this.state.panel;
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "travaux.demande", views: [[false, "form"]], target: "new",
            name: `Nouvelle demande · #${p.type} ${p.ref}`,
            context: { default_objet_recense_id: p.id, default_titre: (p.choix && p.choix[0]) || "" },
        }, { onClose: () => this.refresh() });
    }
    modifier() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "travaux.objet", res_id: this.state.panel.id,
            views: [[false, "form"]], target: "new", name: "Objet recensé",
        }, { onClose: () => this.refresh() });
    }
    ouvrirDemande(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "travaux.demande", res_id: id, views: [[false, "form"]] });
    }
    ouvrirLieu() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "travaux.lieu", res_id: this.state.panel.id, views: [[false, "form"]] });
    }
    importer() {
        this.action.doAction("service_travaux_carte.action_import_geo", { onClose: () => this.refresh() });
    }
}

registry.category("actions").add("travaux_carte", TravauxCarte);
