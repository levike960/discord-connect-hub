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
Rank = models["Rank"]
Ingredient = models["Ingredient"]
MenuItem = models["MenuItem"]
MenuItemIngredient = models["MenuItemIngredient"]
CompanyDiscount = models["CompanyDiscount"]
Partner = models["Partner"]
PartnerImage = models["PartnerImage"]


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
    partners = Partner.query.order_by(Partner.sort_order).all()
    return render_template("visitor.html", workers=workers, partners=partners)


@app.route("/partner/<slug>")
def partner_detail(slug):
    partner = Partner.query.filter_by(slug=slug).first_or_404()
    return render_template("partner_detail.html", partner=partner)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        new_nick = request.form.get("nickname", "").strip()
        current_user.nickname = new_nick if new_nick else None

        new_ingame = request.form.get("ingame_name", "").strip()
        current_user.ingame_name = new_ingame if new_ingame else None

        new_phone = request.form.get("phone", "").strip()
        current_user.phone = new_phone if new_phone else None

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


@app.route("/fraction/members")
@fraction_required
def fraction_members():
    members = User.query.filter_by(has_fraction_permission=True).all()
    ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
    # Sort members by rank order (no rank = last)
    members.sort(key=lambda u: (u.rank.sort_order if u.rank else 9999, u.display_name))
    return render_template("fraction_members.html", members=members, ranks=ranks)


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
    step = request.args.get("step", "categories")
    cat = request.args.get("cat", "")
    item_id = request.args.get("item_id", type=int)
    mode = request.args.get("mode", "")
    company_id = request.args.get("company_id", type=int)

    # Handle cart in session
    if "pos_cart" not in session:
        session["pos_cart"] = []

    # Remove item from cart
    remove_idx = request.args.get("remove", type=int)
    if remove_idx is not None:
        cart = session.get("pos_cart", [])
        if 0 <= remove_idx < len(cart):
            cart.pop(remove_idx)
            session["pos_cart"] = cart
        return redirect(url_for("fraction_calculator"))

    # Clear cart
    if request.args.get("clear"):
        session["pos_cart"] = []
        return redirect(url_for("fraction_calculator"))

    # Build cart display data
    cart = []
    cart_total = 0.0
    for c in session.get("pos_cart", []):
        mi = db.session.get(MenuItem, c["id"])
        if mi:
            line_total = mi.price * c["qty"]
            cart.append({"name": mi.name, "qty": c["qty"], "line_total": line_total,
                         "category": mi.category, "item_id": mi.id})
            cart_total += line_total

    ctx = dict(step=step, cat=cat, cart=cart, cart_total=cart_total, mode=mode)

    if step == "items":
        ctx["items"] = MenuItem.query.filter_by(category=cat).order_by(MenuItem.name).all()
    elif step == "qty":
        ctx["selected_item"] = db.session.get(MenuItem, item_id)
        if not ctx["selected_item"]:
            return redirect(url_for("fraction_calculator"))
    elif step == "finish":
        if mode == "discount":
            # Get companies that have discounts
            disc_company_ids = db.session.query(CompanyDiscount.company_id).distinct().all()
            disc_company_ids = [x[0] for x in disc_company_ids]
            ctx["discount_companies"] = DeliveryCompany.query.filter(
                DeliveryCompany.id.in_(disc_company_ids)).all() if disc_company_ids else []
            ctx["selected_company"] = None
            ctx["discount_details"] = []
            ctx["discount_total"] = cart_total
            if company_id:
                company = db.session.get(DeliveryCompany, company_id)
                ctx["selected_company"] = company
                if company:
                    discounts = {d.category: d.discount_percent
                                 for d in CompanyDiscount.query.filter_by(company_id=company_id).all()}
                    details = []
                    disc_total = 0.0
                    for item in cart:
                        pct = discounts.get(item["category"], 0)
                        discounted = item["line_total"] * (1 - pct / 100)
                        details.append({
                            "name": item["name"], "qty": item["qty"],
                            "original": item["line_total"], "discount_pct": pct,
                            "discounted": discounted
                        })
                        disc_total += discounted
                    ctx["discount_details"] = details
                    ctx["discount_total"] = disc_total
        elif mode == "production":
            # Aggregate ingredients and time
            ingredient_totals = {}
            total_time = 0
            total_cost = 0.0
            for item in cart:
                mi = db.session.get(MenuItem, item["item_id"])
                if mi:
                    total_time += mi.production_time_seconds * item["qty"]
                    for ri in mi.recipe_items.all():
                        key = ri.ingredient_id
                        if key not in ingredient_totals:
                            ingredient_totals[key] = {
                                "name": ri.ingredient.name,
                                "unit": ri.ingredient.unit,
                                "unit_price": ri.ingredient.price_per_unit,
                                "qty": 0.0
                            }
                        ingredient_totals[key]["qty"] += ri.quantity * item["qty"]
            for v in ingredient_totals.values():
                v["cost"] = v["qty"] * v["unit_price"]
                total_cost += v["cost"]
            ctx["production_ingredients"] = list(ingredient_totals.values())
            ctx["production_time"] = total_time
            ctx["production_cost"] = total_cost

    return render_template("fraction_calculator.html", **ctx)


@app.route("/fraction/calculator/add", methods=["POST"])
@fraction_required
def fraction_calculator_add():
    item_id = request.form.get("item_id", type=int)
    qty = request.form.get("qty", type=int, default=1)
    if item_id and qty and qty > 0:
        cart = session.get("pos_cart", [])
        # Check if item already in cart
        found = False
        for c in cart:
            if c["id"] == item_id:
                c["qty"] += qty
                found = True
                break
        if not found:
            cart.append({"id": item_id, "qty": qty})
        session["pos_cart"] = cart
    return redirect(url_for("fraction_calculator"))


@app.route("/fraction/calculator/record_due", methods=["POST"])
@fraction_required
def fraction_calculator_record_due():
    """Record discounted total as a Due for the selected company."""
    company_id = request.form.get("company_id", type=int)
    discount_total = request.form.get("discount_total", type=float)
    if company_id and discount_total is not None:
        company = db.session.get(DeliveryCompany, company_id)
        if company:
            due = Due(
                name=f"{company.name} — POS kedvezmény",
                amount=discount_total,
                due_date=date.today() + timedelta(days=30),
                company_id=company_id,
                created_by=current_user.id,
            )
            db.session.add(due)
            db.session.commit()
            session["pos_cart"] = []
            flash(f"Tartozás felírva: {company.name} — {'%.0f' % discount_total} Ft", "success")
        else:
            flash("Cég nem található.", "danger")
    return redirect(url_for("fraction_calculator"))


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

        # --- Settle Due (mark as paid) ---
        elif form_type == "settle_due":
            due_id = request.form.get("due_id", type=int)
            due = db.session.get(Due, due_id)
            if due:
                due.is_paid = True
                due.paid_at = datetime.utcnow()
                db.session.commit()
                flash("Tartozás rendezve.", "success")

        # --- Settle all dues for a company ---
        elif form_type == "settle_company_dues":
            company_id = request.form.get("company_id", type=int)
            if company_id:
                unpaid = Due.query.filter_by(company_id=company_id, is_paid=False).all()
                for due in unpaid:
                    due.is_paid = True
                    due.paid_at = datetime.utcnow()
                db.session.commit()
                flash(f"{len(unpaid)} tartozás rendezve.", "success")

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

        # --- Add Ingredient ---
        elif form_type == "add_ingredient":
            name = request.form.get("ing_name", "").strip()
            unit = request.form.get("ing_unit", "db").strip()
            price = request.form.get("ing_price", type=float)
            if name and price is not None:
                db.session.add(Ingredient(name=name, unit=unit, price_per_unit=price))
                db.session.commit()
                flash("Ingredient added.", "success")

        # --- Delete Ingredient ---
        elif form_type == "delete_ingredient":
            ing_id = request.form.get("ing_id", type=int)
            ing = db.session.get(Ingredient, ing_id)
            if ing:
                db.session.delete(ing)
                db.session.commit()
                flash("Ingredient deleted.", "success")

        # --- Add Menu Item ---
        elif form_type == "add_menu_item":
            mi_name = request.form.get("mi_name", "").strip()
            mi_cat = request.form.get("mi_category", "food")
            mi_price = request.form.get("mi_price", type=float, default=0)
            mi_time = request.form.get("mi_time", type=int, default=0)
            mi_cost = request.form.get("mi_cost_override", type=float)
            image_path = None
            file = request.files.get("mi_image")
            if file and file.filename and allowed_file(file.filename):
                fname = secure_filename(f"menu_{datetime.utcnow().timestamp()}_{file.filename}")
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
                file.save(filepath)
                image_path = f"uploads/{fname}"
            if mi_name:
                item = MenuItem(name=mi_name, category=mi_cat, price=mi_price,
                                production_time_seconds=mi_time,
                                production_cost=mi_cost if mi_cost is not None else 0,
                                image_path=image_path, created_by=current_user.id)
                db.session.add(item)
                db.session.commit()
                flash("Menu item added.", "success")

        # --- Delete Menu Item ---
        elif form_type == "delete_menu_item":
            mi_id = request.form.get("mi_id", type=int)
            mi = db.session.get(MenuItem, mi_id)
            if mi:
                db.session.delete(mi)
                db.session.commit()
                flash("Menu item deleted.", "success")

        # --- Add Recipe Item ---
        elif form_type == "add_recipe_item":
            mi_id = request.form.get("mi_id", type=int)
            ing_id = request.form.get("ri_ingredient_id", type=int)
            qty = request.form.get("ri_quantity", type=float, default=1)
            if mi_id and ing_id:
                db.session.add(MenuItemIngredient(
                    menu_item_id=mi_id, ingredient_id=ing_id, quantity=qty))
                db.session.commit()
                # Auto-recalculate production cost
                mi = db.session.get(MenuItem, mi_id)
                if mi:
                    mi.production_cost = mi.calculated_cost
                    db.session.commit()
                flash("Recipe ingredient added, cost recalculated.", "success")

        # --- Remove Recipe Item ---
        elif form_type == "remove_recipe_item":
            ri_id = request.form.get("ri_id", type=int)
            ri = db.session.get(MenuItemIngredient, ri_id)
            if ri:
                mi_id = ri.menu_item_id
                db.session.delete(ri)
                db.session.commit()
                # Auto-recalculate production cost
                mi = db.session.get(MenuItem, mi_id)
                if mi:
                    mi.production_cost = mi.calculated_cost
                    db.session.commit()
                flash("Recipe ingredient removed, cost recalculated.", "success")

        # --- Recalculate Cost ---
        elif form_type == "recalc_cost":
            mi_id = request.form.get("mi_id", type=int)
            mi = db.session.get(MenuItem, mi_id)
            if mi:
                mi.production_cost = mi.calculated_cost
                db.session.commit()
                flash(f"Production cost recalculated: {mi.production_cost} Ft", "success")

        # --- Add Discount ---
        elif form_type == "add_discount":
            comp_id = request.form.get("disc_company_id", type=int)
            cat = request.form.get("disc_category", "")
            pct = request.form.get("disc_percent", type=float, default=0)
            if comp_id and cat:
                existing = CompanyDiscount.query.filter_by(
                    company_id=comp_id, category=cat).first()
                if existing:
                    existing.discount_percent = pct
                else:
                    db.session.add(CompanyDiscount(
                        company_id=comp_id, category=cat, discount_percent=pct))
                db.session.commit()
                flash("Discount saved.", "success")

        # --- Delete Discount ---
        elif form_type == "delete_discount":
            disc_id = request.form.get("disc_id", type=int)
            disc = db.session.get(CompanyDiscount, disc_id)
            if disc:
                db.session.delete(disc)
                db.session.commit()
                flash("Discount deleted.", "success")

        # --- Add Rank ---
        elif form_type == "add_rank":
            rname = request.form.get("rank_name", "").strip()
            rcolor = request.form.get("rank_color", "").strip() or None
            if rname:
                max_order = db.session.query(db.func.max(Rank.sort_order)).scalar() or 0
                db.session.add(Rank(name=rname, sort_order=max_order + 1, color=rcolor))
                db.session.commit()
                flash("Rank added.", "success")

        # --- Delete Rank ---
        elif form_type == "delete_rank":
            rank_id = request.form.get("rank_id", type=int)
            rank = db.session.get(Rank, rank_id)
            if rank:
                # Unassign users with this rank
                for u in User.query.filter_by(rank_id=rank_id).all():
                    u.rank_id = None
                db.session.delete(rank)
                db.session.commit()
                flash("Rank deleted.", "success")

        # --- Move Rank Up/Down ---
        elif form_type == "move_rank":
            rank_id = request.form.get("rank_id", type=int)
            direction = request.form.get("direction")
            rank = db.session.get(Rank, rank_id)
            if rank:
                all_ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
                idx = next((i for i, r in enumerate(all_ranks) if r.id == rank_id), None)
                if idx is not None:
                    if direction == "up" and idx > 0:
                        all_ranks[idx].sort_order, all_ranks[idx-1].sort_order = \
                            all_ranks[idx-1].sort_order, all_ranks[idx].sort_order
                    elif direction == "down" and idx < len(all_ranks) - 1:
                        all_ranks[idx].sort_order, all_ranks[idx+1].sort_order = \
                            all_ranks[idx+1].sort_order, all_ranks[idx].sort_order
                    db.session.commit()

        # --- Edit Member (admin edits user profile) ---
        elif form_type == "edit_member":
            user_id = request.form.get("user_id", type=int)
            target = db.session.get(User, user_id)
            if target:
                new_ingame = request.form.get("ingame_name", "").strip()
                target.ingame_name = new_ingame if new_ingame else None
                new_phone = request.form.get("phone", "").strip()
                target.phone = new_phone if new_phone else None
                rank_id = request.form.get("rank_id", type=int)
                target.rank_id = rank_id if rank_id else None
                db.session.commit()
                flash(f"Updated {target.display_name}.", "success")

        # --- Add Partner ---
        elif form_type == "add_partner":
            pname = request.form.get("partner_name", "").strip()
            pslug = request.form.get("partner_slug", "").strip().lower().replace(" ", "-")
            pshort = request.form.get("partner_short_desc", "").strip()
            pdesc = request.form.get("partner_description", "").strip()
            pprice = request.form.get("partner_price_list", "").strip()
            logo_path = None
            file = request.files.get("partner_logo")
            if file and file.filename and allowed_file(file.filename):
                fname = secure_filename(f"partner_{datetime.utcnow().timestamp()}_{file.filename}")
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
                file.save(filepath)
                logo_path = f"uploads/{fname}"
            if pname and pslug:
                max_order = db.session.query(db.func.max(Partner.sort_order)).scalar() or 0
                db.session.add(Partner(
                    name=pname, slug=pslug, short_description=pshort or None,
                    description=pdesc or None, price_list=pprice or None,
                    logo_path=logo_path, sort_order=max_order + 1
                ))
                db.session.commit()
                flash("Partner added.", "success")

        # --- Edit Partner ---
        elif form_type == "edit_partner":
            pid = request.form.get("partner_id", type=int)
            partner = db.session.get(Partner, pid)
            if partner:
                partner.name = request.form.get("partner_name", partner.name).strip()
                partner.slug = request.form.get("partner_slug", partner.slug).strip().lower().replace(" ", "-")
                partner.short_description = request.form.get("partner_short_desc", "").strip() or None
                partner.description = request.form.get("partner_description", "").strip() or None
                partner.price_list = request.form.get("partner_price_list", "").strip() or None
                file = request.files.get("partner_logo")
                if file and file.filename and allowed_file(file.filename):
                    fname = secure_filename(f"partner_{datetime.utcnow().timestamp()}_{file.filename}")
                    filepath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
                    file.save(filepath)
                    partner.logo_path = f"uploads/{fname}"
                db.session.commit()
                flash("Partner updated.", "success")

        # --- Delete Partner ---
        elif form_type == "delete_partner":
            pid = request.form.get("partner_id", type=int)
            partner = db.session.get(Partner, pid)
            if partner:
                db.session.delete(partner)
                db.session.commit()
                flash("Partner deleted.", "success")

        # --- Add Partner Image ---
        elif form_type == "add_partner_image":
            pid = request.form.get("partner_id", type=int)
            caption = request.form.get("image_caption", "").strip()
            file = request.files.get("partner_image")
            if pid and file and file.filename and allowed_file(file.filename):
                fname = secure_filename(f"partner_img_{datetime.utcnow().timestamp()}_{file.filename}")
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
                file.save(filepath)
                max_order = db.session.query(db.func.max(PartnerImage.sort_order)).filter(
                    PartnerImage.partner_id == pid).scalar() or 0
                db.session.add(PartnerImage(
                    partner_id=pid, image_path=f"uploads/{fname}",
                    caption=caption or None, sort_order=max_order + 1
                ))
                db.session.commit()
                flash("Image added.", "success")

        # --- Delete Partner Image ---
        elif form_type == "delete_partner_image":
            img_id = request.form.get("image_id", type=int)
            img = db.session.get(PartnerImage, img_id)
            if img:
                db.session.delete(img)
                db.session.commit()
                flash("Image deleted.", "success")

        active_tab = request.form.get("active_tab", "")
        return redirect(url_for("admin", tab=active_tab))

    # --- Workhour stats period ---
    wh_period = request.args.get("wh_period", "day")
    now = datetime.utcnow()
    if wh_period == "day":
        wh_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif wh_period == "week":
        wh_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif wh_period == "month":
        wh_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif wh_period == "year":
        wh_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        wh_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    users = User.query.order_by(User.username).all()
    all_dues = Due.query.order_by(Due.due_date.asc()).all()
    # Group dues by company for admin view
    from collections import defaultdict
    dues_by_company = defaultdict(list)
    dues_no_company = []
    for d in all_dues:
        if d.company_id and d.company:
            dues_by_company[d.company_id].append(d)
        else:
            dues_no_company.append(d)
    grouped_dues = []
    for company_id, company_dues in dues_by_company.items():
        company = company_dues[0].company
        total = sum(d.amount for d in company_dues)
        unpaid = sum(d.amount for d in company_dues if not d.is_paid)
        grouped_dues.append({
            "company": company,
            "dues": company_dues,
            "total": total,
            "unpaid": unpaid,
            "all_paid": all(d.is_paid for d in company_dues),
        })
    grouped_dues.sort(key=lambda x: x["company"].name)
    ads = Advertisement.query.order_by(Advertisement.created_at.desc()).all()
    companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
    contracts = Contract.query.order_by(Contract.created_at.desc()).all()
    work_logs = WorkLog.query.order_by(WorkLog.clock_in.desc()).limit(100).all()
    ingredients = Ingredient.query.order_by(Ingredient.name).all()
    menu_items = MenuItem.query.order_by(MenuItem.category, MenuItem.name).all()
    discounts = CompanyDiscount.query.all()
    ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
    fraction_members = User.query.filter_by(has_fraction_permission=True).all()
    fraction_members.sort(key=lambda u: (u.rank.sort_order if u.rank else 9999, u.display_name))

    # Per-user workhour stats
    workhour_stats = []
    for member in fraction_members:
        logs = WorkLog.query.filter(
            WorkLog.user_id == member.id,
            WorkLog.clock_in >= wh_start
        ).order_by(WorkLog.clock_in.desc()).all()
        total_secs = sum(l.duration_seconds for l in logs)
        h, rem = divmod(int(total_secs), 3600)
        m, _ = divmod(rem, 60)
        workhour_stats.append({
            "user": member,
            "logs": logs,
            "total_formatted": f"{h}h {m}m",
            "total_seconds": total_secs,
        })
    workhour_stats.sort(key=lambda x: x["total_seconds"], reverse=True)

    partners = Partner.query.order_by(Partner.sort_order).all()

    return render_template("admin.html", users=users, grouped_dues=grouped_dues,
                            dues_no_company=dues_no_company, ads=ads,
                            companies=companies, contracts=contracts, work_logs=work_logs,
                            ingredients=ingredients, menu_items=menu_items, discounts=discounts,
                            ranks=ranks, fraction_members=fraction_members,
                            workhour_stats=workhour_stats, wh_period=wh_period,
                            partners=partners)


# ---------------------------------------------------------------------------
# Database Init & Run
# ---------------------------------------------------------------------------

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
