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
StockMovement = models["StockMovement"]
GuestBookEntry = models["GuestBookEntry"]
GuestBookLike = models["GuestBookLike"]
RatingComment = models["RatingComment"]
Event = models["Event"]
Booking = models["Booking"]
BookingMessage = models["BookingMessage"]
BonusConfig = models["BonusConfig"]
BonusEntry = models["BonusEntry"]


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


@app.context_processor
def inject_now():
    return {"now": datetime.utcnow}


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
    guest_book = GuestBookEntry.query.order_by(GuestBookEntry.created_at.desc()).limit(50).all()
    # Find top-liked entry
    top_entry = GuestBookEntry.query.filter(GuestBookEntry.likes > 0).order_by(GuestBookEntry.likes.desc()).first()
    # User's likes
    user_likes = set()
    if hasattr(current_user, 'id') and current_user.is_authenticated:
        user_likes = {l.entry_id for l in GuestBookLike.query.filter_by(user_id=current_user.id).all()}
    # Preload comments per worker
    worker_comments = {}
    for w in workers:
        worker_comments[w.id] = RatingComment.query.filter_by(target_user_id=w.id)\
            .order_by(RatingComment.created_at.desc()).limit(20).all()
    # Events for calendar
    upcoming_events = Event.query.filter(
        Event.is_published == True,
        Event.event_date >= date.today()
    ).order_by(Event.event_date.asc()).all()
    # User's own bookings
    user_bookings = []
    if hasattr(current_user, 'id') and current_user.is_authenticated:
        user_bookings = Booking.query.filter_by(user_id=current_user.id)\
            .order_by(Booking.created_at.desc()).all()
    return render_template("visitor.html", workers=workers, partners=partners,
                           guest_book=guest_book, worker_comments=worker_comments,
                           upcoming_events=upcoming_events, user_bookings=user_bookings,
                           top_entry=top_entry, user_likes=user_likes)


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

@app.route("/guestbook", methods=["POST"])
@login_required
def add_guestbook_entry():
    message = request.form.get("message", "").strip()
    if not message or len(message) > 500:
        flash("Az üzenet nem lehet üres és max 500 karakter.", "warning")
        return redirect(url_for("visitor") + "#guestbook")
    entry = GuestBookEntry(user_id=current_user.id, message=message)
    db.session.add(entry)
    db.session.commit()
    flash("Bejegyzés hozzáadva a vendégkönyvhöz!", "success")
    return redirect(url_for("visitor") + "#guestbook")


@app.route("/guestbook/delete/<int:entry_id>", methods=["POST"])
@login_required
def delete_guestbook_entry(entry_id):
    entry = db.session.get(GuestBookEntry, entry_id)
    if not entry:
        flash("Bejegyzés nem található.", "danger")
    elif entry.user_id != current_user.id and not current_user.is_admin:
        flash("Nincs jogosultságod törölni ezt a bejegyzést.", "danger")
    else:
        db.session.delete(entry)
        db.session.commit()
        flash("Bejegyzés törölve.", "success")
    return redirect(url_for("visitor") + "#guestbook")


@app.route("/guestbook/like/<int:entry_id>", methods=["POST"])
@login_required
def like_guestbook_entry(entry_id):
    entry = db.session.get(GuestBookEntry, entry_id)
    if not entry:
        flash("Bejegyzés nem található.", "danger")
        return redirect(request.referrer or url_for("visitor") + "#guestbook")
    existing = GuestBookLike.query.filter_by(entry_id=entry_id, user_id=current_user.id).first()
    if existing:
        db.session.delete(existing)
        entry.likes = max(0, entry.likes - 1)
    else:
        db.session.add(GuestBookLike(entry_id=entry_id, user_id=current_user.id))
        entry.likes = entry.likes + 1
    db.session.commit()
    return redirect(request.referrer or url_for("visitor") + "#guestbook")


@app.route("/guestbook")
def guestbook_page():
    guest_book = GuestBookEntry.query.order_by(GuestBookEntry.created_at.desc()).all()
    user_likes = set()
    if hasattr(current_user, 'id') and current_user.is_authenticated:
        user_likes = {l.entry_id for l in GuestBookLike.query.filter_by(user_id=current_user.id).all()}
    return render_template("guestbook.html", guest_book=guest_book, user_likes=user_likes)


@app.route("/worker/<int:worker_id>/comment", methods=["POST"])
@login_required
def add_worker_comment(worker_id):
    target = db.session.get(User, worker_id)
    if not target or not target.has_fraction_permission:
        flash("Érvénytelen munkatárs.", "danger")
        return redirect(url_for("visitor"))
    if target.id == current_user.id:
        flash("Saját magadat nem kommentelheted.", "warning")
        return redirect(url_for("visitor"))
    comment_type = request.form.get("comment_type", "")
    content = request.form.get("content", "").strip()
    if comment_type not in ("positive", "negative"):
        flash("Érvénytelen megjegyzés típus.", "warning")
        return redirect(url_for("visitor"))
    if not content or len(content) > 300:
        flash("A megjegyzés nem lehet üres és max 300 karakter.", "warning")
        return redirect(url_for("visitor"))
    comment = RatingComment(
        reviewer_user_id=current_user.id,
        target_user_id=worker_id,
        comment_type=comment_type,
        content=content,
    )
    db.session.add(comment)
    db.session.commit()
    flash("Megjegyzés hozzáadva!", "success")
    return redirect(url_for("visitor") + "#ratings")


# ---------------------------------------------------------------------------
# Routes — Bookings & Events
# ---------------------------------------------------------------------------

@app.route("/booking/create", methods=["POST"])
@login_required
def create_booking():
    contact_name = request.form.get("contact_name", "").strip()
    booking_date_str = request.form.get("booking_date", "")
    booking_time = request.form.get("booking_time", "").strip()
    guest_count = request.form.get("guest_count", type=int, default=1)
    event_type_label = request.form.get("event_type_label", "").strip()
    note = request.form.get("note", "").strip()
    event_id = request.form.get("event_id", type=int)

    if not contact_name or len(contact_name) > 128:
        flash("A kapcsolattartó neve kötelező (max 128 karakter).", "warning")
        return redirect(url_for("visitor") + "#events")
    if not booking_date_str:
        flash("A dátum megadása kötelező.", "warning")
        return redirect(url_for("visitor") + "#events")
    try:
        booking_date = date.fromisoformat(booking_date_str)
    except ValueError:
        flash("Érvénytelen dátum.", "danger")
        return redirect(url_for("visitor") + "#events")
    if guest_count < 1 or guest_count > 500:
        flash("A létszám 1-500 között legyen.", "warning")
        return redirect(url_for("visitor") + "#events")

    contact_phone = request.form.get("contact_phone", "").strip()

    booking = Booking(
        user_id=current_user.id,
        event_id=event_id if event_id else None,
        booking_date=booking_date,
        booking_time=booking_time or None,
        guest_count=guest_count,
        event_type_label=event_type_label or None,
        contact_name=contact_name,
        contact_phone=contact_phone or None,
        note=note[:500] if note else None,
        status="pending",
    )
    db.session.add(booking)
    db.session.commit()
    flash("Foglalás leadva! Az adminisztrátor hamarosan megerősíti.", "success")
    return redirect(url_for("visitor") + "#mybookings")


@app.route("/booking/<int:booking_id>/message", methods=["POST"])
@login_required
def booking_message(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking:
        flash("Foglalás nem található.", "danger")
        return redirect(url_for("visitor"))
    # Only booking owner or admin can message
    if booking.user_id != current_user.id and not current_user.is_admin:
        flash("Nincs jogosultságod.", "danger")
        return redirect(url_for("visitor"))
    content = request.form.get("content", "").strip()
    if not content or len(content) > 500:
        flash("Az üzenet nem lehet üres (max 500 karakter).", "warning")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    msg = BookingMessage(booking_id=booking_id, user_id=current_user.id, content=content)
    db.session.add(msg)
    db.session.commit()
    flash("Üzenet elküldve!", "success")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/booking/<int:booking_id>")
@login_required
def booking_detail(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking:
        abort(404)
    # Only booking owner, admin, or fraction can view
    if booking.user_id != current_user.id and not current_user.is_admin and not current_user.has_fraction_permission:
        abort(403)
    messages = booking.messages.all()
    can_message = (booking.user_id == current_user.id or current_user.is_admin)
    return render_template("booking_detail.html", booking=booking, messages=messages, can_message=can_message)


@app.route("/fraction/bookings")
@fraction_required
def fraction_bookings():
    bookings = Booking.query.order_by(Booking.booking_date.desc()).all()
    return render_template("fraction_bookings.html", bookings=bookings)


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


@app.route("/fraction/dues", methods=["GET", "POST"])
@fraction_required
def fraction_dues():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("fraction_dues"))
    from collections import defaultdict
    all_dues = Due.query.order_by(Due.due_date.asc()).all()
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
            "company": company, "dues": company_dues,
            "total": total, "unpaid": unpaid,
            "all_paid": all(d.is_paid for d in company_dues),
        })
    grouped_dues.sort(key=lambda x: x["company"].name)
    return render_template("fraction_dues.html", grouped_dues=grouped_dues, dues_no_company=dues_no_company)


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


@app.route("/fraction/calculator/confirm", methods=["POST"])
@fraction_required
def fraction_calculator_confirm():
    """Process stock changes based on POS mode and clear cart."""
    mode = request.form.get("mode", "")
    cart = session.get("pos_cart", [])

    if not cart:
        flash("A kosár üres.", "warning")
        return redirect(url_for("fraction_calculator"))

    if mode in ("basic", "discount"):
        # Calculate total for revenue tracking
        sale_total = 0.0
        for c in cart:
            mi = db.session.get(MenuItem, c["id"])
            if mi:
                mi.stock = max(0, mi.stock - c["qty"])
                sale_total += mi.price * c["qty"]
                db.session.add(StockMovement(
                    item_type="menu_item", item_id=mi.id,
                    quantity=-c["qty"],
                    reason=f"POS eladás ({mode})",
                    user_id=current_user.id
                ))
        # If discount mode, recalculate with discounts
        if mode == "discount":
            company_id = request.form.get("company_id", type=int)
            if company_id:
                discounts = {d.category: d.discount_percent
                             for d in CompanyDiscount.query.filter_by(company_id=company_id).all()}
                disc_total = 0.0
                for c in cart:
                    mi = db.session.get(MenuItem, c["id"])
                    if mi:
                        pct = discounts.get(mi.category, 0)
                        disc_total += mi.price * c["qty"] * (1 - pct / 100)
                sale_total = disc_total
        # Record revenue as a Due entry
        label = "POS eladás (alapár)" if mode == "basic" else "POS eladás (kedvezményes)"
        due = Due(
            name=label,
            amount=sale_total,
            due_date=date.today(),
            is_paid=True,
            paid_at=datetime.utcnow(),
            created_by=current_user.id,
        )
        db.session.add(due)
        db.session.commit()
        session["pos_cart"] = []
        flash("Eladás rögzítve, raktár frissítve.", "success")

    elif mode == "production":
        # Add finished products to stock, deduct ingredients
        for c in cart:
            mi = db.session.get(MenuItem, c["id"])
            if mi:
                mi.stock += c["qty"]
                db.session.add(StockMovement(
                    item_type="menu_item", item_id=mi.id,
                    quantity=c["qty"],
                    reason="POS gyártás",
                    user_id=current_user.id
                ))
                # Deduct ingredients/sub-items based on recipe
                for ri in mi.recipe_items.all():
                    used = ri.quantity * c["qty"]
                    if ri.sub_menu_item_id and ri.sub_menu_item:
                        ri.sub_menu_item.stock = max(0, ri.sub_menu_item.stock - used)
                        db.session.add(StockMovement(
                            item_type="menu_item", item_id=ri.sub_menu_item_id,
                            quantity=-used,
                            reason=f"POS gyártás (altétel): {mi.name}",
                            user_id=current_user.id
                        ))
                    elif ri.ingredient:
                        ri.ingredient.stock = max(0, ri.ingredient.stock - used)
                        db.session.add(StockMovement(
                            item_type="ingredient", item_id=ri.ingredient.id,
                            quantity=-used,
                            reason=f"POS gyártás: {mi.name}",
                            user_id=current_user.id
                        ))
        db.session.commit()
        session["pos_cart"] = []
        flash("Gyártás rögzítve, raktár frissítve.", "success")

    return redirect(url_for("fraction_calculator"))


@app.route("/fraction/calculator/record_due", methods=["POST"])
@fraction_required
def fraction_calculator_record_due():
    """Record discounted total as a Due for the selected company + deduct stock."""
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
            # Also deduct products from stock
            cart = session.get("pos_cart", [])
            for c in cart:
                mi = db.session.get(MenuItem, c["id"])
                if mi:
                    mi.stock = max(0, mi.stock - c["qty"])
                    db.session.add(StockMovement(
                        item_type="menu_item", item_id=mi.id,
                        quantity=-c["qty"],
                        reason=f"POS felírás: {company.name}",
                        user_id=current_user.id
                    ))
            db.session.commit()
            session["pos_cart"] = []
            flash(f"Tartozás felírva + raktár frissítve: {company.name} — {'%.0f' % discount_total} Ft", "success")
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


@app.route("/fraction/warehouse", methods=["GET", "POST"])
@fraction_required
def fraction_warehouse():
    if request.method == "POST":
        form_type = request.form.get("form_type")
        item_type = request.form.get("item_type", "")  # 'ingredient' or 'menu_item'
        item_id = request.form.get("item_id", type=int)
        quantity = request.form.get("quantity", type=float)
        reason = request.form.get("reason", "").strip()

        if item_id and quantity and quantity != 0:
            if form_type == "stock_add":
                qty = abs(quantity)
            elif form_type == "stock_remove":
                qty = -abs(quantity)
            elif form_type == "stock_set":
                # Set absolute value - handled below
                pass
            else:
                flash("Érvénytelen művelet.", "danger")
                return redirect(url_for("fraction_warehouse"))

            # Update stock on the item
            if item_type == "ingredient":
                item = db.session.get(Ingredient, item_id)
            elif item_type == "menu_item":
                item = db.session.get(MenuItem, item_id)
            else:
                flash("Érvénytelen típus.", "danger")
                return redirect(url_for("fraction_warehouse"))

            if item:
                if form_type == "stock_set":
                    old_stock = item.stock
                    item.stock = max(0, quantity)
                    qty = item.stock - old_stock
                    db.session.add(StockMovement(
                        item_type=item_type, item_id=item_id,
                        quantity=qty, reason=reason or "Készlet beállítás",
                        user_id=current_user.id
                    ))
                    db.session.commit()
                    flash(f"Készlet beállítva: {item.name} → {item.stock}", "success")
                else:
                    item.stock = max(0, item.stock + qty)
                    db.session.add(StockMovement(
                        item_type=item_type, item_id=item_id,
                        quantity=qty, reason=reason or None,
                        user_id=current_user.id
                    ))
                    db.session.commit()
                    action_word = "hozzáadva" if qty > 0 else "elvéve"
                    flash(f"{abs(qty)} {action_word}: {item.name}", "success")
            else:
                flash("Tétel nem található.", "danger")

        return redirect(url_for("fraction_warehouse"))

    # GET
    ingredients = Ingredient.query.order_by(Ingredient.name).all()
    menu_items = MenuItem.query.order_by(MenuItem.category, MenuItem.name).all()
    recent_movements = StockMovement.query.order_by(
        StockMovement.created_at.desc()).limit(50).all()

    # Resolve item names for movements
    movement_data = []
    for mv in recent_movements:
        if mv.item_type == "ingredient":
            item = db.session.get(Ingredient, mv.item_id)
        else:
            item = db.session.get(MenuItem, mv.item_id)
        movement_data.append({
            "movement": mv,
            "item_name": item.name if item else "Törölt tétel",
            "item_unit": item.unit if hasattr(item, 'unit') and item else "db",
        })

    return render_template("fraction_warehouse.html",
                           ingredients=ingredients, menu_items=menu_items,
                           movement_data=movement_data)


@app.route("/fraction/contracts")
@fraction_required
def fraction_contracts():
    contracts = Contract.query.order_by(Contract.created_at.desc()).all()
    return render_template("fraction_contracts.html", contracts=contracts)


# ---------------------------------------------------------------------------
# Routes — Admin
# ---------------------------------------------------------------------------

def _admin_post_handler():
    """Shared POST handler for all admin sub-routes. Returns redirect target route name."""
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

    elif form_type == "delete_due":
        due_id = request.form.get("due_id", type=int)
        due = db.session.get(Due, due_id)
        if due:
            db.session.delete(due)
            db.session.commit()
            flash("Due deleted.", "success")

    elif form_type == "settle_due":
        due_id = request.form.get("due_id", type=int)
        due = db.session.get(Due, due_id)
        if due:
            due.is_paid = True
            due.paid_at = datetime.utcnow()
            db.session.commit()
            flash("Tartozás rendezve.", "success")

    elif form_type == "settle_company_dues":
        company_id = request.form.get("company_id", type=int)
        if company_id:
            unpaid = Due.query.filter_by(company_id=company_id, is_paid=False).all()
            for due in unpaid:
                due.is_paid = True
                due.paid_at = datetime.utcnow()
            db.session.commit()
            flash(f"{len(unpaid)} tartozás rendezve.", "success")

    elif form_type == "monthly_close_company":
        company_id = request.form.get("company_id", type=int)
        if company_id:
            now = datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            unpaid = Due.query.filter(
                Due.company_id == company_id,
                Due.is_paid == False,
                Due.created_at < month_start
            ).all()
            current_month = Due.query.filter(
                Due.company_id == company_id,
                Due.is_paid == False,
                Due.created_at >= month_start
            ).all()
            all_to_close = unpaid + current_month
            for due in all_to_close:
                due.is_paid = True
                due.paid_at = datetime.utcnow()
            db.session.commit()
            company = db.session.get(DeliveryCompany, company_id)
            cname = company.name if company else "?"
            flash(f"Havi zárás kész: {cname} — {len(all_to_close)} tétel rendezve.", "success")

    elif form_type == "add_ad":
        title = request.form.get("ad_title", "").strip()
        content = request.form.get("ad_content", "").strip()
        if title and content:
            db.session.add(Advertisement(title=title, content=content,
                                          created_by=current_user.id))
            db.session.commit()
            flash("Advertisement added.", "success")

    elif form_type == "delete_ad":
        ad_id = request.form.get("ad_id", type=int)
        ad = db.session.get(Advertisement, ad_id)
        if ad:
            db.session.delete(ad)
            db.session.commit()
            flash("Advertisement deleted.", "success")

    elif form_type == "add_company":
        name = request.form.get("company_name", "").strip()
        if name:
            db.session.add(DeliveryCompany(name=name))
            db.session.commit()
            flash("Company added.", "success")

    elif form_type == "delete_company":
        cid = request.form.get("company_id", type=int)
        company = db.session.get(DeliveryCompany, cid)
        if company:
            DeliveryMessage.query.filter_by(company_id=cid).delete()
            db.session.delete(company)
            db.session.commit()
            flash("Company and its messages deleted.", "success")

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

    elif form_type == "delete_contract":
        cid = request.form.get("contract_id", type=int)
        contract = db.session.get(Contract, cid)
        if contract:
            db.session.delete(contract)
            db.session.commit()
            flash("Contract deleted.", "success")

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

    elif form_type == "update_min_stock":
        item_type = request.form.get("item_type", "")
        item_id = request.form.get("item_id", type=int)
        min_stock = request.form.get("min_stock", type=float)
        if item_id and min_stock is not None:
            if item_type == "ingredient":
                item = db.session.get(Ingredient, item_id)
            elif item_type == "menu_item":
                item = db.session.get(MenuItem, item_id)
            else:
                item = None
            if item:
                item.min_stock = max(0, min_stock)
                db.session.commit()
                flash(f"Min. készlet frissítve: {item.name} → {item.min_stock}", "success")

    elif form_type == "add_ingredient":
        name = request.form.get("ing_name", "").strip()
        unit = request.form.get("ing_unit", "db").strip()
        price = request.form.get("ing_price", type=float)
        if name and price is not None:
            db.session.add(Ingredient(name=name, unit=unit, price_per_unit=price))
            db.session.commit()
            flash("Ingredient added.", "success")

    elif form_type == "delete_ingredient":
        ing_id = request.form.get("ing_id", type=int)
        ing = db.session.get(Ingredient, ing_id)
        if ing:
            db.session.delete(ing)
            db.session.commit()
            flash("Ingredient deleted.", "success")

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

    elif form_type == "update_menu_item_image":
        mi_id = request.form.get("mi_id", type=int)
        mi = db.session.get(MenuItem, mi_id)
        if mi:
            file = request.files.get("mi_image")
            if file and file.filename and allowed_file(file.filename):
                fname = secure_filename(f"menu_{datetime.utcnow().timestamp()}_{file.filename}")
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
                file.save(filepath)
                mi.image_path = f"uploads/{fname}"
                db.session.commit()
                flash("Kép frissítve.", "success")

    elif form_type == "delete_menu_item":
        mi_id = request.form.get("mi_id", type=int)
        mi = db.session.get(MenuItem, mi_id)
        if mi:
            db.session.delete(mi)
            db.session.commit()
            flash("Menu item deleted.", "success")

    elif form_type == "add_recipe_item":
        mi_id = request.form.get("mi_id", type=int)
        ri_type = request.form.get("ri_type", "ingredient")
        ing_id = request.form.get("ri_ingredient_id", type=int)
        sub_mi_id = request.form.get("ri_sub_menu_item_id", type=int)
        qty = request.form.get("ri_quantity", type=float, default=1)
        if mi_id and (ing_id or sub_mi_id):
            new_ri = MenuItemIngredient(menu_item_id=mi_id, quantity=qty)
            if ri_type == "menu_item" and sub_mi_id:
                if sub_mi_id == mi_id:
                    flash("Egy tétel nem lehet saját maga hozzávalója!", "danger")
                    return redirect(request.referrer or url_for("admin_page"))
                new_ri.sub_menu_item_id = sub_mi_id
                new_ri.ingredient_id = None
            else:
                new_ri.ingredient_id = ing_id
                new_ri.sub_menu_item_id = None
            db.session.add(new_ri)
            db.session.commit()
            mi = db.session.get(MenuItem, mi_id)
            if mi:
                mi.production_cost = mi.calculated_cost
                db.session.commit()
            flash("Recept hozzávaló hozzáadva, költség újraszámolva.", "success")

    elif form_type == "remove_recipe_item":
        ri_id = request.form.get("ri_id", type=int)
        ri = db.session.get(MenuItemIngredient, ri_id)
        if ri:
            mi_id = ri.menu_item_id
            db.session.delete(ri)
            db.session.commit()
            mi = db.session.get(MenuItem, mi_id)
            if mi:
                mi.production_cost = mi.calculated_cost
                db.session.commit()
            flash("Recipe ingredient removed, cost recalculated.", "success")

    elif form_type == "recalc_cost":
        mi_id = request.form.get("mi_id", type=int)
        mi = db.session.get(MenuItem, mi_id)
        if mi:
            mi.production_cost = mi.calculated_cost
            db.session.commit()
            flash(f"Production cost recalculated: {mi.production_cost} Ft", "success")

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

    elif form_type == "delete_discount":
        disc_id = request.form.get("disc_id", type=int)
        disc = db.session.get(CompanyDiscount, disc_id)
        if disc:
            db.session.delete(disc)
            db.session.commit()
            flash("Discount deleted.", "success")

    elif form_type == "add_rank":
        rname = request.form.get("rank_name", "").strip()
        rcolor = request.form.get("rank_color", "").strip() or None
        if rname:
            max_order = db.session.query(db.func.max(Rank.sort_order)).scalar() or 0
            db.session.add(Rank(name=rname, sort_order=max_order + 1, color=rcolor))
            db.session.commit()
            flash("Rank added.", "success")

    elif form_type == "delete_rank":
        rank_id = request.form.get("rank_id", type=int)
        rank = db.session.get(Rank, rank_id)
        if rank:
            for u in User.query.filter_by(rank_id=rank_id).all():
                u.rank_id = None
            db.session.delete(rank)
            db.session.commit()
            flash("Rank deleted.", "success")

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

    elif form_type == "delete_partner":
        pid = request.form.get("partner_id", type=int)
        partner = db.session.get(Partner, pid)
        if partner:
            db.session.delete(partner)
            db.session.commit()
            flash("Partner deleted.", "success")

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

    elif form_type == "delete_partner_image":
        img_id = request.form.get("image_id", type=int)
        img = db.session.get(PartnerImage, img_id)
        if img:
            db.session.delete(img)
            db.session.commit()
            flash("Image deleted.", "success")

    elif form_type == "add_event":
        title = request.form.get("event_title", "").strip()
        desc = request.form.get("event_description", "").strip()
        event_date_str = request.form.get("event_date", "")
        event_time = request.form.get("event_time", "").strip()
        event_type = request.form.get("event_type", "public")
        if title and event_date_str:
            try:
                event_date = date.fromisoformat(event_date_str)
                db.session.add(Event(
                    title=title, description=desc or None,
                    event_date=event_date, event_time=event_time or None,
                    event_type=event_type, created_by=current_user.id
                ))
                db.session.commit()
                flash("Esemény hozzáadva.", "success")
            except ValueError:
                flash("Érvénytelen dátum.", "danger")

    elif form_type == "edit_event":
        eid = request.form.get("event_id", type=int)
        event = db.session.get(Event, eid)
        if event:
            event.title = request.form.get("event_title", event.title).strip()
            event.description = request.form.get("event_description", "").strip() or None
            ed = request.form.get("event_date", "")
            if ed:
                try:
                    event.event_date = date.fromisoformat(ed)
                except ValueError:
                    pass
            event.event_time = request.form.get("event_time", "").strip() or None
            event.event_type = request.form.get("event_type", event.event_type)
            event.is_published = "is_published" in request.form
            db.session.commit()
            flash("Esemény frissítve.", "success")

    elif form_type == "delete_event":
        eid = request.form.get("event_id", type=int)
        event = db.session.get(Event, eid)
        if event:
            db.session.delete(event)
            db.session.commit()
            flash("Esemény törölve.", "success")

    elif form_type == "update_booking_status":
        bid = request.form.get("booking_id", type=int)
        new_status = request.form.get("status", "")
        booking = db.session.get(Booking, bid)
        if booking and new_status in ("confirmed", "rejected", "pending"):
            booking.status = new_status
            db.session.commit()
            flash(f"Foglalás státusza frissítve: {new_status}.", "success")

    elif form_type == "delete_booking":
        bid = request.form.get("booking_id", type=int)
        booking = db.session.get(Booking, bid)
        if booking:
            db.session.delete(booking)
            db.session.commit()
            flash("Foglalás törölve.", "success")


# ---------------------------------------------------------------------------
# Admin Dashboard (lightweight — only loads stats)
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    from collections import defaultdict
    all_dues = Due.query.all()
    total_unpaid = sum(d.amount for d in all_dues if not d.is_paid)
    unpaid_count = sum(1 for d in all_dues if not d.is_paid)
    fraction_members = User.query.filter_by(has_fraction_permission=True).all()
    active_workers = sum(1 for m in fraction_members if m.is_clocked_in)
    total_members = len(fraction_members)
    ingredients = Ingredient.query.all()
    menu_items = MenuItem.query.all()
    low_stock_ingredients = [i for i in ingredients if i.stock < i.min_stock]
    low_stock_products = [m for m in menu_items if m.stock < m.min_stock]
    total_low_stock = len(low_stock_ingredients) + len(low_stock_products)

    return render_template("admin.html",
                           dash_total_unpaid=total_unpaid, dash_unpaid_count=unpaid_count,
                           dash_active_workers=active_workers, dash_total_members=total_members,
                           dash_low_stock_ingredients=low_stock_ingredients,
                           dash_low_stock_products=low_stock_products,
                           dash_total_low_stock=total_low_stock)

@app.route("/admin/reviews")
@admin_required
def admin_reviews():
    guestbook_entries = GuestBookEntry.query.order_by(GuestBookEntry.created_at.desc()).all()
    rating_comments = RatingComment.query.order_by(RatingComment.created_at.desc()).all()
    return render_template("admin_reviews.html",
                           guestbook_entries=guestbook_entries,
                           rating_comments=rating_comments)


@app.route("/admin/reviews/guestbook/<int:entry_id>/delete", methods=["POST"])
@admin_required
def admin_delete_guestbook(entry_id):
    entry = db.session.get(GuestBookEntry, entry_id)
    if entry:
        GuestBookLike.query.filter_by(entry_id=entry.id).delete()
        db.session.delete(entry)
        db.session.commit()
        flash("Vendégkönyv bejegyzés törölve.", "success")
    else:
        flash("Bejegyzés nem található.", "danger")
    return redirect(url_for("admin_reviews"))


@app.route("/admin/reviews/comment/<int:comment_id>/delete", methods=["POST"])
@admin_required
def admin_delete_comment(comment_id):
    comment = db.session.get(RatingComment, comment_id)
    if comment:
        db.session.delete(comment)
        db.session.commit()
        flash("Vélemény törölve.", "success")
    else:
        flash("Vélemény nem található.", "danger")
    return redirect(url_for("admin_reviews"))


# ---------------------------------------------------------------------------
# Admin Sub-Routes (each loads only its own data)
# ---------------------------------------------------------------------------

@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_users"))
    users = User.query.order_by(User.username).all()
    return render_template("admin_users.html", users=users)


@app.route("/admin/ranks", methods=["GET", "POST"])
@admin_required
def admin_ranks():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_ranks"))
    ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
    return render_template("admin_ranks_page.html", ranks=ranks)


@app.route("/admin/members", methods=["GET", "POST"])
@admin_required
def admin_members():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_members"))
    ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
    fraction_members = User.query.filter_by(has_fraction_permission=True).all()
    fraction_members.sort(key=lambda u: (u.rank.sort_order if u.rank else 9999, u.display_name))
    return render_template("admin_members.html", fraction_members=fraction_members, ranks=ranks)


@app.route("/admin/dues", methods=["GET", "POST"])
@admin_required
def admin_dues():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_dues"))
    from collections import defaultdict
    all_dues = Due.query.order_by(Due.due_date.asc()).all()
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
            "company": company, "dues": company_dues,
            "total": total, "unpaid": unpaid,
            "all_paid": all(d.is_paid for d in company_dues),
        })
    grouped_dues.sort(key=lambda x: x["company"].name)
    return render_template("admin_dues.html", grouped_dues=grouped_dues, dues_no_company=dues_no_company)


@app.route("/admin/ads", methods=["GET", "POST"])
@admin_required
def admin_ads():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_ads"))
    ads = Advertisement.query.order_by(Advertisement.created_at.desc()).all()
    return render_template("admin_ads.html", ads=ads)


@app.route("/admin/companies", methods=["GET", "POST"])
@admin_required
def admin_companies():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_companies"))
    companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
    return render_template("admin_companies.html", companies=companies)


@app.route("/admin/contracts", methods=["GET", "POST"])
@admin_required
def admin_contracts():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_contracts"))
    contracts = Contract.query.order_by(Contract.created_at.desc()).all()
    return render_template("admin_contracts_page.html", contracts=contracts)


@app.route("/admin/worklogs", methods=["GET", "POST"])
@admin_required
def admin_worklogs():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_worklogs"))
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
    fraction_members = User.query.filter_by(has_fraction_permission=True).all()
    fraction_members.sort(key=lambda u: (u.rank.sort_order if u.rank else 9999, u.display_name))
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
            "user": member, "logs": logs,
            "total_formatted": f"{h}h {m}m",
            "total_seconds": total_secs,
        })
    workhour_stats.sort(key=lambda x: x["total_seconds"], reverse=True)
    return render_template("admin_worklogs.html", workhour_stats=workhour_stats, wh_period=wh_period)


@app.route("/admin/ingredients", methods=["GET", "POST"])
@admin_required
def admin_ingredients():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_ingredients"))
    ingredients = Ingredient.query.order_by(Ingredient.name).all()
    return render_template("admin_ingredients.html", ingredients=ingredients)


@app.route("/admin/menuitems", methods=["GET", "POST"])
@admin_required
def admin_menuitems():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_menuitems"))
    menu_items = MenuItem.query.order_by(MenuItem.category, MenuItem.name).all()
    ingredients = Ingredient.query.order_by(Ingredient.name).all()
    return render_template("admin_menuitems.html", menu_items=menu_items, ingredients=ingredients)


@app.route("/admin/discounts", methods=["GET", "POST"])
@admin_required
def admin_discounts():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_discounts"))
    discounts = CompanyDiscount.query.all()
    companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
    return render_template("admin_discounts.html", discounts=discounts, companies=companies)


@app.route("/admin/partners", methods=["GET", "POST"])
@admin_required
def admin_partners_page():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_partners_page"))
    partners = Partner.query.order_by(Partner.sort_order).all()
    return render_template("admin_partners_page.html", partners=partners)


@app.route("/admin/reports")
@admin_required
def admin_reports_page():
    now = datetime.utcnow()
    report_period = request.args.get("report_period", "week")
    if report_period == "month":
        report_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        num_days = (now - report_start).days + 1
    else:
        report_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        num_days = 7
    report_dues = Due.query.filter(Due.created_at >= report_start).all()
    report_revenue = sum(d.amount for d in report_dues)
    report_revenue_count = len(report_dues)
    report_movements = StockMovement.query.filter(StockMovement.created_at >= report_start).all()
    report_stock_in_count = sum(1 for m in report_movements if m.quantity > 0)
    report_stock_out_count = sum(1 for m in report_movements if m.quantity < 0)
    report_wlogs = WorkLog.query.filter(WorkLog.clock_in >= report_start).all()
    report_total_secs = sum(l.duration_seconds for l in report_wlogs)
    rh, rm = divmod(int(report_total_secs), 3600)
    rmm, _ = divmod(rm, 60)
    report_total_hours = f"{rh}h {rmm}m"
    report_worklogs_count = len(report_wlogs)
    from collections import defaultdict as dd
    worker_secs = dd(lambda: {"secs": 0, "count": 0, "user": None})
    for l in report_wlogs:
        ws = worker_secs[l.user_id]
        ws["secs"] += l.duration_seconds
        ws["count"] += 1
        ws["user"] = l.user
    report_worker_stats = []
    for uid, ws in worker_secs.items():
        wh, wr = divmod(int(ws["secs"]), 3600)
        wm, _ = divmod(wr, 60)
        report_worker_stats.append({
            "user": ws["user"], "formatted": f"{wh}h {wm}m",
            "total_secs": ws["secs"], "count": ws["count"]
        })
    report_worker_stats.sort(key=lambda x: x["total_secs"], reverse=True)
    chart_labels = []
    chart_revenue = []
    chart_workhours = []
    for i in range(num_days):
        day = report_start + timedelta(days=i)
        day_end = day + timedelta(days=1)
        chart_labels.append(day.strftime("%m.%d"))
        chart_revenue.append(sum(d.amount for d in report_dues
                                 if d.created_at and day <= d.created_at < day_end))
        day_secs = sum(l.duration_seconds for l in report_wlogs
                       if l.clock_in and day <= l.clock_in < day_end)
        chart_workhours.append(round(day_secs / 3600, 1))
    return render_template("admin_reports_page.html",
                           report_period=report_period,
                           report_revenue=report_revenue,
                           report_revenue_count=report_revenue_count,
                           report_stock_in_count=report_stock_in_count,
                           report_stock_out_count=report_stock_out_count,
                           report_total_hours=report_total_hours,
                           report_worklogs_count=report_worklogs_count,
                           report_worker_stats=report_worker_stats,
                           report_chart_labels=chart_labels,
                           report_chart_revenue=chart_revenue,
                           report_chart_workhours=chart_workhours)


@app.route("/admin/events", methods=["GET", "POST"])
@admin_required
def admin_events_page():
    if request.method == "POST":
        _admin_post_handler()
        return redirect(url_for("admin_events_page"))
    import json
    events = Event.query.order_by(Event.event_date.desc()).all()
    all_bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    pending_bookings_count = sum(1 for b in all_bookings if b.status == "pending")
    # Pre-serialize calendar data to avoid Jinja tojson issues
    events_json = json.dumps([{
        "id": e.id, "title": e.title, "date": e.event_date.isoformat(),
        "time": e.event_time or "", "type": e.event_type,
        "published": e.is_published, "desc": e.description or ""
    } for e in events])
    bookings_json = json.dumps([{
        "id": b.id, "contact": b.contact_name, "date": b.booking_date.isoformat(),
        "time": b.booking_time or "", "guests": b.guest_count,
        "type_label": b.event_type_label or "", "status": b.status,
        "url": url_for("booking_detail", booking_id=b.id)
    } for b in all_bookings])
    return render_template("admin_events_page.html",
                           events=events, all_bookings=all_bookings,
                           pending_bookings_count=pending_bookings_count,
                           events_json=events_json, bookings_json=bookings_json)
# ---------------------------------------------------------------------------
# Database Init & Run
# ---------------------------------------------------------------------------

def _auto_migrate_db():
    """Automatically add missing tables and columns on startup.
    
    This inspects the SQLAlchemy models vs the actual DB schema and applies
    ALTER TABLE ADD COLUMN for any new columns. New tables are created by
    db.create_all(). Existing data is preserved — nothing is dropped.
    """
    from sqlalchemy import inspect, text

    db.create_all()  # Creates any brand-new tables

    # --- Special migration: make ingredient_id nullable & add sub_menu_item_id ---
    try:
        if "menu_item_ingredients" in inspector.get_table_names():
            cols_info = inspector.get_columns("menu_item_ingredients")
            col_names = [c["name"] for c in cols_info]
            needs_rebuild = False
            # Check if ingredient_id is NOT NULL (needs to become nullable)
            for ci in cols_info:
                if ci["name"] == "ingredient_id" and not ci.get("nullable", True):
                    needs_rebuild = True
                    break
            # Check if sub_menu_item_id column is missing
            if "sub_menu_item_id" not in col_names:
                needs_rebuild = True
            if needs_rebuild:
                db.session.execute(text(
                    "CREATE TABLE _mii_backup AS SELECT * FROM menu_item_ingredients"
                ))
                db.session.execute(text("DROP TABLE menu_item_ingredients"))
                db.session.commit()
                db.create_all()
                # Restore data — sub_menu_item_id defaults to NULL
                existing_cols = ", ".join(c for c in col_names if c in ("id", "menu_item_id", "ingredient_id", "quantity"))
                db.session.execute(text(
                    f"INSERT INTO menu_item_ingredients ({existing_cols}) SELECT {existing_cols} FROM _mii_backup"
                ))
                db.session.execute(text("DROP TABLE _mii_backup"))
                db.session.commit()
                print("  [AUTO-MIGRATE] Rebuilt menu_item_ingredients: ingredient_id nullable, sub_menu_item_id added")
    except Exception as e:
        db.session.rollback()
        print(f"  [AUTO-MIGRATE] menu_item_ingredients migration error: {e}")

    inspector = inspect(db.engine)
    existing_tables = inspector.get_table_names()

    for table_name, table in db.metadata.tables.items():
        if table_name not in existing_tables:
            continue  # Already created by create_all above

        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in existing_columns:
                # Build ALTER TABLE statement
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
                # SQLite doesn't support NOT NULL without DEFAULT on existing tables
                if nullable == " NOT NULL" and not default:
                    nullable = ""  # Make it nullable to avoid SQLite error
                sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}{nullable}{default}'
                try:
                    db.session.execute(text(sql))
                    db.session.commit()
                    print(f"  [AUTO-MIGRATE] Added column: {table_name}.{column.name}")
                except Exception as e:
                    db.session.rollback()
                    # Column might already exist or other benign error
                    print(f"  [AUTO-MIGRATE] Skipped {table_name}.{column.name}: {e}")


with app.app_context():
    _auto_migrate_db()
    print("[AUTO-MIGRATE] Database check complete.")

if __name__ == "__main__":
    app.run(debug=True)
