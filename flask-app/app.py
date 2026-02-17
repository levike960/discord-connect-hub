"""
Flask Web Application with Discord OAuth2 Authentication
"""

import os
import requests
from functools import wraps
from flask import (
    Flask, render_template, redirect, url_for, session,
    request, flash, abort, send_from_directory
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

# Admins configurable by Discord ID
ADMIN_DISCORD_IDS: list[str] = [
    # "123456789012345678",  # Add admin Discord IDs here
]

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "visitor"  # type: ignore[assignment]

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ---------------------------------------------------------------------------
# Database Model
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):  # type: ignore[name-defined]
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    discord_id = db.Column(db.String(64), unique=True, nullable=False)
    username = db.Column(db.String(128), nullable=False)
    nickname = db.Column(db.String(128), nullable=True)
    avatar = db.Column(db.String(256), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    has_fraction_permission = db.Column(db.Boolean, default=False)

    @property
    def display_name(self) -> str:
        return self.nickname or self.username

    @property
    def avatar_url(self) -> str:
        # Prefer custom uploaded avatar
        custom = os.path.join(
            app.config["UPLOAD_FOLDER"], f"avatar_{self.discord_id}.png"
        )
        if os.path.isfile(custom):
            return url_for(
                "static", filename=f"uploads/avatar_{self.discord_id}.png"
            )
        # Fall back to Discord avatar
        if self.avatar:
            return (
                f"https://cdn.discordapp.com/avatars/"
                f"{self.discord_id}/{self.avatar}.png?size=128"
            )
        # Default
        return "https://cdn.discordapp.com/embed/avatars/0.png"


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Custom Decorators
# ---------------------------------------------------------------------------

def admin_required(f):
    """Allow only admins."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("You do not have admin access.", "danger")
            return redirect(url_for("visitor"))
        return f(*args, **kwargs)
    return decorated


def fraction_required(f):
    """Allow only users with fraction permission or admins."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not (current_user.has_fraction_permission or current_user.is_admin):
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


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

    # Exchange code for token
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

    # Fetch user info
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

    # Upsert user
    user = User.query.filter_by(discord_id=discord_id).first()
    if user is None:
        user = User(
            discord_id=discord_id,
            username=username,
            avatar=avatar,
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
# Routes — Pages
# ---------------------------------------------------------------------------

@app.route("/")
def visitor():
    return render_template("visitor.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        # Nickname change
        new_nick = request.form.get("nickname", "").strip()
        if new_nick:
            current_user.nickname = new_nick
        else:
            current_user.nickname = None

        # Avatar upload
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


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
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
        return redirect(url_for("admin"))

    users = User.query.order_by(User.username).all()
    return render_template("admin.html", users=users)


@app.route("/fraction")
@fraction_required
def fraction():
    return render_template("fraction.html")


# ---------------------------------------------------------------------------
# Database Init & Run
# ---------------------------------------------------------------------------

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
