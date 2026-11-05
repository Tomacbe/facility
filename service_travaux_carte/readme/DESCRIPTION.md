Ajoute une carte à FACILITY pour situer et éditer le patrimoine suivi par le service technique.

- **Objets recensés** : chaque # objet a une géométrie, point (#Avaloir, #Banc), ligne (#Haie, longueur calculée) ou surface (#Pelouse, #Massif, surface en m²). Les objets sont rangés automatiquement dans le # lieu ou la zone de tournée qui les contient.
- **# lieux** : un # lieu sans géométrie reçoit un contour ou un point depuis la carte.
- **Outils de dessin** : ajouter, modifier la forme (sommets), déplacer, supprimer. La suppression est réservée au superviseur ; un objet ou un # lieu déjà utilisé est archivé plutôt que supprimé.
- **Fonds de plan** : OpenStreetMap en vue éloignée, PICC (Service public de Wallonie) à partir du zoom 17, photo aérienne à la demande.
- **Import** : catalogue ArcGIS REST du SPW, service WFS, ODWB (Open Data Wallonie-Bruxelles) et fichiers QGIS (GeoJSON, GeoPackage, Shapefile zippé ; Lambert 72 et Lambert 2008 convertis). Les entités sont classées par valeur d'un champ vers un # objet ou un # lieu. Un nouvel import met à jour sans créer de doublon et n'écrase pas une forme corrigée à la main.
- **App terrain** : bouton « Carte » ; l'agent propose un objet (point, ligne, surface) et peut retirer sa proposition. Le superviseur le fige ou le refuse depuis « À figer ».
- Une demande peut viser un objet précis.
