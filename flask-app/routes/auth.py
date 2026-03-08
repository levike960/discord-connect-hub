"""Authentication routes — Discord OAuth2."""

import os
import requests as http_requests
from flask import redirect, url_for, session, request, flash
from flask_login import login_user, logout_user, login_required
from urllib.parse import quote_plus


def register_auth_routes(app, db, models):
    User = models["User"]

    DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
    DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
    DISCORD_REDIRECT_URI = os.environ.get(
        "DISCORD_REDIRECT_URI", "http://localhost:5000/callback"
    )
    DISCORD_API_BASE = "https://discord.com/api/v10"

    ADMIN_DISCORD_IDS: list[str] = [
        # "123456789012345678",
    ]

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

        token_res = http_requests.post(
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
        user_res = http_requests.get(
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
