"""
Route registration module — splits the monolithic app.py into focused route files.
All routes are registered on the main app object to preserve url_for() endpoint names.
"""

from .auth import register_auth_routes
from .visitor import register_visitor_routes
from .fraction import register_fraction_routes
from .admin import register_admin_routes


def register_all_routes(app, db, models):
    register_auth_routes(app, db, models)
    register_visitor_routes(app, db, models)
    register_fraction_routes(app, db, models)
    register_admin_routes(app, db, models)
