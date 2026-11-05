from odoo.tests import HttpCase, tagged

from .test_carte import _offline


@tagged("post_install", "-at_install")
class TestCarteTour(HttpCase):
    def test_carte_tour(self):
        admin = self.env.ref("base.user_admin")
        admin.write({"group_ids": [(4, self.env.ref("service_travaux.group_superviseur").id),
                                   (4, self.env.ref("service_travaux.group_agent").id)]})
        for name in ("Espaces verts 2", "Propreté publique"):
            if not self.env["travaux.equipe"].search_count([("name", "=", name)]):
                self.env["travaux.equipe"].create({"name": name})
        with _offline():
            self.env["travaux.carte.demo"].with_user(admin).action_charger()
        self.start_tour("/odoo/action-service_travaux_carte.action_client_carte", "travaux_carte_tour", login="admin")
