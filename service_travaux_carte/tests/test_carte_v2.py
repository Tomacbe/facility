import base64
import json
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import file_open

from ..models import geo
from ..wizards import import_geo
from .test_carte import _offline

PARC = {"type": "Polygon", "coordinates": [[[4.5205, 50.7122], [4.5210, 50.7122], [4.5210, 50.7126],
                                            [4.5205, 50.7126], [4.5205, 50.7122]]]}
HAIE = {"type": "LineString", "coordinates": [[4.5204, 50.71225], [4.5204, 50.7125]]}


def _resp(payload=None, content=None):
    r = MagicMock()
    r.json.return_value = payload
    r.content = content or b""
    r.raise_for_status.return_value = None
    return r


@tagged("post_install", "-at_install")
class TestCarteV2(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, travaux_no_live=True))
        cls.sup = new_test_user(cls.env, "sup_carte2", groups="service_travaux.group_superviseur,service_travaux.group_agent")
        cls.agent = new_test_user(cls.env, "agent_carte2", groups="service_travaux.group_agent")
        with _offline():
            cls.env["travaux.carte.demo"].with_user(cls.sup).action_charger()
        cls.Lieu = cls.env["travaux.lieu"]
        cls.Carte = cls.env["travaux.carte"]
        cls.pelouse = cls.Lieu._by_name("Pelouse")
        cls.haie = cls.Lieu._by_name("Haie")

    # ------------------------------------------------------------ géométrie
    def test_geo_lambert(self):
        lng, lat = geo.to_wgs84(160000, 150000, 31370)
        self.assertAlmostEqual(lng, 4.510183, places=5)
        self.assertAlmostEqual(lat, 50.660530, places=5)
        lng, lat = geo.to_wgs84(660000, 650000, 3812)
        self.assertAlmostEqual(lng, 4.510155, places=5)
        self.assertEqual(geo.guess_epsg(160000, 150000), 31370)
        self.assertEqual(geo.guess_epsg(660000, 650000), 3812)
        self.assertAlmostEqual(geo.length_m(HAIE), 27.8, delta=0.5)

    def test_demo_formes(self):
        Objet = self.env["travaux.objet"]
        self.assertEqual(self.pelouse.geom_type, "surface")
        pelouses = Objet.search([("type_id", "=", self.pelouse.id)])
        self.assertEqual(len(pelouses), 3)
        self.assertTrue(all(p.mesure > 50 and p.mesure_txt.endswith("m²") for p in pelouses))
        self.assertTrue(all(p.lieu_id for p in pelouses), "chaque pelouse est rattachée à sa zone")
        haies = Objet.search([("type_id", "=", self.haie.id)])
        self.assertEqual(len(haies), 2)
        self.assertTrue(haies[0].mesure_txt.endswith(" m"))
        data = self.Carte.with_user(self.sup).carte_data()
        self.assertEqual(data["nb_sans_geo"], 3)
        pe = next(t for t in data["types"] if t["name"] == "Pelouse")
        self.assertEqual((pe["g"], pe["n"]), ("surface", 3))
        self.assertTrue(any(o["prop"] and o["g"] == "surface" for o in data["objets"]))
        self.assertTrue(all(o["geo"] for o in data["objets"] if o["g"] != "point"))

    def test_ajouter_formes(self):
        oid = self.Carte.with_user(self.sup).carte_ajouter(self.pelouse.id, geojson=PARC)
        o = self.env["travaux.objet"].browse(oid)
        self.assertEqual(o.state, "valide")
        self.assertAlmostEqual(o.mesure, geo.area_m2(PARC), delta=1)
        self.assertAlmostEqual(o.latitude, 50.7124, places=4)
        # mauvaise géométrie pour le #
        with self.assertRaises(UserError):
            self.Carte.with_user(self.sup).carte_ajouter(self.pelouse.id, geojson=HAIE)
        with self.assertRaises(UserError):
            self.Carte.with_user(self.sup).carte_ajouter(self.pelouse.id, 50.71, 4.52)
        # modifier la forme puis déplacer toute la surface
        new = json.loads(json.dumps(PARC))
        new["coordinates"][0][2] = [4.5212, 50.7127]
        self.Carte.with_user(self.sup).carte_forme(oid, new)
        self.assertTrue(o.geom_modifie)
        self.assertGreater(o.mesure, geo.area_m2(PARC))
        lat0 = o.latitude
        self.Carte.with_user(self.sup).carte_deplacer(oid, lat0 + 0.0001, o.longitude)
        self.assertAlmostEqual(json.loads(o.geojson)["coordinates"][0][0][1], 50.7123, places=5)

    def test_agent_propose(self):
        Carte = self.Carte.with_user(self.agent)
        oid = Carte.carte_ajouter(self.haie.id, geojson=HAIE)
        o = self.env["travaux.objet"].browse(oid)
        self.assertEqual((o.state, o.source, o.propose_par_id), ("propose", "terrain", self.agent))
        self.assertIn(o.type_id.name, [t["name"] for t in Carte.terrain_types()])
        Carte.carte_forme(oid, {"type": "LineString", "coordinates": [[4.5204, 50.71225], [4.5206, 50.7125]]})
        with self.assertRaises(AccessError):
            Carte.carte_figer(oid)
        fige = self.env["travaux.objet"].search([("state", "=", "valide"), ("type_id", "=", self.haie.id)], limit=1)
        with self.assertRaises(AccessError):
            Carte.carte_forme(fige.id, HAIE)
        with self.assertRaises(AccessError):
            Carte.carte_supprimer(fige.id)
        # non proposable
        self.haie.proposable = False
        with self.assertRaises(AccessError):
            Carte.carte_ajouter(self.haie.id, geojson=HAIE)
        # l'agent retire sa propre proposition
        self.assertEqual(Carte.carte_supprimer(oid), "supprime")
        self.assertFalse(o.exists())

    def test_supprimer_refuser(self):
        Objet = self.env["travaux.objet"]
        Carte = self.Carte.with_user(self.sup)
        avec = Objet.search([("demande_ids", "!=", False)], limit=1)
        self.assertEqual(Carte.carte_supprimer(avec.id), "archive")
        self.assertFalse(avec.active)
        prop = Objet.search([("state", "=", "propose"), ("type_id.name", "=", "Massif")], limit=1)
        self.assertEqual(Carte.carte_refuser(prop.id), "supprime")
        with self.assertRaises(AccessError):
            self.Carte.with_user(self.agent).carte_refuser(Objet.search([("state", "=", "propose")], limit=1).id)

    def test_types(self):
        Carte = self.Carte.with_user(self.sup)
        t = Carte.carte_type({"name": "#Bordure fleurie", "geom_type": "surface", "couleur": "#0F766E", "proposable": True})
        self.assertEqual((t["name"], t["g"], t["pre"]), ("BordureFleurie", "surface", "BO"))
        Carte.carte_type({"geom_type": "ligne"}, t["id"])  # pas encore d'objets : on peut changer
        Carte.carte_ajouter(t["id"], geojson=HAIE)
        with self.assertRaises(UserError):
            Carte.carte_type({"geom_type": "surface"}, t["id"])
        with self.assertRaises(UserError):
            Carte.carte_type_supprimer(t["id"])
        vide = Carte.carte_type({"name": "Clôture", "geom_type": "ligne"})
        self.assertEqual(Carte.carte_type_supprimer(vide["id"]), "supprime")
        with self.assertRaises(AccessError):
            self.Carte.with_user(self.agent).carte_type({"name": "X"})

    def test_lieux(self):
        Carte = self.Carte.with_user(self.sup)
        cim = self.Lieu._by_name("Cimetière communal")
        self.assertFalse(cim.has_contour or cim.latitude)
        # un objet sans # lieu dans la zone dessinée lui est rattaché
        far = {"type": "Polygon", "coordinates": [[[4.530, 50.720], [4.531, 50.720], [4.531, 50.721],
                                                    [4.530, 50.721], [4.530, 50.720]]]}
        oid = Carte.carte_ajouter(self.pelouse.id, geojson=geo.map_coords(far, lambda x, y: (x + 0.0002, y + 0.0002)))
        o = self.env["travaux.objet"].browse(oid)
        self.assertFalse(o.lieu_id)
        Carte.carte_lieu_geometrie(cim.id, geojson=geo.map_coords(far, lambda x, y: (x - 0.0005 + (x - 4.530) * 2, y - 0.0005 + (y - 50.720) * 2)))
        self.assertTrue(cim.has_contour)
        self.assertEqual(o.lieu_id, cim)
        ecole = self.Lieu._by_name("Cour de l'école communale")
        Carte.carte_lieu_geometrie(ecole.id, lat=50.715, lng=4.525)
        self.assertEqual((round(ecole.latitude, 3), ecole.has_contour), (50.715, False))
        # nouveau # lieu dans une tournée
        tonte = self.env["travaux.circuit"].search([("name", "=", "Tonte · Maison communale")])
        nid = Carte.carte_lieu_creer("Pelouse du parking", geojson=PARC, circuit_id=tonte.id)
        self.assertIn(nid, tonte.zone_ids.lieu_id.ids)
        with self.assertRaises(UserError):
            Carte.carte_lieu_creer("Pelouse du parking", geojson=PARC)
        # supprimer : archivé car dans une tournée ; un lieu libre est supprimé
        self.assertEqual(Carte.carte_lieu_supprimer(nid), "archive")
        libre = self.Lieu._by_name("Parking de la gare")
        self.assertEqual(Carte.carte_lieu_supprimer(libre.id), "supprime")
        with self.assertRaises(AccessError):
            self.Carte.with_user(self.agent).carte_lieu_creer("X", geojson=PARC)

    # ------------------------------------------------------------ import
    def _wizard(self, **vals):
        return self.env["travaux.import.geo"].with_user(self.sup).create(dict({"zone": "tout"}, **vals))

    def test_import_shapefile(self):
        with file_open("service_travaux_carte/tests/data/espaces_verts_shp.zip", "rb") as f:
            data = f.read()
        wiz = self._wizard(source="fichier", fichier=base64.b64encode(data), fichier_nom="espaces_verts_shp.zip")
        wiz.action_lire()
        self.assertEqual(sorted(wiz.couche_ids.mapped("name")), ["espaces_verts", "haies"])
        self.assertEqual(wiz.couche_ids.filtered(lambda c: c.name == "haies").geom, "ligne")
        wiz.action_charger()
        self.assertEqual(wiz.champ_id.name, "type")
        self.assertEqual(wiz.nom_champ_id.name, "nom")
        classes = {c.valeur: c for c in wiz.classe_ids}
        self.assertEqual(classes["pelouse"].type_id, self.pelouse)
        self.assertEqual(classes["haie"].type_id, self.haie)
        self.assertEqual(classes["massif"].geom, "surface")
        wiz.action_importer()
        self.assertIn("3 objet(s) créé(s)", wiz.result)
        o = self.env["travaux.objet"].search([("note", "=", "Pelouse du parc")])
        self.assertEqual((o.type_id, o.source), (self.pelouse, "fichier"))
        # Lambert 72 converti : la pelouse est bien autour de 4.5207 / 50.71237
        self.assertAlmostEqual(o.longitude, 4.5207, places=4)
        self.assertAlmostEqual(o.latitude, 50.712375, places=4)
        self.assertAlmostEqual(o.mesure, 237, delta=10)
        # second import : mise à jour, pas de doublon ; une forme corrigée n'est pas écrasée
        o.geom_modifie = True
        geom_before = o.geojson
        wiz2 = self._wizard(source="fichier", fichier=base64.b64encode(data), fichier_nom="espaces_verts_shp.zip")
        wiz2.action_lire()
        wiz2.action_charger()
        wiz2.action_importer()
        self.assertIn("0 objet(s) créé(s), 3 déjà présent(s)", wiz2.result)
        self.assertEqual(o.geojson, geom_before)

    def test_import_gpkg_zone(self):
        with file_open("service_travaux_carte/tests/data/arbres.gpkg", "rb") as f:
            data = f.read()
        wiz = self._wizard(source="fichier", fichier=base64.b64encode(data), fichier_nom="arbres.gpkg", zone="lieu",
                           lieu_id=self.Lieu._by_name("MaisonCommunale").id, rayon=250)
        wiz.action_lire()
        self.assertEqual(wiz.couche_ids.nombre, 3)
        wiz.action_charger()
        self.assertEqual(wiz.total, 2, "l'arbre hors zone est écarté")
        self.assertEqual(wiz.champ_id.name, "essence")
        for c in wiz.classe_ids:
            c.write({"importer": True, "type_id": self.Lieu._by_name("ArbreRemarquable").id})
        wiz.action_importer()
        self.assertIn("2 objet(s) créé(s)", wiz.result)
        hetre = self.env["travaux.objet"].search([("note", "=", "Hêtre pourpre du parc")])
        self.assertAlmostEqual(hetre.latitude, 50.7124, places=4)

    def test_import_geojson_lieux(self):
        fc = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::31370"}},
              "features": [{"type": "Feature", "id": 7, "properties": {"nom": "Plaine de jeux"},
                            "geometry": geo.map_coords(PARC, lambda x, y: (160000 + (x - 4.52) * 70000, 150000 + (y - 50.71) * 111000))}]}
        wiz = self._wizard(source="fichier", fichier=base64.b64encode(json.dumps(fc).encode()), fichier_nom="zones.geojson")
        wiz.action_lire()
        wiz.action_charger()
        wiz.classe_ids.write({"importer": True, "cible": "lieu"})
        wiz.action_importer()
        lieu = self.Lieu._by_name("Plaine de jeux")
        self.assertTrue(lieu.has_contour)
        self.assertEqual(lieu.carte_ext_ref, "fichier:zones:7")

    def test_import_spw(self):
        def fake(url, params=None, timeout=None):
            if url.endswith("/MapServer"):
                return _resp({"layers": [{"id": 4, "name": "Équipement - point", "geometryType": "esriGeometryPoint"},
                                         {"id": 9, "name": "Groupe", "subLayerIds": [4]}]})
            return _resp({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"GEOREF_ID": "G-1", "NATUR_DESC": "Point lumineux"},
                 "geometry": {"type": "Point", "coordinates": [4.5300, 50.7200]}},
                {"type": "Feature", "properties": {"GEOREF_ID": "G-2", "NATUR_DESC": "Avaloir - Petit"},
                 "geometry": {"type": "Point", "coordinates": [4.5301, 50.7201]}}]})
        with patch.object(import_geo.requests, "get", side_effect=fake):
            wiz = self._wizard(source="spw", url=import_geo.SPW_PICC, zone="rect", min_lng=4.51, min_lat=50.70,
                               max_lng=4.54, max_lat=50.73)
            wiz.action_lire()
            self.assertEqual(len(wiz.couche_ids), 1)
            self.assertTrue(wiz.couche_ids.importer, "couche équipements du PICC cochée d'office")
            wiz.action_charger()
        self.assertEqual(wiz.champ_id.name, "NATUR_DESC")
        classes = {c.valeur: c for c in wiz.classe_ids}
        self.assertEqual(classes["Avaloir - Petit"].type_id.name, "Avaloir")
        self.assertEqual(classes["Point lumineux"].type_id.name, "Lampadaire", "# créé à l'installation")
        self.assertTrue(classes["Point lumineux"].importer)
        wiz.action_importer()
        lp = self.env["travaux.objet"].search([("ext_ref", "=", "spw:Équipement - point:G-1")])
        self.assertEqual((lp.type_id.name, lp.picc_id), ("Lampadaire", "G-1"))

    def test_import_wfs_odwb(self):
        caps = b"""<?xml version="1.0"?><wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs">
            <FeatureTypeList><FeatureType><Name>inbw:bulles</Name><Title>Bulles a verre</Title></FeatureType></FeatureTypeList>
            </wfs:WFS_Capabilities>"""

        def fake(url, params=None, timeout=None):
            if params.get("request") == "GetCapabilities":
                return _resp(content=caps)
            if params.get("request") == "GetFeature":  # renvoyé en (lat, lon) : remis à l'endroit
                return _resp({"type": "FeatureCollection", "features": [
                    {"type": "Feature", "id": "b.1", "properties": {"type": "verre"},
                     "geometry": {"type": "Point", "coordinates": [50.7124, 4.5213]}}]})
            if url.endswith("/catalog/datasets"):
                return _resp({"results": [{"dataset_id": "arbres-remarquables", "features": ["geo", "analyze"],
                                           "metas": {"default": {"title": "Arbres remarquables"}},
                                           "fields": [{"name": "geo_point_2d", "type": "geo_point_2d"}]},
                                          {"dataset_id": "budget", "features": ["analyze"]}]})
            return _resp({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"id": "AR9", "espece": "Hêtre", "nom": "Hêtre du parc"},
                 "geometry": {"type": "Point", "coordinates": [4.5209, 50.71252]}}]})
        with patch.object(import_geo.requests, "get", side_effect=fake):
            wiz = self._wizard(source="wfs", url="https://geo.example.be/wfs?service=WFS&request=GetCapabilities")
            wiz.action_lire()
            self.assertEqual(wiz.couche_ids.code, "inbw:bulles")
            wiz.couche_ids.importer = True
            wiz.action_charger()
            wiz.classe_ids.write({"importer": True})
            wiz.action_importer()
            bulle = self.env["travaux.objet"].search([("ext_ref", "=", "wfs:Bulles a verre:b.1")])
            self.assertAlmostEqual(bulle.longitude, 4.5213, places=4)
            self.assertEqual(bulle.type_id.name, "Verre")
            wiz = self._wizard(source="odwb", recherche="arbres")
            wiz.action_lire()
            self.assertEqual(len(wiz.couche_ids), 1, "seuls les jeux géographiques sont proposés")
            wiz.couche_ids.importer = True
            wiz.action_charger()
            wiz.classe_ids.write({"importer": True, "type_id": self.Lieu._by_name("ArbreRemarquable").id})
            wiz.action_importer()
        arbre = self.env["travaux.objet"].search([("ext_ref", "=", "odwb:Arbres remarquables:AR9")])
        self.assertEqual((arbre.source, arbre.note), ("odwb", "Hêtre du parc"))

    def test_types_de_base(self):
        for name, geom in (("Pelouse", "surface"), ("Haie", "ligne"), ("Lampadaire", "point"), ("AireDeJeux", "surface")):
            t = self.Lieu._by_name(name)
            self.assertEqual((t.kind, t.geom_type), ("objet", geom))
        # supprimé par l'utilisateur : pas recréé à la mise à jour suivante
        banc = self.Lieu._by_name("Banc")
        banc.active = False
        self.env["travaux.carte.demo"]._types_de_base()
        self.assertFalse(banc.active)
