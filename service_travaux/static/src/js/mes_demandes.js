/** @odoo-module **/
import { Component, useState, useRef, onWillStart, onPatched } from "@odoo/owl";
import { TvTimeline } from "./components";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useTravauxLive, chatTime, hhmm, imgUrl, readImage, ST_STYLE } from "./common";

/** « Mes demandes » : l'écran du demandeur (autre service), sur le modèle de « Demandes à traiter ». */
export class TravauxMesDemandes extends Component {
    static template = "service_travaux.MesDemandes";
    static components = { TvTimeline };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.ui = useState(useService("ui"));
        this.chatTime = chatTime;
        this.hhmm = hhmm;
        this.imgUrl = imgUrl;
        this.ST_STYLE = ST_STYLE;
        this.chatRef = useRef("chat");
        this.fileRef = useRef("file");
        this.msgFileRef = useRef("msgfile");
        this.titreRef = useRef("titre");
        this.state = useState({
            data: { items: [], lieux: [], categories: [] }, cur: null, selId: null,
            mode: this.ui.isSmall ? "list" : "view", form: this.emptyForm(), draft: "", busy: false,
        });
        onWillStart(() => this.load());
        onPatched(() => {
            if (this.chatRef.el) this.chatRef.el.scrollTop = this.chatRef.el.scrollHeight;
        });
        useTravauxLive(() => this.load(), ["demande", "poll"]);
    }
    emptyForm() {
        return { titre: "", description: "", lieu_id: "", adresse: "", categorie_id: "", photo: false, contact: "", service: "" };
    }
    async load() {
        const data = await this.orm.call("travaux.demande", "tv_dem_data", [this.state.selId || false]);
        this.state.data = data;
        this.state.cur = data.current;
        if (!this.state.form.contact) this.state.form.contact = data.me;
        if (!this.state.form.service) this.state.form.service = data.service;
        if (!this.state.selId && !this.ui.isSmall && data.items.length) {
            await this.select(data.items[0].id);
        }
    }
    async select(id) {
        this.state.selId = id;
        this.state.mode = "view";
        const data = await this.orm.call("travaux.demande", "tv_dem_data", [id]);
        this.state.data = data;
        this.state.cur = data.current;
    }
    newRequest() {
        if (!this.ui.isSmall) {
            // sur ordinateur, le formulaire est toujours à droite
            if (this.titreRef.el) this.titreRef.el.focus();
            return;
        }
        this.state.mode = "new";
        this.state.selId = null;
        this.state.cur = null;
    }
    back() {
        this.state.mode = "list";
        this.state.selId = null;
        this.state.cur = null;
    }
    get layoutClass() {
        if (!this.ui.isSmall) return "";
        return this.state.mode === "list" ? "o_tv_m_list" : "o_tv_m_detail";
    }
    get showForm() {
        return this.state.mode === "new" || !this.ui.isSmall;
    }
    isSel(name, id) {
        return String(this.state.form[name]) === String(id);
    }
    setField(name, ev) {
        this.state.form[name] = ev.target.value;
    }
    pickPhoto() {
        this.fileRef.el.click();
    }
    async onPhoto(ev) {
        const f = ev.target.files[0];
        if (f) this.state.form.photo = await readImage(f);
        ev.target.value = "";
    }
    get photoPreview() {
        return this.state.form.photo ? "data:image/jpeg;base64," + this.state.form.photo : false;
    }
    async send() {
        const f = this.state.form;
        if (!f.titre.trim() || !f.description.trim()) {
            this.notification.add("Indiquez le problème et une description.", { type: "warning" });
            return;
        }
        if (!f.lieu_id && !f.adresse.trim()) {
            this.notification.add("Indiquez où se trouve le problème : un bâtiment ou une adresse.", { type: "warning" });
            return;
        }
        this.state.busy = true;
        try {
            const id = await this.orm.call("travaux.demande", "tv_dem_create", [f]);
            const keep = { contact: f.contact, service: f.service };
            this.state.form = { ...this.emptyForm(), ...keep };
            this.notification.add("Demande envoyée au service technique.", { type: "success" });
            await this.select(id);
        } finally {
            this.state.busy = false;
        }
    }
    async post(ev) {
        if (ev) ev.preventDefault();
        const text = this.state.draft.trim();
        if (!text || this.state.busy) return;
        this.state.busy = true;
        try {
            const data = await this.orm.call("travaux.demande", "tv_dem_post", [this.state.selId, text]);
            this.state.draft = "";
            this.state.data = data;
            this.state.cur = data.current;
        } finally {
            this.state.busy = false;
        }
    }
    pickMsgPhoto() {
        this.msgFileRef.el.click();
    }
    async onMsgPhoto(ev) {
        const f = ev.target.files[0];
        ev.target.value = "";
        if (!f) return;
        const b64 = await readImage(f);
        const data = await this.orm.call("travaux.demande", "tv_dem_post", [this.state.selId, this.state.draft.trim(), b64]);
        this.state.draft = "";
        this.state.data = data;
        this.state.cur = data.current;
    }
}
TravauxMesDemandes.template = "service_travaux.MesDemandes";
registry.category("actions").add("travaux_mes_demandes", TravauxMesDemandes);
