from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..wizards import import_picc


def _offline():
    return patch.object(import_picc.requests, "get", side_effect=requests.ConnectionError("hors ligne"))


def _picc(features):
    resp = MagicMock()
    resp.json.return_value = {"type": "FeatureCollection", "features": features}
    return patch.object(import_picc.requests, "get", return_value=resp)


def _feat(gid, nature, lng, lat):
    return {"type": "Feature", "properties": {"GEOREF_ID": gid, "NATUR_DESC": nature},
            "geometry": {"type": "Point", "coordinates": [lng, lat]}}


@tagged("post_install", "-at_install")
class TestCarte(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, travaux_no_live=True))
        cls.sup = new_test_user(cls.env, "sup_carte", groups="service_travaux.group_superviseur,service_travaux.group_agent")
        cls.agent = new_test_user(cls.env, "agent_carte", groups="service_travaux.group_agent")

    def _demo(self):
        with _offline():
            return self.env["travaux.carte.demo"].with_user(self.sup).action_charger()

    def test_demo_offline(self):
        res = self._demo()
        self.assertIn("copie du PICC", res["params"]["message"])
        Objet = self.env["travaux.objet"]
        av = self.env["travaux.lieu"]._by_name("Avaloir")
        self.assertEqual(Objet.search_count([("type_id", "=", av.id)]), 61)
        self.assertEqual(Objet.search_count([("type_id.name", "=", "ChambreDeVisite")]), 5)
        self.assertEqual(Objet.search_count([("type_id.name", "=", "Poteau")]), 52)
        self.assertEqual(Objet.search_count([("type_id.name", "=", "Poubelle")]), 16)
        self.assertEqual(Objet.search([("type_id", "=", av.id)], order="numero")[0].name, "AV-001")
        mc = self.env["travaux.lieu"]._by_name("MaisonCommunale")
        self.assertAlmostEqual(mc.latitude, 50.7124, places=3)
        # zones : contours, tournée lancée, deux zones faites
        tonte = self.env["travaux.circuit"].search([("name", "=", "Tonte · Maison communale")])
        self.assertEqual(len(tonte.zone_ids), 5)
        self.assertTrue(all(z.lieu_id.has_contour for z in tonte.zone_ids))
        self.assertTrue(tonte.lancement_en_cours)
        bt = tonte._bt_cours_tournee()
        zones = bt._tz_zones()
        self.assertEqual(sum(1 for z in zones if z["status"] == "fait"), 2)
        self.assertTrue(all(z["geojson"] for z in zones))
        # le relancer ne duplique rien
        self._demo()
        self.assertEqual(Objet.search_count([("type_id", "=", av.id)]), 61)
        self.assertEqual(Objet.search_count([("type_id.name", "=", "Poubelle")]), 16)
        # carte du bureau
        data = self.env["travaux.carte"].with_user(self.sup).carte_data()
        self.assertEqual(len(data["objets"]), 143)
        self.assertEqual(len(data["zones"]), 5)
        self.assertEqual(sum(1 for z in data["zones"] if z["etat"] == "fait"), 2)

    def test_picc_import_dedupe(self):
        self._demo()
        Objet = self.env["travaux.objet"]
        av = Objet.search([("type_id.name", "=", "Avaloir")], order="numero", limit=1)
        feats = [_feat("PICC-1", "Avaloir - Petit", av.longitude, av.latitude),
                 _feat("PICC-2", "Avaloir - Petit", 4.5300, 50.7200),
                 _feat("PICC-3", "Point lumineux", 4.5301, 50.7201)]
        wiz = self.env["travaux.import.picc"].with_user(self.sup).create({"mode": "rect", "min_lng": 4.51, "min_lat": 50.70,
                                                                         "max_lng": 4.54, "max_lat": 50.73})
        with _picc(feats):
            wiz.action_compter()
        self.assertEqual(wiz.total, 3)
        lines = {line.nature: line for line in wiz.line_ids}
        self.assertTrue(lines["Avaloir - Petit"].importer)
        self.assertTrue(lines["Point lumineux"].importer)
        wiz.action_importer()
        self.assertIn("2 objet(s) créé(s), 1 déjà présent", wiz.result)
        self.assertEqual(av.picc_id, "PICC-1", "l'objet de la copie reçoit son identifiant PICC")
        lp = Objet.search([("picc_id", "=", "PICC-3")])
        self.assertEqual(lp.type_id.name, "Lampadaire")
        # second import : rien de nouveau
        wiz2 = self.env["travaux.import.picc"].with_user(self.sup).create({"mode": "rect", "min_lng": 4.51, "min_lat": 50.70,
                                                                          "max_lng": 4.54, "max_lat": 50.73})
        with _picc(feats):
            wiz2.action_compter()
        wiz2.action_importer()
        self.assertIn("0 objet(s) créé(s), 3 déjà présent", wiz2.result)

    def test_demande_objet(self):
        self._demo()
        o = self.env["travaux.objet"].search([("type_id.name", "=", "Avaloir")], limit=1)
        d = self.env["travaux.demande"].create({"titre": "Avaloir bouché", "objet_recense_id": o.id})
        self.assertIn(o.type_id, d.objet_ids)
        self.assertEqual(d.latitude, o.latitude)
        self.assertEqual(d.lieu_id, o.lieu_id)
        self.assertEqual(o.nb_ouvertes, 1)
        # signalement depuis l'app terrain
        rec_id = self.env["travaux.demande"].with_user(self.agent).tv_objet_signaler(o.id, "Grille cassée")
        d2 = self.env["travaux.demande"].browse(rec_id)
        self.assertEqual(d2.objet_recense_id, o)
        self.assertEqual(d2.state, "recue")
        infos = self.env["travaux.demande"].with_user(self.agent).tv_objet_infos(o.id)
        self.assertEqual(len(infos["ouvertes"]), 2)
        self.assertIn("Avaloir bouché", infos["choix"])

    def test_droits_carte(self):
        self._demo()
        Carte = self.env["travaux.carte"]
        av = self.env["travaux.lieu"]._by_name("Avaloir")
        new_id = Carte.with_user(self.agent).carte_ajouter(av.id, 50.7125, 4.5215)
        new = self.env["travaux.objet"].browse(new_id)
        self.assertEqual(new.state, "propose")
        self.assertEqual(new.lieu_id.name, "MaisonCommunale")
        # l'agent peut corriger sa proposition, pas un objet figé
        Carte.with_user(self.agent).carte_deplacer(new_id, 50.71252, 4.52152)
        fige = self.env["travaux.objet"].search([("state", "=", "valide")], limit=1)
        with self.assertRaises(AccessError):
            Carte.with_user(self.agent).carte_deplacer(fige.id, 50.71, 4.52)
        with self.assertRaises(AccessError):
            Carte.with_user(self.agent).carte_figer(new_id)
        Carte.with_user(self.sup).carte_figer(new_id)
        self.assertEqual(new.state, "valide")

    def test_lieu_proche_contour(self):
        self._demo()
        Lieu = self.env["travaux.lieu"]
        parc = Lieu._by_name("Parc de la maison communale")
        self.assertTrue(parc.surface > 500)
        self.assertEqual(Lieu._lieu_proche(parc.latitude, parc.longitude), parc)

    def test_conversation_lot(self):
        self._demo()
        pb = self.env["travaux.circuit"].search([("name", "=", "Poubelles · Maison communale")])
        bt = pb._bt_cours_tournee()
        conv = self.env["travaux.demande"].tv_conversation(bt.id)
        self.assertEqual(conv["carte"]["mode"], "lot")
        self.assertEqual(len(conv["carte"]["objets"]), 16)
        self.assertTrue(conv["carte"]["autour"])
