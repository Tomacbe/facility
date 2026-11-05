#!/bin/bash
# Crée (ou recrée) la base "facility_test" remplie de données de test :
# FACILITY + données de démonstration + Carte + démo « maison communale ».
set -e
BASE=${1:-facility_test}
C=odoo19_web_1
# identifiants de la base lus dans le conteneur Odoo lui-même
DBARGS='--db_host="${HOST:-db}" --db_user="${USER:-odoo}" --db_password="$PASSWORD"'
ODOO="odoo $DBARGS"
SHELL_ODOO="odoo shell $DBARGS"

echo "== Suppression de l'ancienne base $BASE (si elle existe)"
docker exec odoo19_db_1 dropdb -U odoo --if-exists --force "$BASE"

echo "== Création de $BASE et installation des modules (quelques minutes)"
docker exec $C sh -c "$ODOO -d $BASE -i service_travaux_demo,service_travaux_carte --stop-after-init --no-http"

echo "== Admin superviseur + démo maison communale"
docker exec -i $C sh -c "$SHELL_ODOO -d $BASE --no-http" <<'EOF'
admin = env.ref("base.user_admin")
admin.write({"group_ids": [(4, env.ref("service_travaux.group_superviseur").id), (4, env.ref("service_travaux.group_agent").id)]})
env["travaux.carte.demo"].with_user(admin).action_charger()
env.cr.commit()
print("OK : %s objets, %s équipes, %s demandes" % (env["travaux.objet"].search_count([]), env["travaux.equipe"].search_count([]), env["travaux.demande"].search_count([])))
EOF

docker restart $C
echo "== Prêt : http://localhost:8069/web?db=$BASE  (login admin / mot de passe admin)"
