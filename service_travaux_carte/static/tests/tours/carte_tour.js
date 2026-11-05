/** @odoo-module **/
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("travaux_carte_tour", {
    steps: () => [
        { trigger: ".o_tvc_page .o_tvc_map.leaflet-container" },
        { trigger: ".o_tvc_side .o_tvc_row:contains(#Avaloir) .o_tvc_n:contains(61)" },
        { trigger: ".o_tvc_side .o_tvc_row:contains(#Pelouse) .o_tvc_n:contains(3)" },
        { trigger: ".o_tvc_side .o_tvc_proprow:contains(#Massif)" },
        { trigger: ".o_tvc_toolbar button:contains(Surface)" },
        // la proposition d'agent : fiche avec Figer / Refuser
        { trigger: ".o_tvc_side .o_tvc_proprow:contains(#Massif)", run: "click" },
        { trigger: ".o_tvc_panel .o_tvc_box_prop button:contains(Figer)" },
        { trigger: ".o_tvc_panel button:contains(Modifier la forme)", run: "click" },
        { trigger: ".o_tvc_drawbar:contains(Modifier la forme)" },
        { trigger: ".o_tvc_map .o_tvc_vtx" },
        { trigger: ".o_tvc_drawbar button:contains(Annuler)", run: "click" },
        // éditeur de #
        { trigger: ".o_tvc_side .o_tvc_addtype", run: "click" },
        { trigger: ".o_tvc_panel h2:contains(Nouveau type)" },
        { trigger: ".o_tvc_panel .o_tvc_geoseg button:contains(Ligne)", run: "click" },
        { trigger: ".o_tvc_panel .o_tvc_geoseg button[aria-pressed=true]:contains(Ligne)" },
        { trigger: ".o_tvc_panel button:contains(Annuler)", run: "click" },
        // un # lieu sans géométrie
        { trigger: ".o_tvc_side .o_tvc_nogeo_row:contains(Parking)", run: "click" },
        { trigger: ".o_tvc_panel button:contains(Placer un point)" },
    ],
});
