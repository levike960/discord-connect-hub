"""
Shared helpers: timezone, decorators, file utilities, period calculations.
"""

from datetime import datetime, timedelta
import calendar
from zoneinfo import ZoneInfo
from functools import wraps
from flask import flash, redirect, url_for, abort
from flask_login import login_required, current_user


CET = ZoneInfo("Europe/Budapest")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

HU_MONTHS = [
    "", "január", "február", "március", "április", "május", "június",
    "július", "augusztus", "szeptember", "október", "november", "december"
]

HU_DAYS = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]


def now_cet():
    """Return current CET/CEST time as naive datetime (SQLite compatible)."""
    return datetime.now(CET).replace(tzinfo=None)


def period_range(period, offset=0):
    """
    Calculate (start, end, label) for a given period type and offset.
    offset=0 is current period, offset=-1 is previous, offset=1 is next.
    Returns naive datetimes (CET).
    """
    now = now_cet()

    if period == "day":
        base = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=offset)
        start = base
        end = base + timedelta(days=1)
        label = f"{base.year}. {HU_MONTHS[base.month]} {base.day}. ({HU_DAYS[base.weekday()]})"

    elif period == "week":
        # Monday of current week, then offset
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        start = monday + timedelta(weeks=offset)
        end = start + timedelta(weeks=1)
        sun = start + timedelta(days=6)
        label = f"{start.month:02d}.{start.day:02d} – {sun.month:02d}.{sun.day:02d}"

    elif period == "month":
        # Current month + offset
        month = now.month + offset
        year = now.year
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        start = datetime(year, month, 1)
        # End = first of next month
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        label = f"{year}. {HU_MONTHS[month]}"

    elif period == "year":
        year = now.year + offset
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        label = f"{year}"

    else:
        return period_range("day", offset)

    return start, end, label


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
