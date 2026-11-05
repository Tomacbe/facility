{
    "name": "FACILITY – Carte",
    "summary": "Objets recensés (points, lignes, surfaces) et # lieux sur la carte : dessin et édition, "
    "import SPW / WFS / ODWB / QGIS, propositions depuis l'app terrain",
    "version": "19.0.0.2.1",
    "category": "Services",
    "author": "Tomacbe",
    "website": "https://github.com/Tomacbe/facility",
    "license": "LGPL-3",
    "maintainers": ["Tomacbe"],
    "development_status": "Alpha",
    "depends": ["service_travaux"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "views/objet_views.xml",
        "views/lieu_views.xml",
        "wizards/import_picc_views.xml",
        "wizards/import_geo_views.xml",
        "views/menus.xml",
        "data/types_de_base.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "service_travaux_carte/static/src/carte/*",
            "service_travaux_carte/static/src/terrain/*",
        ],
        "web.assets_tests": [
            "service_travaux_carte/static/tests/tours/*.js",
        ],
    },
    "installable": True,
}
