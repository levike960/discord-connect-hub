"""
Database models for the Flask application.
"""

import os
from datetime import datetime
from flask_login import UserMixin
from flask import url_for


# db is injected from app.py after creation
db = None
app_config = None


def init_models(database, config):
    """Initialize models with the database instance."""
    global db, app_config
    db = database
    app_config = config
    return db


class User(UserMixin, object):
    """Registered user via Discord OAuth."""
    pass  # Defined dynamically below


class Rating(object):
    pass


class WorkLog(object):
    pass


class Due(object):
    pass


class Advertisement(object):
    pass


class DeliveryCompany(object):
    pass


class DeliveryMessage(object):
    pass


class Contract(object):
    pass


def define_models(db, app):
    """Define all SQLAlchemy models. Must be called after db is created."""

    class User(UserMixin, db.Model):
        __tablename__ = "users"

        id = db.Column(db.Integer, primary_key=True)
        discord_id = db.Column(db.String(64), unique=True, nullable=False)
        username = db.Column(db.String(128), nullable=False)
        nickname = db.Column(db.String(128), nullable=True)
        avatar = db.Column(db.String(256), nullable=True)
        is_admin = db.Column(db.Boolean, default=False)
        has_fraction_permission = db.Column(db.Boolean, default=False)

        ratings_received = db.relationship(
            "Rating", foreign_keys="Rating.target_user_id",
            backref="target_user", lazy="dynamic"
        )
        work_logs = db.relationship("WorkLog", backref="user", lazy="dynamic")

        @property
        def display_name(self):
            return self.nickname or self.username

        @property
        def average_rating(self):
            ratings = self.ratings_received.all()
            if not ratings:
                return 0.0
            return round(sum(r.stars for r in ratings) / len(ratings), 1)

        @property
        def rating_count(self):
            return self.ratings_received.count()

        @property
        def avatar_url(self):
            custom = os.path.join(
                app.config["UPLOAD_FOLDER"], f"avatar_{self.discord_id}.png"
            )
            if os.path.isfile(custom):
                return url_for("static", filename=f"uploads/avatar_{self.discord_id}.png")
            if self.avatar:
                return (
                    f"https://cdn.discordapp.com/avatars/"
                    f"{self.discord_id}/{self.avatar}.png?size=128"
                )
            return "https://cdn.discordapp.com/embed/avatars/0.png"

        @property
        def is_clocked_in(self):
            """Check if user has an open work log (clocked in but not out)."""
            return self.work_logs.filter_by(clock_out=None).first() is not None

        @property
        def active_work_log(self):
            return self.work_logs.filter_by(clock_out=None).first()

    class Rating(db.Model):
        __tablename__ = "ratings"

        id = db.Column(db.Integer, primary_key=True)
        reviewer_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        stars = db.Column(db.Integer, nullable=False)

        __table_args__ = (
            db.UniqueConstraint("reviewer_user_id", "target_user_id", name="uq_rating"),
        )
        reviewer = db.relationship("User", foreign_keys=[reviewer_user_id])

    class WorkLog(db.Model):
        __tablename__ = "work_logs"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        clock_in = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        clock_out = db.Column(db.DateTime, nullable=True)

        @property
        def duration_seconds(self):
            if self.clock_out:
                return (self.clock_out - self.clock_in).total_seconds()
            return (datetime.utcnow() - self.clock_in).total_seconds()

        @property
        def duration_formatted(self):
            secs = int(self.duration_seconds)
            h, remainder = divmod(secs, 3600)
            m, s = divmod(remainder, 60)
            return f"{h}h {m}m {s}s"

    class Due(db.Model):
        __tablename__ = "dues"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(128), nullable=False)
        amount = db.Column(db.Float, nullable=False)
        due_date = db.Column(db.Date, nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    class Advertisement(db.Model):
        __tablename__ = "advertisements"

        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(256), nullable=False)
        content = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    class DeliveryCompany(db.Model):
        __tablename__ = "delivery_companies"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(128), nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        messages = db.relationship("DeliveryMessage", backref="company", lazy="dynamic",
                                   order_by="DeliveryMessage.created_at.desc()")

    class DeliveryMessage(db.Model):
        __tablename__ = "delivery_messages"

        id = db.Column(db.Integer, primary_key=True)
        company_id = db.Column(db.Integer, db.ForeignKey("delivery_companies.id"), nullable=False)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        content = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        author = db.relationship("User", backref="delivery_messages")

    class Contract(db.Model):
        __tablename__ = "contracts"

        id = db.Column(db.Integer, primary_key=True)
        company_name = db.Column(db.String(256), nullable=False)
        description = db.Column(db.Text, nullable=False)
        image_path = db.Column(db.String(512), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    return {
        "User": User,
        "Rating": Rating,
        "WorkLog": WorkLog,
        "Due": Due,
        "Advertisement": Advertisement,
        "DeliveryCompany": DeliveryCompany,
        "DeliveryMessage": DeliveryMessage,
        "Contract": Contract,
    }
