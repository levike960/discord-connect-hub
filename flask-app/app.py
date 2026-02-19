"""
Flask Web Application with Discord OAuth2 Authentication
"""

import os
import requests
from datetime import datetime, date, timedelta
from functools import wraps
from flask import (
    Flask, render_template, redirect, url_for, session,
    request, flash, abort, send_from_directory, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from flask_wtf import CSRFProtect
from werkzeug.utils import secure_filename
from urllib.parse import quote_plus

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB

DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.environ.get(
    "DISCORD_REDIRECT_URI", "http://localhost:5000/callback"
)
DISCORD_API_BASE = "https://discord.com/api/v10"

ADMIN_DISCORD_IDS: list[str] = [
    # "123456789012345678",
]

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "visitor"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ---------------------------------------------------------------------------
# Models (defined inline to keep single-import simplicity)
# ---------------------------------------------------------------------------

from models import define_models

models = define_models(db, app)
User = models["User"]
Rating = models["Rating"]
WorkLog = models["WorkLog"]
Due = models["Due"]
Advertisement = models["Advertisement"]
DeliveryCompany = models["DeliveryCompany"]
DeliveryMessage = models["DeliveryMessage"]
Contract = models["Contract"]


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/login")
def login():
    scope = "identify"
    redirect_uri = quote_plus(DISCORD_REDIRECT_URI)
    return redirect(
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={scope}"
    )


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        flash("Authentication failed.", "danger")
        return redirect(url_for("visitor"))

    token_res = requests.post(
        f"{DISCORD_API_BASE}/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    if token_res.status_code != 200:
        flash("Could not obtain access token.", "danger")
        return redirect(url_for("visitor"))

    access_token = token_res.json().get("access_token")
    user_res = requests.get(
        f"{DISCORD_API_BASE}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if user_res.status_code != 200:
        flash("Could not fetch user info.", "danger")
        return redirect(url_for("visitor"))

    data = user_res.json()
    discord_id = data["id"]
    username = data["username"]
    avatar = data.get("avatar")

    user = User.query.filter_by(discord_id=discord_id).first()
    if user is None:
        user = User(
            discord_id=discord_id, username=username, avatar=avatar,
            is_admin=discord_id in ADMIN_DISCORD_IDS,
        )
        db.session.add(user)
    else:
        user.username = username
        user.avatar = avatar
        if discord_id in ADMIN_DISCORD_IDS:
            user.is_admin = True

    db.session.commit()
    login_user(user, remember=True)
    return redirect(url_for("visitor"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("visitor"))


# ---------------------------------------------------------------------------
# Routes — Visitor & Profile
# ---------------------------------------------------------------------------

@app.route("/")
def visitor():
    workers = User.query.filter_by(has_fraction_permission=True).all()
    workers.sort(key=lambda w: w.average_rating, reverse=True)
    return render_template("visitor.html", workers=workers)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        new_nick = request.form.get("nickname", "").strip()
        current_user.nickname = new_nick if new_nick else None

        file = request.files.get("avatar")
        if file and file.filename and allowed_file(file.filename):
            filename = f"avatar_{current_user.discord_id}.png"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(filepath)
        elif file and file.filename:
            flash("Invalid file type. Allowed: png, jpg, jpeg, gif, webp", "warning")

        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    return render_template("profile.html")


# ---------------------------------------------------------------------------
# Routes — Ratings
# ---------------------------------------------------------------------------

@app.route("/rate/<int:target_id>", methods=["POST"])
@login_required
def rate_worker(target_id):
    target = db.session.get(User, target_id)
    if not target or not target.has_fraction_permission:
        flash("Invalid worker.", "danger")
        return redirect(url_for("visitor"))
    if target.id == current_user.id:
        flash("You cannot rate yourself.", "warning")
        return redirect(url_for("visitor"))

    stars = request.form.get("stars", type=int)
    if not stars or stars < 1 or stars > 5:
        flash("Please select a rating between 1 and 5.", "warning")
        return redirect(url_for("visitor"))

    existing = Rating.query.filter_by(
        reviewer_user_id=current_user.id, target_user_id=target_id
    ).first()
    if existing:
        existing.stars = stars
    else:
        db.session.add(Rating(
            reviewer_user_id=current_user.id,
            target_user_id=target_id, stars=stars,
        ))

    db.session.commit()
    flash(f"Rated {target.display_name} {stars} star(s).", "success")
    return redirect(url_for("visitor"))


# ---------------------------------------------------------------------------
# Routes — Faction
# ---------------------------------------------------------------------------

@app.route("/fraction")
@fraction_required
def fraction():
    return render_template("fraction.html")


@app.route("/fraction/clock", methods=["GET", "POST"])
@fraction_required
def fraction_clock():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "clock_in":
            if current_user.is_clocked_in:
                flash("Already clocked in.", "warning")
            else:
                db.session.add(WorkLog(user_id=current_user.id))
                db.session.commit()
                flash("Clocked in!", "success")
        elif action == "clock_out":
            log = current_user.active_work_log
            if log:
                log.clock_out = datetime.utcnow()
                db.session.commit()
                flash(f"Clocked out. Duration: {log.duration_formatted}", "success")
            else:
                flash("You are not clocked in.", "warning")
        return redirect(url_for("fraction_clock"))

    return render_template("fraction_clock.html")


@app.route("/fraction/workhours")
@fraction_required
def fraction_workhours():
    period = request.args.get("period", "day")
    now = datetime.utcnow()

    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    logs = WorkLog.query.filter(
        WorkLog.user_id == current_user.id,
        WorkLog.clock_in >= start
    ).order_by(WorkLog.clock_in.desc()).all()

    total_seconds = sum(l.duration_seconds for l in logs)
    h, remainder = divmod(int(total_seconds), 3600)
    m, s = divmod(remainder, 60)
    total_formatted = f"{h}h {m}m"

    return render_template("fraction_workhours.html",
                           logs=logs, period=period, total_formatted=total_formatted)


@app.route("/fraction/dues")
@fraction_required
def fraction_dues():
    dues = Due.query.order_by(Due.due_date.asc()).all()
    return render_template("fraction_dues.html", dues=dues, today=date.today())


@app.route("/fraction/calculator")
@fraction_required
def fraction_calculator():
    return render_template("fraction_calculator.html")


@app.route("/fraction/ads")
@fraction_required
def fraction_ads():
    ads = Advertisement.query.order_by(Advertisement.created_at.desc()).all()
    return render_template("fraction_ads.html", ads=ads)


@app.route("/fraction/deliveries")
@fraction_required
def fraction_deliveries():
    companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
    return render_template("fraction_deliveries.html", companies=companies)


@app.route("/fraction/deliveries/<int:company_id>", methods=["GET", "POST"])
@fraction_required
def fraction_delivery_wall(company_id):
    company = db.session.get(DeliveryCompany, company_id)
    if not company:
        flash("Company not found.", "danger")
        return redirect(url_for("fraction_deliveries"))

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content and len(content) <= 1000:
            db.session.add(DeliveryMessage(
                company_id=company_id, user_id=current_user.id, content=content
            ))
            db.session.commit()
            flash("Message posted.", "success")
        else:
            flash("Message cannot be empty or longer than 1000 characters.", "warning")
        return redirect(url_for("fraction_delivery_wall", company_id=company_id))

    messages = company.messages.all()
    return render_template("fraction_delivery_wall.html", company=company, messages=messages)


@app.route("/fraction/brewery")
@fraction_required
def fraction_brewery():
    return render_template("fraction_brewery.html")


@app.route("/fraction/contracts")
@fraction_required
def fraction_contracts():
    contracts = Contract.query.order_by(Contract.created_at.desc()).all()
    return render_template("fraction_contracts.html", contracts=contracts)


# ---------------------------------------------------------------------------
# Routes — Admin
# ---------------------------------------------------------------------------

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        form_type = request.form.get("form_type")

        # --- User permission management ---
        if form_type == "user_perm":
            user_id = request.form.get("user_id", type=int)
            action = request.form.get("action")
            target = db.session.get(User, user_id)
            if target:
                if action == "grant":
                    target.has_fraction_permission = True
                elif action == "revoke":
                    target.has_fraction_permission = False
                db.session.commit()
                flash(f"Updated permissions for {target.display_name}.", "success")

        # --- Add Due ---
        elif form_type == "add_due":
            name = request.form.get("due_name", "").strip()
            amount = request.form.get("due_amount", type=float)
            due_date_str = request.form.get("due_date", "")
            if name and amount is not None and due_date_str:
                try:
                    due_date = date.fromisoformat(due_date_str)
                    db.session.add(Due(name=name, amount=amount, due_date=due_date,
                                       created_by=current_user.id))
                    db.session.commit()
                    flash("Due added.", "success")
                except ValueError:
                    flash("Invalid date format.", "danger")

        # --- Delete Due ---
        elif form_type == "delete_due":
            due_id = request.form.get("due_id", type=int)
            due = db.session.get(Due, due_id)
            if due:
                db.session.delete(due)
                db.session.commit()
                flash("Due deleted.", "success")

        # --- Add Advertisement ---
        elif form_type == "add_ad":
            title = request.form.get("ad_title", "").strip()
            content = request.form.get("ad_content", "").strip()
            if title and content:
                db.session.add(Advertisement(title=title, content=content,
                                              created_by=current_user.id))
                db.session.commit()
                flash("Advertisement added.", "success")

        # --- Delete Advertisement ---
        elif form_type == "delete_ad":
            ad_id = request.form.get("ad_id", type=int)
            ad = db.session.get(Advertisement, ad_id)
            if ad:
                db.session.delete(ad)
                db.session.commit()
                flash("Advertisement deleted.", "success")

        # --- Add Delivery Company ---
        elif form_type == "add_company":
            name = request.form.get("company_name", "").strip()
            if name:
                db.session.add(DeliveryCompany(name=name))
                db.session.commit()
                flash("Company added.", "success")

        # --- Delete Delivery Company ---
        elif form_type == "delete_company":
            cid = request.form.get("company_id", type=int)
            company = db.session.get(DeliveryCompany, cid)
            if company:
                DeliveryMessage.query.filter_by(company_id=cid).delete()
                db.session.delete(company)
                db.session.commit()
                flash("Company and its messages deleted.", "success")

        # --- Add Contract ---
        elif form_type == "add_contract":
            cname = request.form.get("contract_company", "").strip()
            desc = request.form.get("contract_desc", "").strip()
            image_path = None
            file = request.files.get("contract_image")
            if file and file.filename and allowed_file(file.filename):
                fname = secure_filename(f"contract_{datetime.utcnow().timestamp()}_{file.filename}")
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
                file.save(filepath)
                image_path = f"uploads/{fname}"
            if cname and desc:
                db.session.add(Contract(company_name=cname, description=desc,
                                         image_path=image_path, created_by=current_user.id))
                db.session.commit()
                flash("Contract added.", "success")

        # --- Delete Contract ---
        elif form_type == "delete_contract":
            cid = request.form.get("contract_id", type=int)
            contract = db.session.get(Contract, cid)
            if contract:
                db.session.delete(contract)
                db.session.commit()
                flash("Contract deleted.", "success")

        # --- Edit Work Hours ---
        elif form_type == "edit_worklog":
            log_id = request.form.get("log_id", type=int)
            log = db.session.get(WorkLog, log_id)
            if log:
                new_in = request.form.get("new_clock_in", "")
                new_out = request.form.get("new_clock_out", "")
                try:
                    if new_in:
                        log.clock_in = datetime.fromisoformat(new_in)
                    if new_out:
                        log.clock_out = datetime.fromisoformat(new_out)
                    db.session.commit()
                    flash("Work log updated.", "success")
                except ValueError:
                    flash("Invalid datetime format.", "danger")

        return redirect(url_for("admin"))

    users = User.query.order_by(User.username).all()
    dues = Due.query.order_by(Due.due_date.asc()).all()
    ads = Advertisement.query.order_by(Advertisement.created_at.desc()).all()
    companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
    contracts = Contract.query.order_by(Contract.created_at.desc()).all()
    work_logs = WorkLog.query.order_by(WorkLog.clock_in.desc()).limit(100).all()

    return render_template("admin.html", users=users, dues=dues, ads=ads,
                           companies=companies, contracts=contracts, work_logs=work_logs)


# ---------------------------------------------------------------------------
# Database Init & Run
# ---------------------------------------------------------------------------

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
