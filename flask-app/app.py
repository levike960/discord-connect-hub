"""
Flask Web Application — Simplified entry point with modular routes.
"""

import os
import logging
import traceback
import threading
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, flash, redirect, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from flask_login import current_user
from helpers import now_cet
import requests as http_requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB

# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "visitor"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = os.path.join(app.root_path, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "error.log"),
    maxBytes=1_000_000,  # 1 MB
    backupCount=5
)
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s in %(module)s (%(pathname)s:%(lineno)d):\n%(message)s\n"
))
app.logger.addHandler(file_handler)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

from models import define_models

models = define_models(db, app)
User = models["User"]


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Context Processors & Error Handlers
# ---------------------------------------------------------------------------

@app.context_processor
def inject_now():
    return {"now": now_cet}


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


@app.errorhandler(500)
def internal_error(e):
    app.logger.error("500 Internal Server Error\nURL: %s\nMethod: %s\n%s",
                     request.url, request.method, traceback.format_exc())
    flash("Hiba történt a kérés feldolgozása közben. A hiba naplózásra került.", "danger")
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect("/")


# ---------------------------------------------------------------------------
# Discord Webhook Request Logging
# ---------------------------------------------------------------------------

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1484299458194837635/_EAr7dG4-1C1xAQ4UHFis1FCP_k-aIbhQgq52_eBn12ojI40W6aANw-2Wf46uSWO9VXd"


def _send_discord_log(method, url, status, user_info, ip):
    """Send request log to Discord webhook in a background thread."""
    try:
        color = 0x2ecc71 if status < 400 else (0xe67e22 if status < 500 else 0xe74c3c)
        embed = {
            "title": f"{method} → {status}",
            "description": f"**URL:** `{url}`",
            "color": color,
            "fields": [
                {"name": "Felhasználó", "value": user_info, "inline": True},
                {"name": "IP", "value": ip, "inline": True},
            ],
        }
        http_requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
    except Exception:
        pass


@app.after_request
def log_request_to_discord(response):
    if request.path.startswith("/static"):
        return response
    method = request.method
    if method not in ("GET", "POST"):
        return response
    url = request.url
    status = response.status_code
    ip = request.remote_addr or "unknown"
    if hasattr(current_user, "id") and current_user.is_authenticated:
        user_info = f"{current_user.username} (ID: {current_user.id})"
    else:
        user_info = "Vendég"
    threading.Thread(target=_send_discord_log, args=(method, url, status, user_info, ip), daemon=True).start()
    return response


# ---------------------------------------------------------------------------
# Register Routes (from modular route files)
# ---------------------------------------------------------------------------

from routes import register_all_routes
register_all_routes(app, db, models)


# ---------------------------------------------------------------------------
# Database Auto-Migration & Startup
# ---------------------------------------------------------------------------

def _auto_migrate_db():
    """Automatically add missing tables and columns on startup."""
    from sqlalchemy import inspect, text

    db.create_all()

    inspector = inspect(db.engine)
    existing_tables = inspector.get_table_names()

    # --- Special migration: make ingredient_id nullable & add sub_menu_item_id ---
    try:
        if "menu_item_ingredients" in existing_tables:
            cols_info = inspector.get_columns("menu_item_ingredients")
            col_names = [c["name"] for c in cols_info]
            needs_rebuild = False
            for ci in cols_info:
                if ci["name"] == "ingredient_id" and not ci.get("nullable", True):
                    needs_rebuild = True
                    break
            if "sub_menu_item_id" not in col_names:
                needs_rebuild = True
            if needs_rebuild:
                db.session.execute(text(
                    "CREATE TABLE _mii_backup AS SELECT * FROM menu_item_ingredients"
                ))
                db.session.execute(text("DROP TABLE menu_item_ingredients"))
                db.session.commit()
                db.create_all()
                existing_cols = ", ".join(c for c in col_names if c in ("id", "menu_item_id", "ingredient_id", "quantity"))
                db.session.execute(text(
                    f"INSERT INTO menu_item_ingredients ({existing_cols}) SELECT {existing_cols} FROM _mii_backup"
                ))
                db.session.execute(text("DROP TABLE _mii_backup"))
                db.session.commit()
                print("  [AUTO-MIGRATE] Rebuilt menu_item_ingredients")
    except Exception as e:
        db.session.rollback()
        print(f"  [AUTO-MIGRATE] menu_item_ingredients migration error: {e}")

    # Refresh inspector after possible rebuild
    inspector = inspect(db.engine)
    existing_tables = inspector.get_table_names()

    for table_name, table in db.metadata.tables.items():
        if table_name not in existing_tables:
            continue

        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in existing_columns:
                col_type = column.type.compile(db.engine.dialect)
                nullable = "" if column.nullable else " NOT NULL"
                default = ""
                if column.default is not None:
                    val = column.default.arg
                    if callable(val):
                        val = val(None)
                    if isinstance(val, bool):
                        default = f" DEFAULT {1 if val else 0}"
                    elif isinstance(val, (int, float)):
                        default = f" DEFAULT {val}"
                    elif isinstance(val, str):
                        default = f" DEFAULT '{val}'"
                if nullable == " NOT NULL" and not default:
                    nullable = ""
                sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}{nullable}{default}'
                try:
                    db.session.execute(text(sql))
                    db.session.commit()
                    print(f"  [AUTO-MIGRATE] Added column: {table_name}.{column.name}")
                except Exception as e:
                    db.session.rollback()
                    print(f"  [AUTO-MIGRATE] Skipped {table_name}.{column.name}: {e}")


with app.app_context():
    _auto_migrate_db()
    print("[AUTO-MIGRATE] Database check complete.")

if __name__ == "__main__":
    app.run(debug=True)
