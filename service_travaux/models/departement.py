from odoo import models


class HrDepartment(models.Model):
    _inherit = "hr.department"

    def write(self, vals):
        res = super().write(vals)
        if "manager_id" in vals or "parent_id" in vals:
            # un responsable ou un rattachement change : les équipes en dessous changent de responsables
            teams = self.env["travaux.equipe"].sudo().with_context(active_test=False).search([("department_id", "!=", False)])
            teams._compute_responsables()
        return res
