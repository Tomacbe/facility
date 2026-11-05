/** @odoo-module **/
import { Component, useState } from "@odoo/owl";
import { hhmm, dt } from "./common";

const JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"];

/** Chronologie d'une demande : Reçue → Attribuée → Démarrée → Terminée → Clôturée → Demandeur informé */
export class TvTimeline extends Component {
    setup() {
        this.state = useState({ open: !this.props.compact });
    }
    toggle() {
        if (this.props.compact) this.state.open = !this.state.open;
    }
    when(value) {
        const t = dt(value);
        if (!t) return "";
        return `${t.toFormat("dd/MM")} ${hhmm(value)}`;
    }
    get currentLabel() {
        const cur = (this.props.steps || []).find((s) => s.current);
        return cur ? `Prochaine étape : ${cur.label.toLowerCase()}` : "Terminé";
    }
}
TvTimeline.template = "service_travaux.TvTimeline";
TvTimeline.props = { steps: Array, compact: { type: Boolean, optional: true } };

/** Bandeau d'aide : explique l'écran en une phrase et 3 étapes. Masquable, réouvrable par « ? ». */
export class TvHelp extends Component {
    setup() {
        let hidden = false;
        try {
            hidden = window.localStorage.getItem("facility_help_" + this.props.id) === "1";
        } catch {
            hidden = false;
        }
        this.state = useState({ hidden });
    }
    hide() {
        this.state.hidden = true;
        try {
            window.localStorage.setItem("facility_help_" + this.props.id, "1");
        } catch {
            // stockage indisponible : on masque seulement pour cette session
        }
    }
    show() {
        this.state.hidden = false;
        try {
            window.localStorage.removeItem("facility_help_" + this.props.id);
        } catch {
            // rien
        }
    }
}
TvHelp.template = "service_travaux.TvHelp";
TvHelp.props = { id: String, text: String, steps: { type: Array, optional: true } };
