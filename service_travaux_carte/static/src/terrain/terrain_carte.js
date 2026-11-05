/** @odoo-module **/
import { onPatched, onWillUnmount } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { TravauxTerrain } from "@service_travaux/js/terrain";
import { loadLeaflet } from "@service_travaux/js/common";

const Z_COL = { todo: "#D07A1F", cours: "#6A5CC4", fait: "#2E8B57", inutile: "#8D9BA1" };

/** App terrain : carte de la tournée (zones en surfaces, objets du circuit), ouverte par un bouton. */
patch(TravauxTerrain.prototype, {
    setup() {
        super.setup(...arguments);
        Object.assign(this.state, { tzMap: false, lotMap: false, tvcSel: null, tvcObj: null, tvcProp: null, tvcTypes: [] });
        onPatched(() => this.tvcRender());
        onWillUnmount(() => this.tvcDestroy());
    },

    get tzZones() {
        return this.state.tzMap ? [] : super.tzZones;
    },
    get tvcZones() {
        return (this.state.conv && this.state.conv.tz) || [];
    },
    get tvcSelZone() {
        return this.tvcZones.find((z) => z.id === this.state.tvcSel) || null;
    },
    get tvcCarte() {
        return (this.state.conv && this.state.conv.carte) || null;
    },
    get tvcHasCircuitMap() {
        const c = this.tvcCarte;
        return !!(c && c.mode === "lot" && c.objets.length);
    },

    get tvcPropType() {
        const p = this.state.tvcProp;
        return p && this.state.tvcTypes.find((t) => t.id === p.type);
    },
    get tvcPropMin() {
        const t = this.tvcPropType;
        return t && t.g === "ligne" ? 2 : 3;
    },

    // ------------------------------------------------------------ proposer un objet (l'agent propose, le superviseur fige)
    async tvcPropOpen() {
        if (!this.state.tvcTypes.length) {
            this.state.tvcTypes = await this.orm.call("travaux.carte", "terrain_types", []);
        }
        this.state.tvcSel = null;
        this.state.tvcObj = null;
        const last = this._tvcLastType && this.state.tvcTypes.find((t) => t.id === this._tvcLastType);
        this.state.tvcProp = { step: "sheet", type: (last || this.state.tvcTypes[0] || {}).id, pts: [], last: null };
    },
    tvcPropSetType(ev) {
        this.state.tvcProp.type = parseInt(ev.target.value);
    },
    tvcPropClose() {
        this.state.tvcProp = null;
        this._tvcSig = null;
        this.tvcDrawDraft();
    },
    tvcPropDraw() {
        const p = this.state.tvcProp;
        if (!p.type) return;
        this._tvcLastType = p.type;
        p.step = "draw";
        p.pts = [];
    },
    tvcPropUndo() {
        this.state.tvcProp.pts.pop();
        this.tvcDrawDraft();
    },
    tvcPropTap(latlng) {
        const p = this.state.tvcProp, t = this.tvcPropType;
        if (!p || p.step !== "draw" || !t) return;
        p.pts.push([latlng.lat, latlng.lng]);
        if (t.g === "point") this.tvcPropSave();
        else this.tvcDrawDraft();
    },
    tvcPropHere() {
        if (!this.state.pos) return;
        this.state.tvcProp.pts = [[this.state.pos.lat, this.state.pos.lng]];
        this.tvcPropSave();
    },
    tvcDrawDraft() {
        const L = window.L;
        if (!this._tvcMap || !L) return;
        if (!this._tvcDraft) this._tvcDraft = L.layerGroup().addTo(this._tvcMap);
        this._tvcDraft.clearLayers();
        const p = this.state.tvcProp, t = this.tvcPropType;
        if (!p || p.step !== "draw" || !t) return;
        const opts = { color: "#276577", weight: 3, dashArray: "7 4", interactive: false };
        if (p.pts.length > 1) (t.g === "ligne" ? L.polyline(p.pts, opts) : L.polygon(p.pts, { ...opts, fillOpacity: 0.18 })).addTo(this._tvcDraft);
        for (const x of p.pts) L.circleMarker(x, { radius: 6, color: "#fff", weight: 2, fillColor: "#276577", fillOpacity: 1, interactive: false }).addTo(this._tvcDraft);
    },
    async tvcPropSave() {
        const p = this.state.tvcProp, t = this.tvcPropType;
        if (!p || !t || this.state.busy) return;
        const xy = (x) => [x[1], x[0]];
        let kw;
        if (t.g === "point") kw = { lat: p.pts[0][0], lng: p.pts[0][1] };
        else if (t.g === "ligne") kw = { geojson: { type: "LineString", coordinates: p.pts.map(xy) } };
        else kw = { geojson: { type: "Polygon", coordinates: [[...p.pts.map(xy), xy(p.pts[0])]] } };
        this.state.busy = true;
        try {
            const id = await this.orm.call("travaux.carte", "carte_ajouter", [t.id], kw);
            const pt = await this.orm.call("travaux.carte", "carte_point", [id]);
            const carte = this.tvcCarte;
            if (carte && pt) carte.autour = [...(carte.autour || []), pt];
            this.state.tvcProp = { step: "done", type: t.id, pts: [], last: pt };
            this.tvcDrawDraft();
            this.notification.add(pt && pt.prop ? `#${t.name} proposé : un superviseur doit le figer.` : `#${t.name} enregistré.`, { type: "success" });
        } finally {
            this.state.busy = false;
        }
    },
    async tvcPropRetirer() {
        const p = this.state.tvcProp;
        if (!p || !p.last) return;
        await this.orm.call("travaux.carte", "carte_supprimer", [p.last.id]);
        const carte = this.tvcCarte;
        if (carte) carte.autour = (carte.autour || []).filter((o) => o.id !== p.last.id);
        this.state.tvcProp = null;
        this.notification.add("Proposition retirée.", { type: "info" });
    },

    tvcToggleTz() {
        this.state.tzMap = !this.state.tzMap;
        this.state.tvcSel = null;
        this.state.tvcObj = null;
        this.state.tvcProp = null;
        if (!this.state.tzMap) this.tvcDestroy();
    },
    tvcToggleLot() {
        this.state.lotMap = !this.state.lotMap;
        this.state.tvcObj = null;
        this.state.tvcProp = null;
        if (!this.state.lotMap) this.tvcDestroy();
    },
    tvcDestroy() {
        if (this._tvcMap) {
            this._tvcMap.remove();
            this._tvcMap = null;
            this._tvcEl = null;
            this._tvcDraft = null;
        }
    },

    async tvcRender() {
        const el = document.querySelector(".o_tvc_tmap");
        if (!el) {
            if (this._tvcMap && !document.body.contains(this._tvcEl)) this.tvcDestroy();
            return;
        }
        const sig = JSON.stringify([this.state.conv && this.state.conv.id, this.tvcZones.map((z) => [z.id, z.status]),
            this.tvcCarte && this.tvcCarte.objets.map((o) => [o.id, o.open]),
            this.tvcCarte && (this.tvcCarte.autour || []).map((o) => o.id), this.state.pos]);
        if (this._tvcMap && this._tvcEl === el && this._tvcSig === sig) return;
        if (this._tvcLoading) return;
        this._tvcLoading = true;
        try {
            await loadLeaflet();
        } catch {
            el.innerHTML = '<div class="o_tv_empty">Carte indisponible : pas d\'accès au fond de plan depuis ce téléphone.</div>';
            return;
        } finally {
            this._tvcLoading = false;
        }
        const L = window.L;
        const first = !this._tvcMap || this._tvcEl !== el;
        if (first) {
            this.tvcDestroy();
            this._tvcMap = L.map(el, { zoomControl: false, attributionControl: false });
            this._tvcEl = el;
            L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 21, maxNativeZoom: 19 }).addTo(this._tvcMap);
            this._tvcGroup = L.featureGroup().addTo(this._tvcMap);
            this._tvcMap.on("click", (ev) => {
                if (this.state.tvcProp && this.state.tvcProp.step === "draw") {
                    this.tvcPropTap(ev.latlng);
                    return;
                }
                this.state.tvcSel = null;
                this.state.tvcObj = null;
            });
        }
        this._tvcSig = sig;
        const g = this._tvcGroup;
        g.clearLayers();
        const focus = [];
        const carte = this.tvcCarte || { objets: [], autour: [] };
        const order = { surface: 0, ligne: 1, point: 2 };
        for (const o of [...(carte.autour || [])].sort((a, b) => (order[a.g] || 2) - (order[b.g] || 2))) {
            this._tvcPoint(L, g, o, o.mine ? 8 : o.g === "ligne" ? 5 : 4);
        }
        if (this.state.tzMap) {
            for (const z of this.tvcZones) {
                const c = Z_COL[z.status] || "#D07A1F";
                const onClick = (ev) => {
                    L.DomEvent.stopPropagation(ev);
                    if (this.state.tvcProp && this.state.tvcProp.step === "draw") {
                        this.tvcPropTap(ev.latlng);
                        return;
                    }
                    this.state.tvcProp = null;
                    this.state.tvcObj = null;
                    this.state.tvcSel = z.id;
                };
                if (z.geojson) {
                    const layer = L.geoJSON(JSON.parse(z.geojson), { style: { color: c, weight: 2.5, fillColor: c, fillOpacity: 0.35 } })
                        .on("click", onClick).bindTooltip(z.name).addTo(g);
                    focus.push(layer.getBounds());
                } else if (z.lat) {
                    L.circleMarker([z.lat, z.lng], { radius: 10, color: "#fff", weight: 2, fillColor: c, fillOpacity: 1 })
                        .on("click", onClick).bindTooltip(z.name).addTo(g);
                    focus.push(L.latLngBounds([[z.lat, z.lng], [z.lat, z.lng]]));
                }
            }
        }
        for (const o of carte.objets || []) {
            this._tvcPoint(L, g, o, 7);
            focus.push(L.latLngBounds([[o.lat, o.lng], [o.lat, o.lng]]));
        }
        if (this.state.pos) {
            L.circleMarker([this.state.pos.lat, this.state.pos.lng], { radius: 7, color: "#fff", weight: 3, fillColor: "#2B7BE4", fillOpacity: 1, interactive: false }).addTo(g);
        }
        if (first) {
            const b = focus.length ? focus.reduce((acc, x) => acc.extend(x), L.latLngBounds(focus[0].getSouthWest(), focus[0].getNorthEast())) : g.getBounds();
            if (b.isValid()) this._tvcMap.fitBounds(b, { padding: [28, 28], maxZoom: 19 });
            else this._tvcMap.setView([50.7124, 4.5213], 17);
            setTimeout(() => this._tvcMap && this._tvcMap.invalidateSize(), 150);
        }
    },

    _tvcPoint(L, g, o, r) {
        let layer;
        if (o.g && o.g !== "point" && o.geo) {
            layer = L.geoJSON(o.geo, { style: {
                color: o.open ? "#C2410C" : o.c, weight: o.g === "ligne" ? Math.max(r - 1, 4) : 1.5, fillColor: o.c,
                fillOpacity: o.prop ? 0.12 : 0.3, dashArray: o.prop ? "6 4" : null, lineCap: "round" } });
        } else {
            layer = L.circleMarker([o.lat, o.lng], {
                radius: r, color: o.open ? "#C2410C" : o.prop ? o.c : "#fff", weight: o.open ? 3 : o.prop ? 2.5 : 1.5,
                fillColor: o.prop ? "#fff" : o.c, fillOpacity: 1, dashArray: o.prop ? "3 2" : null,
            });
        }
        layer.on("click", async (ev) => {
            L.DomEvent.stopPropagation(ev);
            if (this.state.tvcProp && this.state.tvcProp.step === "draw") {
                this.tvcPropTap(ev.latlng);
                return;
            }
            this.state.tvcSel = null;
            this.state.tvcProp = null;
            this.state.tvcObj = await this.orm.call("travaux.demande", "tv_objet_infos", [o.id]);
        }).bindTooltip(`#${o.type} ${o.ref}` + (o.prop ? " · proposé" : "")).addTo(g);
    },

    async tvcSignaler(titre) {
        const o = this.state.tvcObj;
        if (!o || this.state.busy) return;
        this.state.busy = true;
        try {
            await this.orm.call("travaux.demande", "tv_objet_signaler", [o.id, titre]);
            this.state.tvcObj = { ...o, sent: titre };
            await this.loadList();
            this.notification.add(`Signalé : ${titre} (${o.ref}). La demande part au superviseur avec l'objet exact.`, { type: "success" });
        } finally {
            this.state.busy = false;
        }
    },
});
