"""Démonstration autour de la maison communale de Rixensart (avenue de Mérode).

Les avaloirs, chambres de visite et poteaux viennent du PICC (SPW) : en direct si le serveur
a Internet, sinon depuis la copie livrée avec le module. Les zones d'espaces verts, les poubelles
et les quelques demandes sont des exemples.
"""

import json
import logging

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import file_open

from . import geo

_logger = logging.getLogger(__name__)

MAISON_COMMUNALE = (50.71240, 4.52135, "Avenue de Mérode 75")
PICC_BBOX = (4.518, 50.7115, 4.524, 50.715)

TYPES = [  # nom, préfixe, couleur, géométrie
    ("Avaloir", "AV", "#1F5FA8", "point"), ("ChambreDeVisite", "CV", "#6A5CC4", "point"),
    ("Poteau", "PO", "#6B7780", "point"), ("Poubelle", "PB", "#C2410C", "point"),
    ("Pelouse", "PE", "#4E9A3A", "surface"), ("Massif", "MA", "#B5527E", "surface"),
    ("Haie", "HA", "#2F6B2A", "ligne"), ("ArbreRemarquable", "AR", "#7A5C1E", "point"),
]
# objets surfaces / lignes dessinés dans les zones : type, zone, réduction vers le centre (surfaces)
SURFACES = [("Pelouse", "Parc de la maison communale", 0.62), ("Pelouse", "Pelouse arrière", 0.8),
            ("Pelouse", "Square Roosevelt", 0.7), ("Massif", "Massif de l'entrée, avenue de Mérode", 0.75)]
HAIES = [  # haie côté parking du commissariat, haie du square
    "4.520514 50.712732;4.520335 50.712776;4.520202 50.712794;4.520128 50.712898",
    "4.519973 50.71199;4.519909 50.712083;4.51997 50.712199",
]
ARBRES = [(50.712420, 4.520700, "Hêtre pourpre"), (50.712560, 4.520930, "Tilleul argenté")]
LIEUX_SANS_GEOMETRIE = ["Cimetière communal", "Cour de l'école communale", "Parking de la gare"]


def _line(text):
    return {"type": "LineString", "coordinates": [[float(v) for v in p.split()] for p in text.split(";")]}


def _shrink(geojson, factor):
    g = json.loads(geojson)
    c = geo.centroid(g)
    return geo.map_coords(g, lambda x, y: (c[0] + (x - c[0]) * factor, c[1] + (y - c[1]) * factor))


def _ring(text):
    pts = [[float(v) for v in p.split()] for p in text.split(";")]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


# zones d'espaces verts (contours repris des espaces plantés du PICC) : nom, à savoir, passée cette semaine
ZONES = [
    ("Parc de la maison communale", "", True,
     "4.520541 50.712231;4.520382 50.712303;4.520382 50.71236;4.520474 50.712456;4.520697 50.712502;4.520852 50.712639;"
     "4.520894 50.712691;4.521003 50.712676;4.521032 50.712597;4.520995 50.712499;4.521058 50.712397;4.521025 50.712298;"
     "4.52105 50.712209;4.520999 50.71215;4.520879 50.712167;4.520839 50.712268;4.52078 50.712268"),
    ("Abords du commissariat", "Haie côté parking", False,
     "4.520514 50.712732;4.520335 50.712776;4.520202 50.712794;4.520128 50.712898;4.520098 50.713019;4.520269 50.71309;"
     "4.520353 50.713106;4.520454 50.713246;4.520618 50.713397;4.520762 50.713413;4.520878 50.713325;4.520733 50.713212;"
     "4.520896 50.713144;4.520905 50.713063;4.520731 50.713049;4.520677 50.71296;4.520664 50.712776"),
    ("Massif de l'entrée, avenue de Mérode", "", True,
     "4.521481 50.711859;4.521427 50.71188;4.521433 50.711999;4.521526 50.712042;4.521668 50.712026;4.5217 50.711968;"
     "4.521689 50.711888;4.521655 50.711848"),
    ("Pelouse arrière", "", False,
     "4.521 50.713044;4.521049 50.713074;4.521185 50.713052;4.521228 50.712963;4.521149 50.712901;4.521005 50.712918;"
     "4.520973 50.713002"),
    ("Square Roosevelt", "Attention aux massifs de vivaces", False,
     "4.520217 50.712034;4.519973 50.71199;4.519909 50.712083;4.51997 50.712199;4.520078 50.712233;4.520223 50.712189;"
     "4.520296 50.712053"),
]
POUBELLES = [
    (50.71162, 4.52092, "Avenue de Mérode"), (50.71170, 4.52140, "Avenue de Mérode"), (50.71172, 4.52195, "Avenue de Mérode"),
    (50.71174, 4.52262, "Avenue de Mérode"), (50.71181, 4.52320, "Avenue de Mérode"), (50.71161, 4.51960, "Avenue de Mérode"),
    (50.71160, 4.51880, "Avenue de Mérode"), (50.71180, 4.52060, "Avenue Franklin Roosevelt"),
    (50.71233, 4.52123, "Avenue de Mérode"), (50.71279, 4.52128, "Avenue de Mérode"), (50.71347, 4.52098, "Avenue de Mérode"),
    (50.71400, 4.52085, "Avenue Roger de Grimberghe"), (50.71436, 4.52140, "Avenue Roger de Grimberghe"),
    (50.71500, 4.51965, "Rue Albert Croy"), (50.71480, 4.51875, "Rue Albert Croy"), (50.71319, 4.51938, "Avenue Roger de Grimberghe"),
]
DEMANDES = [  # type, n° d'objet, titre, statut
    ("Avaloir", 23, "Avaloir bouché", "planif"), ("Avaloir", 24, "Avaloir bouché", "recue"),
    ("Poubelle", 3, "Corbeille débordante", "recue"), ("Poubelle", 9, "Corbeille cassée", "cours"),
    ("ChambreDeVisite", 2, "Taque descellée", "close"), ("Poteau", 11, "Poteau penché", "recue"),
]


# # objets créés à l'installation (s'ils n'existent pas) : nom, préfixe, couleur, géométrie
TYPES_DE_BASE = TYPES + [
    ("Lampadaire", "LP", "#B7791F", "point"), ("Banc", "BC", "#0F766E", "point"),
    ("Parterre", "PA", "#C26D9B", "surface"), ("AireDeJeux", "AJ", "#2563EB", "surface"),
]


class TravauxCarteDemo(models.AbstractModel):
    _name = "travaux.carte.demo"
    _description = "Démonstration carte FACILITY"

    @api.model
    def _types_de_base(self):
        """Appelé à l'installation et à chaque mise à jour du module : quelques # objets prêts pour la carte."""
        Lieu = self.env["travaux.lieu"].sudo()
        for name, pre, col, geom in TYPES_DE_BASE:
            if not Lieu._by_name(name):  # jamais recréé ni réactivé s'il a été supprimé ou archivé
                Lieu.create({"name": name, "kind": "objet", "state": "valide", "geom_type": geom,
                             "prefixe": pre, "couleur": col})
        return True

    @api.model
    def _type(self, name, prefixe, couleur, geom="point"):
        Lieu = self.env["travaux.lieu"].sudo()
        t = Lieu._by_name(name)
        if not t:
            t = Lieu.create({"name": name, "kind": "objet", "state": "valide", "geom_type": geom})
        vals = {"prefixe": t.prefixe or prefixe, "couleur": t.couleur or couleur, "active": True}
        if t.geom_type != geom and not t.nb_objets:
            vals["geom_type"] = geom
        t.write(vals)
        return t

    @api.model
    def _picc_points(self):
        Wizard = self.env["travaux.import.picc"]
        try:
            pts = Wizard._points(Wizard._fetch(PICC_BBOX))
            pts = [p for p in pts if p["nature"] in ("Avaloir - Petit", "Chambre de visite", "Poteau")]
            if pts:
                return pts, "PICC en direct"
        except UserError as err:
            _logger.info("FACILITY carte : PICC injoignable (%s), copie livrée utilisée", err)
        with file_open("service_travaux_carte/data/picc_maison_communale.json") as f:
            return json.load(f)["points"], "copie du PICC livrée avec le module"

    @api.model
    def action_charger(self):
        if not self.env.user.has_group("service_travaux.group_superviseur"):
            raise AccessError(_("La démonstration se prépare avec un compte superviseur."))
        env = self.env
        Lieu = env["travaux.lieu"].sudo()
        Objet = env["travaux.objet"].sudo()
        # 1. maison communale à sa vraie place
        lat, lng, adr = MAISON_COMMUNALE
        mc = Lieu._by_name("MaisonCommunale")
        vals = {"latitude": lat, "longitude": lng, "adresse": adr, "state": "valide", "active": True}
        mc = mc.write(vals) and mc if mc else Lieu.create(dict(vals, name="MaisonCommunale", kind="lieu"))
        # 2. types d'objets
        types = {name: self._type(name, pre, col, g) for name, pre, col, g in TYPES}
        # 3. zones d'espaces verts (contours) et leur tournée
        Circuit = env["travaux.circuit"].sudo()
        Equipe = env["travaux.equipe"].sudo()
        teams = {t.name: t for t in Equipe.search([])}
        ev = teams.get("Espaces verts 2") or teams.get("Espaces verts 1") or Equipe.create({"name": "Espaces verts 2"})
        prop = teams.get("Propreté publique") or Equipe.create({"name": "Propreté publique"})
        tonte = Circuit.search([("name", "=", "Tonte · Maison communale")], limit=1)
        zone_lieux = []
        for name, note, done, ring in ZONES:
            lieu = Lieu._by_name(name)
            geojson = json.dumps(_ring(ring))
            if not lieu:
                lieu = Lieu.create({"name": name, "kind": "lieu", "state": "valide", "adresse": adr})
            lieu.write({"geojson": geojson, "latitude": 0, "longitude": 0})
            c = geo.centroid(geo.loads(geojson))
            lieu.write({"latitude": c[1], "longitude": c[0]})
            zone_lieux.append((name, note, done, lieu))
        if not tonte:
            tonte = Circuit.create({"name": "Tonte · Maison communale", "equipe_id": ev.id, "mode": "une", "tour_no": 1})
            for i, (name, note, _done, lieu) in enumerate(zone_lieux):
                env["travaux.circuit.zone"].sudo().create({"circuit_id": tonte.id, "sequence": 10 * (i + 1), "name": name,
                                                           "lieu_id": lieu.id, "note": note, "duree": 1.5})
        # 3 bis. pelouses, massifs (surfaces), haies (lignes), arbres remarquables, # lieux encore sans géométrie
        lieux_par_nom = {name: lieu for name, _n, _d, lieu in zone_lieux}
        if not Objet.search_count([("type_id", "in", [types["Pelouse"].id, types["Massif"].id])]):
            for tname, zname, factor in SURFACES:
                Objet.create({"type_id": types[tname].id, "geojson": json.dumps(_shrink(lieux_par_nom[zname].geojson, factor)),
                              "rue": adr, "source": "bureau", "state": "valide"})
            # une proposition d'agent : petit massif dans le parc
            parc = lieux_par_nom["Parc de la maison communale"]
            Objet.create({"type_id": types["Massif"].id, "geojson": json.dumps(_shrink(parc.geojson, 0.18)),
                          "source": "terrain", "state": "propose", "propose_par_id": env.ref("base.user_admin").id,
                          "note": "Nouveau massif planté au printemps"})
        if not Objet.search_count([("type_id", "=", types["Haie"].id)]):
            Objet.create([{"type_id": types["Haie"].id, "geojson": json.dumps(_line(h)), "source": "bureau",
                           "state": "valide"} for h in HAIES])
        if not Objet.search_count([("type_id", "=", types["ArbreRemarquable"].id)]):
            Objet.create([{"type_id": types["ArbreRemarquable"].id, "latitude": la, "longitude": lo, "note": note,
                           "source": "odwb", "state": "valide"} for la, lo, note in ARBRES])
        for name in LIEUX_SANS_GEOMETRIE:
            if not Lieu.with_context(active_test=False)._by_name(name):
                Lieu.create({"name": name, "kind": "lieu", "state": "valide"})
        # 4. poubelles et leur tournée en lot
        pb = Circuit.search([("name", "=", "Poubelles · Maison communale")], limit=1)
        if not pb:
            pb = Circuit.create({"name": "Poubelles · Maison communale", "equipe_id": prop.id, "mode": "lot", "tour_no": 1})
            for i, rue in enumerate(["Avenue de Mérode", "Avenue Roger de Grimberghe", "Rue Albert Croy"]):
                env["travaux.circuit.zone"].sudo().create({"circuit_id": pb.id, "sequence": 10 * (i + 1), "name": rue})
        if not Objet.search_count([("circuit_id", "=", pb.id)]):
            Objet.create([{"type_id": types["Poubelle"].id, "latitude": la, "longitude": lo, "rue": rue,
                           "circuit_id": pb.id, "source": "terrain", "state": "valide"} for la, lo, rue in POUBELLES])
            last = Objet.search([("circuit_id", "=", pb.id)], order="numero desc", limit=1)
            last.write({"state": "propose", "propose_par_id": env.ref("base.user_admin").id})
        # 5. avaloirs, chambres de visite, poteaux du PICC
        pts, origine = self._picc_points()
        wiz = env["travaux.import.picc"].create({"mode": "rect", "min_lng": PICC_BBOX[0], "min_lat": PICC_BBOX[1],
                                                 "max_lng": PICC_BBOX[2], "max_lat": PICC_BBOX[3]})
        wiz._set_points(pts)
        for line in wiz.line_ids:
            name = {"Avaloir - Petit": "Avaloir", "Chambre de visite": "ChambreDeVisite", "Poteau": "Poteau"}.get(line.nature)
            line.write({"importer": bool(name), "type_id": types[name].id if name else False})
        res = wiz._import_points(pts)
        # rue la plus probable : l'adresse du # lieu ou celle de la poubelle la plus proche
        for o in Objet.search([("rue", "=", False)]):
            near = min(POUBELLES, key=lambda p: (p[0] - o.latitude) ** 2 + (p[1] - o.longitude) ** 2)
            o.rue = near[2]
        # 6. quelques demandes sur des objets précis
        Demande = env["travaux.demande"].sudo().with_context(tracking_disable=True, travaux_no_live=True)
        if not Demande.search_count([("objet_recense_id", "!=", False)]):
            for tname, num, titre, state in DEMANDES:
                o = Objet.search([("type_id", "=", types[tname].id), ("numero", "=", num)], limit=1)
                if o:
                    d = Demande.create({"titre": titre, "objet_recense_id": o.id, "source": "app", "state": "recue",
                                        "type_travail": "demande"})
                    if state != "recue":
                        d.write({"state": state})
        # 7. tournées lancées (visibles dans l'app terrain), deux zones déjà faites cette semaine
        for circuit in (tonte, pb):
            if not circuit.lancement_en_cours:
                Circuit.with_user(env.user).tv_lancer(circuit.id)
        bt = tonte._bt_cours_tournee()
        if bt:
            for name, _note, done, _lieu in zone_lieux:
                zone = tonte.zone_ids.filtered(lambda z, n=name: z.name == n)[:1]
                if done and zone and not bt._tz_passages().filtered(lambda p, z=zone: p.zone_id == z):
                    bt._tz_noter(zone, 1.25)
        msg = _("Démonstration prête : %(n)s objets du PICC (%(o)s), %(p)s poubelles, %(z)s zones d'espaces verts, "
                "2 tournées lancées.", n=res["created"] + res["updated"], o=origine, p=len(POUBELLES), z=len(ZONES))
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": _("Carte"), "message": msg, "type": "success", "sticky": False,
                           "next": {"type": "ir.actions.client", "tag": "travaux_carte", "name": _("Carte")}}}
