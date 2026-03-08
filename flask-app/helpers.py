"""
Shared helpers: timezone, decorators, file utilities.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from functools import wraps
from flask import flash, redirect, url_for, abort
from flask_login import login_required, current_user


CET = ZoneInfo("Europe/Budapest")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def now_cet():
    """Return current CET/CEST time as naive datetime (SQLite compatible)."""
    return datetime.now(CET).replace(tzinfo=None)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("You do not have admin access.", "danger")
            return redirect(url_for("visitor"))
        return f(*args, **kwargs)
    return decorated


def fraction_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not (current_user.has_fraction_permission or current_user.is_admin):
            abort(403)
        return f(*args, **kwargs)
    return decorated
