"""Données fictives reprises de la maquette « Service Travaux – Rixensart »."""
import base64
import random
from datetime import datetime, time, timedelta

from odoo import api, fields, models
from odoo.tools.misc import file_path

# Organigramme : un département (Service technique), deux services (Voirie, Bâtiment),
# trois contremaîtres (deux en voirie, un en bâtiment), chacun avec un brigadier en binôme.
SERVICES = ["Voirie", "Bâtiment"]
CONTREMAITRES = [
    # clé, nom, identifiant, service, brigadier (nom, identifiant)
    ("cv1", "Jean Lambert", "jean.lambert", "Voirie", ("Nicolas Petit", "nicolas.petit")),
    ("cv2", "Sophie Martin", "sophie.martin", "Voirie", ("Laura Simon", "laura.simon")),
    ("cb", "Michel Dupont", "michel.dupont", "Bâtiment", ("David Leroy", "david.leroy")),
]
S_PLAT = [1] * 12
S_ETE = [.7, .5, .4, .4, .6, 1, 1.4, 1.6, 1.5, 1.3, 1.2, 1]
S_HIVER = [1.2, 1.3, 1.4, 1.3, 1.1, .9, .8, .7, .8, .9, 1, 1.1]
TEAMS = [
    # clé, nom, contremaître, couleur, effectif min, capacité hebdo, historique annuel (demandes, h tournées, h maintenance, h demandes), saisonnalité oct→sep
    ("voirie", "Voirie", "cv1", 2, 2, 114, (320, 900, 700, 1900), [1, .9, .8, .9, 1, 1.1, 1.1, 1.2, 1.1, 1, 1, 1]),
    ("signa", "Signalisation", "cv1", 1, 1, 76, (110, 300, 500, 420), S_PLAT),
    ("pret", "Prêt de matériel", "cv1", 4, 1, 76, (60, 0, 900, 240), [.8, .7, 1.3, .6, .7, .9, 1.1, 1.3, 1.5, 1.2, 1.1, 1.2]),
    ("prop", "Propreté publique", "cv1", 8, 2, 114, (300, 3200, 400, 500), [.9, .8, .9, .8, .9, 1, 1.1, 1.1, 1.2, 1.2, 1.1, 1]),
    ("hydro", "Hydrocureuse", "cv1", 5, 2, 76, (140, 2100, 200, 350), [1.5, 1.4, 1.2, 1, .9, .8, .7, .8, .9, 1, 1.1, 1.3]),
    ("bal", "Balayeuse", "cv1", 7, 1, 76, (40, 2400, 150, 90), [1.4, 1.3, 1, .8, .8, 1, 1, .9, .9, .9, 1, 1.2]),
    ("ev1", "Espaces verts 1", "cv2", 10, 2, 114, (70, 1500, 400, 200), S_ETE),
    ("ev2", "Espaces verts 2", "cv2", 9, 2, 114, (60, 1500, 400, 180), S_ETE),
    ("ev3", "Espaces verts 3", "cv2", 11, 2, 76, (40, 1100, 300, 120), S_ETE),
    ("ev4", "Espaces verts 4", "cv2", 3, 2, 76, (30, 1000, 300, 100), S_ETE),
    ("elec", "Électriciens", "cb", 3, 1, 76, (95, 400, 500, 610), S_HIVER),
    ("menu", "Menuisiers", "cb", 6, 1, 76, (80, 0, 700, 560), S_PLAT),
    ("macon", "Maçons", "cb", 1, 2, 76, (60, 0, 900, 520), [1, .8, .6, .6, .8, 1.1, 1.2, 1.3, 1.3, 1.2, 1.1, 1]),
    ("plomb", "Plombiers", "cb", 5, 1, 76, (120, 0, 600, 700), S_HIVER),
    ("peint", "Peintres", "cb", 4, 1, 76, (40, 0, 1100, 260), [.9, .9, .9, .9, 1, 1, 1.1, 1.3, 1.3, 1, .9, .9]),
    ("poly", "Polyvalents", "cb", 7, 2, 114, (170, 0, 900, 1100), S_PLAT),
]
PEOPLE = [
    ("Marc", "voirie", "Chef d'équipe", "present"), ("Ahmed", "voirie", "Ouvrier", "conge"), ("Luc", "voirie", "Ouvrier", "formation"),
    ("Gilles", "signa", "Chef d'équipe", "present"), ("Mehdi", "signa", "Ouvrier", "present"),
    ("Fabrice", "pret", "Chef d'équipe", "present"), ("Loïc", "pret", "Chauffeur", "present"),
    ("Nadia", "prop", "Chef d'équipe", "present"), ("Yves", "prop", "Ouvrier", "present"), ("Karim", "prop", "Ouvrier", "present"), ("Léa", "prop", "Ouvrière", "conge"),
    ("Bruno", "hydro", "Chef d'équipe", "present"), ("Alex", "hydro", "Chauffeur", "present"),
    ("Didier", "bal", "Chauffeur", "present"),
    ("Julie", "ev1", "Chef d'équipe", "present"), ("Tom", "ev1", "Jardinier", "present"), ("Sam", "ev1", "Jardinier", "recup"),
    ("Antoine", "ev2", "Chef d'équipe", "present"), ("Inès", "ev2", "Jardinière", "present"), ("Rémy", "ev2", "Jardinier", "present"),
    ("Coralie", "ev3", "Chef d'équipe", "present"), ("Jordan", "ev3", "Jardinier", "present"),
    ("Thierry", "ev4", "Chef d'équipe", "present"), ("Maxime", "ev4", "Jardinier", "absent"),
    ("Olivier", "elec", "Chef d'équipe", "present"), ("Simon", "elec", "Électricien", "present"),
    ("Patrick", "menu", "Chef d'équipe", "present"), ("Quentin", "menu", "Menuisier", "present"),
    ("Pierre", "macon", "Chef d'équipe", "present"), ("Hugo", "macon", "Maçon", "present"), ("Kevin", "macon", "Maçon", "present"),
    ("Stéphane", "plomb", "Chef d'équipe", "present"), ("Benoît", "plomb", "Plombier", "present"),
    ("Christian", "peint", "Chef d'équipe", "present"), ("Mathieu", "peint", "Peintre", "present"),
    ("Rachid", "poly", "Chef d'équipe", "present"), ("Élodie", "poly", "Ouvrière polyvalente", "present"), ("Vincent", "poly", "Ouvrier polyvalent", "present"),
    ("Jérôme", None, "Ouvrier polyvalent", "present"), ("Sabrina", None, "Ouvrière polyvalente", "present"),
]
CATEGORIES = [
    ("Voirie", "voirie", False), ("Signalisation", "signa", False), ("Égouttage", "hydro", False),
    ("Éclairage public", "elec", False), ("Bâtiments communaux", "poly", False),
    ("Espaces verts", "ev1", False), ("Propreté", "prop", False), ("Prêt de matériel", "pret", False),
    ("Hors compétence", None, True),
]
# Lieux-dits : nom, adresse, équipe qui y travaille le plus, nb de travaux historiques à rattacher, position
LIEUX = [
    ("Salle omnisports", "Rue des Sports 1", "poly", 12, (50.7070, 4.5215)),
    ("École du Centre", "Rue de l'École 4", "plomb", 9, (50.7136, 4.5246)),
    ("Maison communale", "Avenue de Merode 65", "menu", 7, (50.7107, 4.5301)),
    ("École de Bourgeois", "Rue de Bourgeois 12", "peint", 5, (50.7189, 4.5122)),
    ("Parc du Centre", "Rue du Parc 2", "ev1", 6, (50.7131, 4.5275)),
    ("Cimetière communal", "Chemin du Cimetière 1", "ev2", 8, (50.7042, 4.5251)),
    ("Place communale", "Place communale", "prop", 10, (50.7102, 4.5288)),
    ("Dépôt communal", "Rue de Rosières 80", "pret", 4, (50.7031, 4.5402)),
]
# Zones des tournées : lieu-dit et position (pour « la plus proche » dans l'app terrain)
ZONES_GEO = {
    "Zone 1 · Parc du Centre": ("Parc du Centre", None), "Zone 2 · Place communale": ("Place communale", None),
    "Zone 3 · Abords de l'école du Centre": ("École du Centre", None), "Zone 4 · Maison communale": ("Maison communale", None),
    "Zone 5 · Rond-point de la Gare": ("Rond-point de la Gare", (50.7154, 4.5338)),
    "Zone 6 · Plaine de jeux du Verger": ("Plaine de jeux du Verger", (50.7174, 4.5212)),
    "Zone 7 · Talus de l'avenue de Mérode": ("Talus de l'avenue de Mérode", (50.7083, 4.5324)),
    "Zone 8 · Salle omnisports": ("Salle omnisports", None),
    "Zone 9 · Cimetière communal": ("Cimetière communal", None), "Zone 10 · École de Bourgeois": ("École de Bourgeois", None),
    "Zone 11 · Quartier du Lac": ("Quartier du Lac", (50.7232, 4.5384)),
    "Zone 12 · Rosières, place": ("Place de Rosières", (50.7398, 4.5520)),
    "Zone 13 · Avenue des Tilleuls": ("Avenue des Tilleuls", (50.7121, 4.5165)),
    "Zone 14 · Dépôt communal": ("Dépôt communal", None),
    "Sentier du Lac": ("Sentier du Lac", (50.7245, 4.5360)), "Sentier du Moulin": ("Sentier du Moulin", (50.7012, 4.5180)),
    "Sentier des Écoles": ("Sentier des Écoles", (50.7150, 4.5230)), "Sentier du Bois": ("Sentier du Bois", (50.6985, 4.5290)),
}
# Objets : ce qui est concerné. Le jeu de données se construit à partir des demandes taguées.
OBJETS = [
    ("Avaloir", ["Avaloir bouché"]),
    ("Lampadaire", ["Lampadaire éteint", "Éclairage clignotant", "Éclairage public éteint"]),
    ("Poubelle", ["Corbeille débordante", "Corbeille cassée"]),
    ("Chaufferie", ["Chauffage en panne"]),
    ("Panneau", ["Panneau endommagé", "Panneau stop couché"]),
    ("Toiture", ["Fuite en toiture, salle omnisports"]),
    ("Aire de jeux", ["Branche cassée, aire de jeux"]),
    ("Banc", []),
]
PROPOSED_TAG = "Bulles à verre Rue Haute"
# Adresse qui revient sans être nommée (pour montrer « Nommer ce lieu »)
REPEAT_ADDR = ("Rue Haute 51", "prop", ["Dépôt clandestin", "Déchets autour des bulles à verre", "Dépôt clandestin"])
# Tâches de la semaine (jour 0 = lundi)
TASKS = [
    ("ev1", 0, "maintenance", "Taille des haies", "École du Centre", 6, 6.5),
    ("ev2", 2, "maintenance", "Abattage arbre dangereux", "Avenue de la Gare", 8, None),
    ("ev3", 3, "maintenance", "Réfection sentier", "Sentier du Lac", 6, None),
    ("ev4", 4, "maintenance", "Plantations d'automne", "Place communale", 6, None),
    ("prop", 0, "tournee", "Corbeilles centre", "Centre", 3, 3),
    ("prop", 1, "tournee", "Corbeilles villages", "Villages", 4, 4.5),
    ("prop", 2, "tournee", "Corbeilles centre", "Centre", 3, None),
    ("prop", 3, "tournee", "Ramassage dépôts signalés", "Villages", 5, None),
    ("prop", 4, "maintenance", "Nettoyage abribus", "Arrêts de bus", 5, None),
    ("bal", 0, "tournee", "Balayage filets d'eau secteur Centre", "Centre", 7, 7),
    ("bal", 2, "tournee", "Balayage filets d'eau secteur Gare", "Gare", 7, None),
    ("bal", 3, "tournee", "Balayage après marché", "Place communale", 4, None),
    ("hydro", 0, "tournee", "Curage avaloirs secteur Nord", "Secteur Nord", 7, 7),
    ("hydro", 1, "tournee", "Curage avaloirs secteur Nord", "Secteur Nord", 7, None),
    ("hydro", 3, "tournee", "Curage avaloirs secteur Sud", "Secteur Sud", 7, None),
    ("voirie", 1, "maintenance", "Rebouchage nids de poule", "Secteur Est", 4, None),
    ("signa", 4, "maintenance", "Contrôle signalisation", "Toute la commune", 4, None),
    ("pret", 3, "maintenance", "Livraison barrières et podium", "Place communale", 5, None),
    ("plomb", 0, "maintenance", "Contrôle chaufferies", "Écoles", 4, 5),
    ("poly", 2, "maintenance", "Entretien salle omnisports", "Salle omnisports", 6, None),
    ("elec", 3, "maintenance", "Éclairage LED couloir", "Maison communale", 5, None),
    ("elec", 4, "tournee", "Tournée éclairage (nuit)", "Toute la commune", 5, None),
    ("menu", 1, "maintenance", "Remplacement porte du préau", "École de Bourgeois", 6, None),
    ("peint", 2, "maintenance", "Peinture classe 3", "École du Centre", 14, None),
    ("macon", 3, "maintenance", "Réparation muret", "Cimetière communal", 8, None),
]
# Demandes de la maquette : ref, titre, lieu, source, catégorie, état, équipe, jour, urgent, photo avant, photo après, message, consigne, demandeur
JOBS = [
    ("DT-0142", "Nid de poule dangereux", "Rue de la Station 24", "guichet", "Voirie", "planif", "voirie", 1, True,
     "nid_avant", None, "Trou profond sur la bande de droite, environ 40 cm. Dangereux pour les vélos.",
     "Prioritaire : sécuriser avant midi. Camion 2 et enrobé à froid.", "Claire Dubois"),
    ("DT-0145", "Panneau stop couché", "Avenue de la Gare", "police", "Signalisation", "recue", None, None, True,
     None, None, "Panneau couché au carrefour avec la rue Haute, visibilité nulle.", None, None),
    ("DT-0151", "Avaloir bouché", "Rue des Écoles 12", "email", "Égouttage", "recue", None, None, False,
     "avaloir_avant", None, "L'eau stagne sur la route à chaque averse, grille couverte de feuilles.", None, "Paul Lemaire"),
    ("DT-0147", "Dépôt de gravats", "Rue Haute 51", "app", "Propreté", "recue", None, None, False,
     "depot_avant", None, "Gravats et pots de peinture déposés sur le trottoir.", None, None),
    ("DT-0153", "Haie privée qui déborde", "Avenue des Tilleuls 8", "citoyen", "Hors compétence", "recue", None, None, False,
     None, None, "La haie du voisin empiète sur le trottoir, on ne peut plus passer avec une poussette.", None, "Sarah Collin"),
    ("DT-0146", "Fuite en toiture, salle omnisports", "Salle omnisports", "service", "Bâtiments communaux", "recue", None, None, False,
     None, None, "Fuite au-dessus du vestiaire 2, un seau est posé au sol.", None, None),
    ("DT-0144", "Nid de poule", "Rue du Moulin 2", "guichet", "Voirie", "planif", "voirie", 3, False,
     "nid_avant", None, "Nid de poule devant le numéro 2.", "Planifié jeudi avec l'enrobé de la rue de la Station.", None),
    ("DT-0139", "Éclairage public éteint", "Place communale", "service", "Éclairage public", "planif", "elec", 2, False,
     "eclairage_avant", None, "Lampadaire éteint devant l'école depuis 3 jours.", "Olivier passe mercredi matin.", None),
    ("DT-0137", "Dépôt clandestin", "Chemin du Bois 3", "service", "Propreté", "cours", "prop", 1, False,
     "depot_avant", None, "Sacs, matelas et carton déposés contre le mur.", "La camionnette est libre cet après-midi.", None),
    ("DT-0133", "Branche cassée, aire de jeux", "Parc du Centre", "police", "Espaces verts", "valider", "ev1", 0, True,
     "branche_avant", "branche_apres", "Branche cassée au-dessus de l'aire de jeux, accès fermé.",
     "Urgent : des enfants passent par là.", None),
    ("DT-0128", "Avaloir bouché", "Rue des Écoles 8", "guichet", "Égouttage", "close", "hydro", 0, False,
     "avaloir_avant", "avaloir_apres", "Avaloir couvert de feuilles, l'eau stagne sur la route.", None, "Marie Renard"),
]
JOB_HOURS = {"DT-0133": 2.5, "DT-0128": 1.5}
# Positions approximatives (démo) autour du centre de Rixensart
COORDS = {
    "DT-0142": (50.7125, 4.5320), "DT-0145": (50.7098, 4.5268), "DT-0151": (50.7141, 4.5241),
    "DT-0147": (50.7087, 4.5352), "DT-0153": (50.7162, 4.5305), "DT-0146": (50.7070, 4.5215),
    "DT-0144": (50.7110, 4.5395), "DT-0139": (50.7102, 4.5288), "DT-0137": (50.7055, 4.5330),
    "DT-0133": (50.7131, 4.5275), "DT-0128": (50.7146, 4.5236),
}
_H_EV = ["Branche tombée", "Haie à tailler", "Arbre dangereux"]
HIST_TITLES = {
    "voirie": ["Nid de poule", "Pavés déchaussés", "Bordure cassée"],
    "signa": ["Panneau endommagé", "Marquage effacé", "Potelet arraché"],
    "pret": ["Prêt de barrières", "Montage podium", "Prêt de tables et bancs"],
    "prop": ["Dépôt clandestin", "Corbeille débordante", "Tags", "Corbeille cassée"],
    "hydro": ["Avaloir bouché", "Curage de fossé", "Odeur d'égout"],
    "bal": ["Verre sur la chaussée", "Boue sur la voirie"],
    "ev1": _H_EV, "ev2": _H_EV, "ev3": _H_EV, "ev4": _H_EV,
    "elec": ["Lampadaire éteint", "Coffret électrique ouvert", "Éclairage clignotant"],
    "menu": ["Porte bloquée", "Serrure cassée", "Fenêtre qui ferme mal"],
    "macon": ["Mur fissuré", "Marche descellée", "Carrelage décollé"],
    "plomb": ["Fuite d'eau", "Chauffage en panne", "WC bouché"],
    "peint": ["Mur à repeindre", "Tags sur façade"],
    "poly": ["Vitre cassée", "Mobilier à déplacer", "Store bloqué"],
}
STREETS = ["Rue de la Station", "Rue Haute", "Avenue de la Gare", "Rue des Écoles", "Rue du Moulin",
           "Chemin du Bois", "Avenue des Tilleuls", "Place communale", "Rue de Genval", "Rue de Rosières"]


def _img(name):
    path = file_path("service_travaux_demo/static/img/%s.jpg" % name)
    with open(path, "rb") as f:
        return base64.b64encode(f.read())


class TravauxDemande(models.Model):
    _inherit = "travaux.demande"

    @api.model
    def _travaux_demo_coords(self):
        """Exécuté à chaque mise à jour : positions GPS, auteurs lisibles, lieux-dits, présences, chronologie."""
        self._travaux_demo_lieux()
        self._travaux_demo_org()
        self._travaux_demo_circuits()
        self._travaux_demo_zones_geo()
        self._travaux_demo_topup()
        self._travaux_demo_presences()
        self._travaux_demo_chronologie()
        for ref, (lat, lng) in COORDS.items():
            rec = self.search([("name", "=", ref)], limit=1)
            if rec and not rec.latitude:
                rec.with_context(travaux_no_live=True).write({"latitude": lat, "longitude": lng})
        Employee = self.env["hr.employee"]
        guichet = Employee.search([("name", "=", "Nathalie"), ("fonction", "=", "Guichet")], limit=1)
        if not guichet:
            guichet = Employee.create({"name": "Nathalie", "fonction": "Guichet"})
        if not guichet.work_contact_id:
            guichet.work_contact_id = self.env["res.partner"].create({"name": "Nathalie"})
        # Compte secrétariat (vue globale)
        if not self.env["res.users"].search([("login", "=", "secretariat")], limit=1):
            self.env["res.users"].with_context(no_reset_password=True).create({
                "name": "Secrétariat technique", "login": "secretariat", "email": "secretariat@rixensart.example",
                "group_ids": [(6, 0, [self.env.ref("service_travaux.group_secretariat").id, self.env.ref("base.group_user").id])]})
        # Page d'accueil des comptes de démo
        homes = {"marc": "service_travaux.action_client_terrain", "julie": "service_travaux.action_client_terrain",
                 "nadia": "service_travaux.action_client_terrain"}
        homes.update({login: "service_travaux.action_client_suivi" for _k, _n, login, _s, _b in CONTREMAITRES})
        homes.update({b[1]: "service_travaux.action_client_suivi" for _k, _n, _l, _s, b in CONTREMAITRES})
        homes.update({"secretariat": "service_travaux.action_client_demandes"})
        for login, xmlid in homes.items():
            user = self.env["res.users"].search([("login", "=", login)], limit=1)
            if user and not user.action_id:
                user.action_id = self.env.ref(xmlid).id
        root = self.env.ref("base.partner_root")
        for ref in COORDS:
            rec = self.search([("name", "=", ref)], limit=1)
            if not rec:
                continue
            chef = rec.equipe_id.chef_id
            if chef and not chef.work_contact_id:
                chef.work_contact_id = self.env["res.partner"].create({"name": chef.name})
            for msg in rec.message_ids.filtered(lambda m: m.author_id == root and m.message_type == "comment"):
                body = msg.body or ""
                if "APRÈS" in body and chef:
                    msg.sudo().author_id = chef.work_contact_id
                elif "APRÈS" not in body:
                    msg.sudo().author_id = guichet.work_contact_id
        return True

    @api.model
    def _travaux_demo_org(self):
        """Organigramme (services) et tâches récurrentes de démonstration, créés une seule fois."""
        Team = self.env["travaux.equipe"]
        Dept = self.env["hr.department"]
        teams = {t.name: t for t in Team.search([])}
        if not Team.search_count([("department_id", "!=", False)]) and not Dept.search_count([("name", "=", "Service technique")]):
            top = Dept.create({"name": "Service technique"})
            service = {name: Dept.create({"name": name, "parent_id": top.id}) for name in SERVICES}
            du = {key: srv for key, _n, _l, srv, _b in CONTREMAITRES}
            for _key, name, sup, *_r in TEAMS:
                if name in teams:
                    teams[name].department_id = service[du[sup]]
        Contact = self.env["travaux.transfert.contact"].with_context(active_test=False)
        if not Contact.search_count([]):
            urba = Contact.create({"name": "Service Urbanisme – police de l'environnement", "email": "urbanisme@rixensart.example",
                                   "note": "Haies privées, dépôts sur terrain privé, litiges de voisinage"})
            Contact.create({"name": "Police locale", "email": "police@rixensart.example", "note": "Stationnement, sécurité, véhicules abandonnés"})
            Contact.create({"name": "Gestionnaire du réseau électrique", "email": "pannes@reseau.example", "note": "Éclairage sur voirie régionale, câbles"})
            self.env["travaux.categorie"].search([("hors_competence", "=", True)]).write({"transfert_contact_id": urba.id})
        Rec = self.env["travaux.recurrence"].with_context(active_test=False)
        if not Rec.search_count([]):
            today = fields.Date.context_today(self)
            start = today + timedelta(days=7 - today.weekday())  # lundi prochain : la semaine en cours est déjà remplie
            base = {"date_start": start}
            todo = [
                ("Propreté publique", {"name": "Corbeilles centre", "adresse": "Centre", "duree_prevue": 3, "repeat_unit": "week",
                                       "mon": True, "wed": True, "fri": True,
                                       "liste": "Place communale\nRue Haute\nAvenue de la Gare\nParc du Centre\nAbords des écoles"}),
                ("Propreté publique", {"name": "Corbeilles villages", "adresse": "Villages", "duree_prevue": 4, "repeat_unit": "week",
                                       "tue": True, "thu": True}),
                ("Hydrocureuse", {"name": "Curage avaloirs", "adresse": "Par secteur", "duree_prevue": 7, "repeat_unit": "week",
                                  "mon": True, "tue": True, "thu": True}),
                ("Balayeuse", {"name": "Balayage des filets d'eau", "adresse": "Par secteur", "duree_prevue": 7, "repeat_unit": "week",
                               "mon": True, "wed": True}),
                ("Balayeuse", {"name": "Balayage après marché", "adresse": "Place communale", "duree_prevue": 4,
                               "repeat_unit": "week", "thu": True}),
                ("Plombiers", {"name": "Contrôle chaufferies", "type_travail": "maintenance", "adresse": "Écoles", "duree_prevue": 4,
                               "repeat_unit": "month", "repeat_on_month": "day", "repeat_week": "1", "repeat_weekday": "tue"}),
                ("Électriciens", {"name": "Contrôle des éclairages de secours", "type_travail": "maintenance", "adresse": "Bâtiments communaux",
                                  "duree_prevue": 5, "repeat_unit": "month", "repeat_on_month": "day", "repeat_week": "2", "repeat_weekday": "wed"}),
            ]
            for team, vals in todo:
                if team in teams:
                    Rec.create(dict(base, equipe_id=teams[team].id, **vals))
        return True

    @api.model
    def _travaux_demo_zones_geo(self):
        """Chaque zone de démo est reliée à un lieu-dit positionné (sans toucher aux zones déjà reliées)."""
        Lieu = self.env["travaux.lieu"].with_context(active_test=False)
        for zone in self.env["travaux.circuit.zone"].search([("lieu_id", "=", False), ("name", "in", list(ZONES_GEO))]):
            name, pos = ZONES_GEO[zone.name]
            lieu = Lieu._by_name(name)
            if not lieu and pos:
                lieu = Lieu.create({"name": name, "latitude": pos[0], "longitude": pos[1]})
            if lieu:
                zone.lieu_id = lieu.id
        return True

    def _travaux_demo_circuits(self):
        """Tournées de zones de démonstration (créées une seule fois), avec quelques passages déjà faits."""
        Circuit = self.env["travaux.circuit"].with_context(active_test=False)
        if Circuit.search_count([]):
            return True
        teams = {t.name: t for t in self.env["travaux.equipe"].search([])}
        today = fields.Date.context_today(self)
        # (équipe, tournée, saison, [(zone, à savoir, jours depuis le dernier passage ou None, fait dans le tour en cours)])
        todo = [
            ("Espaces verts 1", "Zones 1 à 8", "une", (False, False), [
                ("Zone 1 · Parc du Centre", "", 2, True), ("Zone 2 · Place communale", "", 2, True),
                ("Zone 3 · Abords de l'école du Centre", "Avant 8 h ou après 16 h", 1, True),
                ("Zone 4 · Maison communale", "", 1, True), ("Zone 5 · Rond-point de la Gare", "Signalisation de chantier", 12, False),
                ("Zone 6 · Plaine de jeux du Verger", "", 14, False), ("Zone 7 · Talus de l'avenue de Mérode", "Débroussailleuse", 26, False),
                ("Zone 8 · Salle omnisports", "", None, False)]),
            ("Espaces verts 2", "Zones 9 à 14", "une", (False, False), [
                ("Zone 9 · Cimetière communal", "Clé au dépôt", 3, True), ("Zone 10 · École de Bourgeois", "", 8, False),
                ("Zone 11 · Quartier du Lac", "", 15, False), ("Zone 12 · Rosières, place", "", 21, False),
                ("Zone 13 · Avenue des Tilleuls", "", 30, False), ("Zone 14 · Dépôt communal", "", None, False)]),
            ("Espaces verts 3", "Sentiers", "une", (False, False), [
                ("Sentier du Lac", "", 6, True), ("Sentier du Moulin", "", 19, False),
                ("Sentier des Écoles", "", 33, False), ("Sentier du Bois", "", None, False)]),
            ("Espaces verts 4", "Taille et entretien d'hiver", "une", ("11", "3"), [
                ("Haies du Parc du Centre", "", None, False), ("Massifs de la Place communale", "", None, False),
                ("Arbres de l'avenue de la Gare", "Nacelle à réserver", None, False)]),
            ("Propreté publique", "Tournée des poubelles", "lot", (False, False), [
                ("Centre : 45 poubelles", "", None, False), ("Gare : 30 poubelles", "", None, False),
                ("Villages : 50 poubelles", "", None, False), ("Parcs et plaines : 25 poubelles", "", None, False)]),
            ("Balayeuse", "Filets d'eau", "lot", (False, False), [
                ("Secteur Centre", "", None, False), ("Secteur Gare", "", None, False),
                ("Secteur Lac", "", None, False), ("Secteur Villages", "", None, False)]),
        ]
        Passage = self.env["travaux.passage"]
        for team, name, mode, (m1, m2), zones in todo:
            if team not in teams:
                continue
            circuit = Circuit.create({"name": name, "equipe_id": teams[team].id, "mode": mode, "mois_debut": m1, "mois_fin": m2, "tour_no": 3})
            first = None
            for i, (zname, note, days, done) in enumerate(zones):
                zone = self.env["travaux.circuit.zone"].create({"circuit_id": circuit.id, "sequence": 10 * (i + 1), "name": zname, "note": note})
                if days is None:
                    continue
                day = today - timedelta(days=days)
                tour = 3 if done else 2
                Passage.create({"zone_id": zone.id, "date": day, "tour_no": tour, "user_id": teams[team].chef_id.user_id.id or self.env.uid})
                zone.write({"dernier_passage": day, "tour_fait": tour})
                if done and (first is None or day < first):
                    first = day
            circuit.tour_debut = first
        return True

    @api.model
    def _travaux_demo_lieux(self):
        from odoo.addons.service_travaux.models.lieu import norm_adresse, tag_key
        Lieu = self.env["travaux.lieu"].with_context(active_test=False)
        teams = {t.name: t for t in self.env["travaux.equipe"].search([])}
        keys = {k: n for k, n, *_r in TEAMS}
        Demande = self.with_context(travaux_no_live=True, tracking_disable=True, mail_notrack=True)
        known = {norm_adresse(l.adresse) for l in Lieu.search([])}
        for name, adr, team_key, count, (lat, lng) in LIEUX:
            lieu = Lieu._by_name(name)
            if not lieu:
                if norm_adresse(adr) in known or tag_key(name) in {k.replace(" ", "") for k in known}:
                    continue
                lieu = Lieu.create({"name": name, "adresse": adr, "latitude": lat, "longitude": lng})
            elif not lieu.latitude:
                lieu.write({"latitude": lat, "longitude": lng})
            # historique : quelques travaux clôturés à ce lieu (pour le tableau de bord)
            team = teams.get(keys.get(team_key))
            have = Demande.search_count([("lieu_id", "=", lieu.id), ("state", "=", "close")])
            if team and have < count:
                cands = Demande.search([("lieu_id", "=", False), ("equipe_id", "=", team.id), ("state", "=", "close"),
                                        ("type_travail", "=", "demande"), ("name", "=like", "DT-H%")],
                                       limit=count - have, order="id")
                for d in cands:
                    d.write({"lieu_id": lieu.id, "adresse": lieu.adresse})
        # une adresse qui revient, pas encore nommée
        adr, team_key, titles = REPEAT_ADDR
        team = teams.get(keys.get(team_key))
        if team and Demande.search_count([("adresse", "=", adr)]) < 3:
            day = fields.Date.context_today(self) - timedelta(days=60)
            vals = []
            for i, t in enumerate(titles):
                d = day + timedelta(days=21 * i)
                start = datetime.combine(d, time(8, 0))
                vals.append({"titre": t, "adresse": adr, "type_travail": "demande", "equipe_id": team.id,
                             "state": "close", "source": "guichet", "date_prevue": d, "date_debut": start,
                             "date_fin": start + timedelta(hours=1.5), "date_cloture": start + timedelta(hours=6)})
            Demande.with_context(travaux_no_photo_post=True, mail_create_nolog=True).create(vals)
        # # objets : tagués sur les demandes existantes (une fois)
        for name, titles in OBJETS:
            obj = Lieu._by_name(name) or Lieu.create({"name": name, "kind": "objet"})
            if obj.kind != "objet" or obj.demande_objet_ids or not titles:
                continue
            recs = Demande.search([("titre", "in", titles), ("type_travail", "=", "demande")], limit=60, order="id desc")
            if recs:
                recs.write({"objet_ids": [(4, obj.id)]})
                self.env.cr.execute("""UPDATE travaux_demande SET latitude = 50.705 + random() * 0.012,
                                              longitude = 4.515 + random() * 0.025
                                        WHERE id IN %s AND COALESCE(latitude, 0) = 0""", (tuple(recs.ids),))
        # un # proposé par le terrain, à confirmer par un superviseur
        job = Demande.search([("name", "=", "DT-0147"), ("lieu_id", "=", False), ("state", "=", "recue")], limit=1)
        marc = self.env["res.users"].search([("login", "=", "marc")], limit=1)
        if job and marc and not Lieu._by_name(PROPOSED_TAG):
            job.lieu_id = Lieu.create({"name": PROPOSED_TAG, "adresse": job.adresse, "latitude": COORDS["DT-0147"][0],
                                       "longitude": COORDS["DT-0147"][1], "state": "propose", "propose_par_id": marc.id})
        return True

    @api.model
    def _travaux_demo_topup(self):
        """Complète l'historique jusqu'à la fin de la semaine passée (tournées et maintenance déclarées)."""
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        teams = {t.name: t for t in self.env["travaux.equipe"].search([])}
        Demande = self.with_context(travaux_no_live=True, tracking_disable=True, mail_create_nolog=True,
                                    mail_notrack=True, travaux_no_photo_post=True)
        vals = []
        for key, name, _sup, _c, _n, _cap, (dt_n, h_tour, h_maint, h_dt), season in TEAMS:
            team = teams.get(name)
            if not team:
                continue
            weekly = (h_tour + h_maint + h_dt) / 52.0
            for w in range(1, 4):
                wk = monday - timedelta(weeks=w)
                done = sum(self.search([("equipe_id", "=", team.id), ("date_fin", ">=", datetime.combine(wk, time(0))),
                                        ("date_fin", "<", datetime.combine(wk + timedelta(days=7), time(0)))]).mapped("duree_mesuree"))
                missing = weekly * season[(wk.month - 10) % 12] - done
                if missing < 2:
                    continue
                per_day = round(missing / 5 * 2) / 2
                for i in range(5):
                    day = wk + timedelta(days=i)
                    start = datetime.combine(day, time(5, 0))
                    vals.append({"titre": "Tournée %s" % name.lower(), "adresse": "Toute la commune", "type_travail": "tournee",
                                 "equipe_id": team.id, "state": "close", "source": "planning", "date_prevue": day,
                                 "duree_prevue": per_day, "date_debut": start, "date_fin": start + timedelta(hours=per_day),
                                 "date_cloture": start + timedelta(hours=9)})
        if vals:
            Demande.create(vals)
        return True

    @api.model
    def _demo_split(self, rnd, n_off):
        """Répartit des absences fictives par type (congé le plus souvent)."""
        out = {"nb_conge": 0, "nb_absent": 0, "nb_recup": 0, "nb_formation": 0}
        for _i in range(n_off):
            x = rnd.random()
            out["nb_conge" if x < .5 else "nb_absent" if x < .75 else "nb_recup" if x < .9 else "nb_formation"] += 1
        return out

    @api.model
    def _travaux_demo_presences(self):
        # historique créé par une version précédente : on détaille les absences une fois
        rnd0 = random.Random(6220)
        old = self.env["travaux.presence"].search([("effectif_min", "=", 0), ("nb_membres", ">", 0)])
        for line in old:
            line.write(dict(self._demo_split(rnd0, max(0, line.nb_membres - line.nb_presents)),
                            effectif_min=line.equipe_id.effectif_min or 1))
        return self._travaux_demo_presences_create()

    @api.model
    def _travaux_demo_presences_create(self):
        """12 mois d'historique de présences (absences aléatoires ~12 %) si aucun n'existe."""
        Presence = self.env["travaux.presence"]
        today = fields.Date.context_today(self)
        if Presence.search_count([("date", "<", today - timedelta(days=30))]):
            return True
        rnd = random.Random(1330)
        teams = self.env["travaux.equipe"].search([("is_pool", "=", False)])
        vals = []
        day = today - timedelta(days=365)
        while day < today:
            if day.weekday() < 5:
                for t in teams:
                    n = len(t.member_ids)
                    presents = sum(1 for _i in range(n) if rnd.random() > 0.12)
                    vals.append(dict(self._demo_split(rnd, n - presents), date=day, equipe_id=t.id, nb_membres=n,
                                     nb_presents=presents, heures_dispo=round(presents * 7.6, 2), effectif_min=t.effectif_min))
            day += timedelta(days=1)
        existing = {(p.date, p.equipe_id.id) for p in Presence.search([("date", ">=", today - timedelta(days=366))])}
        Presence.create([v for v in vals if (v["date"], v["equipe_id"]) not in existing])
        return True

    @api.model
    def _travaux_demo_chronologie(self):
        for ref in COORDS:
            rec = self.search([("name", "=", ref)], limit=1)
            if rec and rec.state not in ("recue", "precision") and not rec.date_attribution:
                sup = rec.equipe_id.superviseur_id
                rec.with_context(travaux_no_live=True).write({
                    "date_attribution": rec.create_date + timedelta(minutes=18),
                    "attribue_par_id": sup.id or False,
                    "cloture_par_id": sup.id if rec.state == "close" else False,
                })
        return True

    @api.model
    def _travaux_load_demo(self):
        env = self.env
        rnd = random.Random(1330)
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())

        def at(day, hour, add_hours=0.0):
            dt = datetime.combine(day, time(hour, 0)) + timedelta(hours=add_hours)
            return dt - timedelta(hours=2)  # Europe/Brussels → UTC (approx.)

        # Utilisateurs de démonstration (sans mot de passe : à définir par l'administrateur)
        g_sup = env.ref("service_travaux.group_superviseur")
        g_agent = env.ref("service_travaux.group_agent")
        Users = env["res.users"].with_context(no_reset_password=True)

        def user(name, login, group):
            u = Users.search([("login", "=", login)], limit=1)
            if not u:
                u = Users.create({"name": name, "login": login, "email": login + "@rixensart.example",
                                  "group_ids": [(6, 0, [group.id, env.ref("base.group_user").id])]})
            return u
        sups = {key: user(name, login, g_sup) for key, name, login, _srv, _b in CONTREMAITRES}
        brigs = {key: user(b[0], b[1], g_sup) for key, _n, _l, _srv, b in CONTREMAITRES}
        marc_user = user("Marc (Voirie)", "marc", g_agent)
        demo_users = {"Marc": marc_user, "Julie": user("Julie (Espaces verts 1)", "julie", g_agent),
                      "Nadia": user("Nadia (Propreté publique)", "nadia", g_agent)}

        # Équipes
        teams = {}
        for i, (key, name, sup, color, need, cap, _h, _s) in enumerate(TEAMS):
            teams[key] = env["travaux.equipe"].create({
                "name": name, "superviseur_id": sups[sup].id, "brigadier_id": brigs[sup].id, "color": color, "effectif_min": need,
                "capacite_hebdo": cap, "sequence": i + 1})
        pool = env.ref("service_travaux.pool_equipe")

        # Agents
        Employee = env["hr.employee"]
        for name, team, job, status in PEOPLE:
            vals = {"name": name, "fonction": job, "equipe_id": (teams[team] if team else pool).id, "statut_jour": status}
            if name in demo_users:
                vals["user_id"] = demo_users[name].id
            emp = Employee.create(vals)
            if job.startswith("Chef") and team:
                teams[team].chef_id = emp

        # Catégories
        cats = {}
        for i, (name, team, hors) in enumerate(CATEGORIES):
            cats[name] = env["travaux.categorie"].create({
                "name": name, "sequence": i, "equipe_defaut_id": teams[team].id if team else False,
                "hors_competence": hors, "service_competent": "Service Urbanisme – police de l'environnement" if hors else False})

        Demande = self.with_context(travaux_no_live=True, tracking_disable=True, mail_create_nolog=True, mail_notrack=True,
                                    travaux_no_photo_post=True)

        # ------------------------------------------------ historique 12 mois (tableau de bord Direction)
        hist = []
        start = today.replace(day=1)
        months = []
        for back in range(11, -1, -1):
            m = start.month - back
            y = start.year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            months.append(start.replace(year=y, month=m))
        # saisonnalité donnée d'octobre à septembre
        for key, name, _sup, _c, _n, _cap, (dt_n, h_tour, h_maint, h_dt), season in TEAMS:
            team = teams[key]
            norm = sum(season)
            for mi, mstart in enumerate(months):
                factor = season[(mstart.month - 10) % 12] / norm
                # demandes
                span = 28 if mstart != start else max(0, today.day - 8)
                if span <= 0:
                    continue
                n = max(1, round(dt_n * factor * span / 28))
                per = h_dt / dt_n
                for _k in range(n):
                    day = mstart + timedelta(days=rnd.randint(0, span - 1))
                    if day >= today - timedelta(days=8):
                        continue
                    h = max(0.5, round(rnd.gauss(per, per * 0.35) * 2) / 2)
                    hist.append({"titre": rnd.choice(HIST_TITLES[key]),
                                 "adresse": "%s %s" % (rnd.choice(STREETS), rnd.randint(1, 120)),
                                 "type_travail": "demande", "equipe_id": team.id, "state": "close",
                                 "source": rnd.choice(["guichet", "email", "telephone", "citoyen", "police", "app"]),
                                 "date_prevue": day, "date_debut": at(day, 8), "date_fin": at(day, 8, h),
                                 "date_cloture": at(day, 16)})
                # tournées : une par semaine
                if h_tour:
                    tot = h_tour * factor
                    tuesdays = [mstart + timedelta(days=i) for i in range(31)
                                if (mstart + timedelta(days=i)).month == mstart.month
                                and (mstart + timedelta(days=i)).weekday() == 1]
                    for day in tuesdays:
                        if day >= today - timedelta(days=8):
                            continue
                        h = round(tot / len(tuesdays) * 2) / 2
                        hist.append({"titre": "Tournée %s" % name.lower(), "adresse": "Toute la commune",
                                     "type_travail": "tournee", "equipe_id": team.id, "state": "close", "source": "planning",
                                     "date_prevue": day, "date_debut": at(day, 7), "date_fin": at(day, 7, h),
                                     "date_cloture": at(day, 16)})
                if h_maint:
                    thursdays = [mstart + timedelta(days=i) for i in range(31)
                                 if (mstart + timedelta(days=i)).month == mstart.month
                                 and (mstart + timedelta(days=i)).weekday() == 3]
                    for day in thursdays:
                        if day >= today - timedelta(days=8):
                            continue
                        h = round(h_maint * factor / len(thursdays) * 2) / 2
                        hist.append({"titre": "Maintenance planifiée", "adresse": "Bâtiments communaux",
                                     "type_travail": "maintenance", "equipe_id": team.id, "state": "close", "source": "planning",
                                     "date_prevue": day, "date_debut": at(day, 7), "date_fin": at(day, 7, h),
                                     "date_cloture": at(day, 16)})
        for i, v in enumerate(hist):
            if v["type_travail"] == "demande":
                v["name"] = "DT-H%04d" % (i + 1)
        Demande.create(hist)

        # ------------------------------------------------ semaine en cours : tournées et maintenance
        week = []
        for key, day, kind, title, place, h, hd in TASKS:
            d = monday + timedelta(days=day)
            vals = {"titre": title, "adresse": place, "type_travail": kind, "equipe_id": teams[key].id,
                    "source": "planning", "date_prevue": d, "duree_prevue": h, "state": "planif"}
            if hd and d <= today:
                vals.update({"state": "close", "date_debut": at(d, 7), "date_fin": at(d, 7, hd),
                             "date_cloture": at(d, 16)})
            week.append(vals)
        Demande.create(week)

        # ------------------------------------------------ demandes de la maquette (avec photos et fil)
        Partner = env["res.partner"]
        for (ref, titre, adresse, source, cat, state, team, day, urgent, av, ap, msg, consigne, who) in JOBS:
            d = monday + timedelta(days=day) if day is not None else False
            vals = {
                "name": ref, "titre": titre, "adresse": adresse, "source": source, "categorie_id": cats[cat].id,
                "state": state, "equipe_id": teams[team].id if team else False, "date_prevue": d,
                "priorite": "1" if urgent else "0", "description": msg, "consigne": consigne,
                "duree_prevue": 2.0,
                "photo_avant": _img(av) if av else False,
                "photo_apres": _img(ap) if ap else False,
            }
            if who:
                vals["demandeur_id"] = Partner.create({
                    "name": who, "email": who.lower().replace(" ", ".") + "@exemple.be",
                    "phone": "+32 470 00 00 %02d" % rnd.randint(10, 99), "city": "Rixensart"}).id
            if state == "cours":
                vals["date_debut"] = fields.Datetime.now() - timedelta(minutes=40)
            if state in ("valider", "close"):
                h = JOB_HOURS.get(ref, 1.5)
                vals.update({"date_debut": at(d, 8), "date_fin": at(d, 8, h)})
            if state == "close":
                vals["date_cloture"] = at(d, 16)
            rec = Demande.create(vals)
            # Le fil de discussion, comme dans l'app terrain
            rec = rec.with_context(travaux_no_photo_post=False)
            if av:
                rec._post_photo("photo_avant", "AVANT", msg)
            else:
                rec.message_post(body=msg, message_type="comment", subtype_xmlid="mail.mt_comment")
            if team:
                rec.message_post(body="Attribuée à l'équipe %s" % teams[team].name,
                                 message_type="notification", subtype_xmlid="mail.mt_note")
                if consigne:
                    rec.with_user(sups[dict((t[0], t[2]) for t in TEAMS)[team]]).sudo().message_post(
                        body=consigne, message_type="comment", subtype_xmlid="mail.mt_comment")
            if ap:
                rec._post_photo("photo_apres", "APRÈS", "")
            if state == "close":
                rec.message_post(body="Clôturée · demandeur informé", message_type="notification",
                                 subtype_xmlid="mail.mt_note")

        # Numérotation : continuer après la maquette
        seq = env.ref("service_travaux.seq_demande")
        seq.sudo().number_next_actual = 160
        return True
