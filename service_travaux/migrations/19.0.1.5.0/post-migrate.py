"""Les modèles de mail ne portent plus un nom de commune en dur : ils prennent celui de la société."""


def migrate(cr, version):
    cr.execute("""
        UPDATE mail_template
           SET body_html = replace(body_html::text, 'Commune de Rixensart · Service technique',
                                   '<t t-out=\\"object.env.company.name\\"/> · Service technique')::jsonb
         WHERE body_html::text LIKE '%Commune de Rixensart · Service technique%'
    """)
