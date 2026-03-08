"""
Database models for the Flask application.
Uses proper CET timezone via zoneinfo instead of manual timedelta(hours=1).
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from flask_login import UserMixin
from flask import url_for

CET = ZoneInfo("Europe/Budapest")


def now_cet():
    """Return current CET/CEST time as naive datetime (SQLite compatible)."""
    return datetime.now(CET).replace(tzinfo=None)


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
    pass


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
        ingame_name = db.Column(db.String(128), nullable=True)
        phone = db.Column(db.String(32), nullable=True)
        rank_id = db.Column(db.Integer, db.ForeignKey("ranks.id"), nullable=True)

        rank = db.relationship("Rank", backref="users", lazy="joined")

        ratings_received = db.relationship(
            "Rating", foreign_keys="Rating.target_user_id",
            backref="target_user", lazy="dynamic"
        )
        work_logs = db.relationship("WorkLog", backref="user", lazy="dynamic")

        @property
        def display_name(self):
            return self.ingame_name or self.nickname or self.username

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
                mtime = int(os.path.getmtime(custom))
                return url_for("static", filename=f"uploads/avatar_{self.discord_id}.png") + f"?v={mtime}"
            if self.avatar:
                return (
                    f"https://cdn.discordapp.com/avatars/"
                    f"{self.discord_id}/{self.avatar}.png?size=128"
                )
            return "https://cdn.discordapp.com/embed/avatars/0.png"

        @property
        def is_clocked_in(self):
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
        clock_in = db.Column(db.DateTime, nullable=False, default=now_cet)
        clock_out = db.Column(db.DateTime, nullable=True)

        @property
        def duration_seconds(self):
            end = self.clock_out if self.clock_out else now_cet()
            return (end - self.clock_in).total_seconds()

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
        is_paid = db.Column(db.Boolean, default=False)
        paid_at = db.Column(db.DateTime, nullable=True)
        company_id = db.Column(db.Integer, db.ForeignKey("delivery_companies.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=now_cet)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

        company = db.relationship("DeliveryCompany", backref="dues")
        creator = db.relationship("User", foreign_keys=[created_by], backref="created_dues")

    class Advertisement(db.Model):
        __tablename__ = "advertisements"

        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(256), nullable=False)
        content = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=now_cet)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    class DeliveryCompany(db.Model):
        __tablename__ = "delivery_companies"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(128), nullable=False)
        created_at = db.Column(db.DateTime, default=now_cet)

        messages = db.relationship("DeliveryMessage", backref="company", lazy="dynamic",
                                   order_by="DeliveryMessage.created_at.desc()")

    class DeliveryMessage(db.Model):
        __tablename__ = "delivery_messages"

        id = db.Column(db.Integer, primary_key=True)
        company_id = db.Column(db.Integer, db.ForeignKey("delivery_companies.id"), nullable=False)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        content = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=now_cet)

        author = db.relationship("User", backref="delivery_messages")

    class Contract(db.Model):
        __tablename__ = "contracts"

        id = db.Column(db.Integer, primary_key=True)
        company_name = db.Column(db.String(256), nullable=False)
        description = db.Column(db.Text, nullable=False)
        image_path = db.Column(db.String(512), nullable=True)
        created_at = db.Column(db.DateTime, default=now_cet)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    class Rank(db.Model):
        __tablename__ = "ranks"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(128), nullable=False, unique=True)
        sort_order = db.Column(db.Integer, nullable=False, default=0)
        color = db.Column(db.String(32), nullable=True)
        created_at = db.Column(db.DateTime, default=now_cet)

    class Ingredient(db.Model):
        __tablename__ = "ingredients"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(128), nullable=False, unique=True)
        unit = db.Column(db.String(32), nullable=False, default="db")
        price_per_unit = db.Column(db.Float, nullable=False, default=0.0)
        stock = db.Column(db.Float, nullable=False, default=0.0)
        min_stock = db.Column(db.Float, nullable=False, default=5.0)
        weight_per_unit_gram = db.Column(db.Float, nullable=True, default=0.0)
        created_at = db.Column(db.DateTime, default=now_cet)

    class MenuItem(db.Model):
        __tablename__ = "menu_items"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(128), nullable=False)
        category = db.Column(db.String(32), nullable=False)
        price = db.Column(db.Float, nullable=False, default=0.0)
        production_cost = db.Column(db.Float, nullable=False, default=0.0)
        production_time_seconds = db.Column(db.Integer, nullable=False, default=0)
        image_path = db.Column(db.String(512), nullable=True)
        stock = db.Column(db.Float, nullable=False, default=0.0)
        min_stock = db.Column(db.Float, nullable=False, default=5.0)
        created_at = db.Column(db.DateTime, default=now_cet)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

        recipe_items = db.relationship("MenuItemIngredient",
                                        foreign_keys="MenuItemIngredient.menu_item_id",
                                        backref="menu_item",
                                        cascade="all, delete-orphan", lazy="dynamic")

        @property
        def calculated_cost(self):
            total = 0.0
            for ri in self.recipe_items.all():
                total += ri.quantity * ri.unit_cost
            return round(total, 2)

    class MenuItemIngredient(db.Model):
        __tablename__ = "menu_item_ingredients"

        id = db.Column(db.Integer, primary_key=True)
        menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), nullable=False)
        ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=True)
        sub_menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), nullable=True)
        quantity = db.Column(db.Float, nullable=False, default=1.0)

        ingredient = db.relationship("Ingredient")
        sub_menu_item = db.relationship("MenuItem", foreign_keys=[sub_menu_item_id])

        @property
        def display_name(self):
            if self.sub_menu_item_id and self.sub_menu_item:
                return self.sub_menu_item.name
            if self.ingredient:
                return self.ingredient.name
            return "?"

        @property
        def display_unit(self):
            if self.sub_menu_item_id:
                return "db"
            if self.ingredient:
                return self.ingredient.unit
            return ""

        @property
        def unit_cost(self):
            if self.sub_menu_item_id and self.sub_menu_item:
                return self.sub_menu_item.production_cost
            if self.ingredient:
                return self.ingredient.price_per_unit
            return 0.0

    class CompanyDiscount(db.Model):
        __tablename__ = "company_discounts"

        id = db.Column(db.Integer, primary_key=True)
        company_id = db.Column(db.Integer, db.ForeignKey("delivery_companies.id"), nullable=False)
        category = db.Column(db.String(32), nullable=False)
        discount_percent = db.Column(db.Float, nullable=False, default=0.0)

        company = db.relationship("DeliveryCompany", backref="discounts")

        __table_args__ = (
            db.UniqueConstraint("company_id", "category", name="uq_company_category_discount"),
        )

    class Partner(db.Model):
        __tablename__ = "partners"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(256), nullable=False)
        slug = db.Column(db.String(256), nullable=False, unique=True)
        short_description = db.Column(db.String(512), nullable=True)
        description = db.Column(db.Text, nullable=True)
        price_list = db.Column(db.Text, nullable=True)
        logo_path = db.Column(db.String(512), nullable=True)
        sort_order = db.Column(db.Integer, nullable=False, default=0)
        created_at = db.Column(db.DateTime, default=now_cet)

        images = db.relationship("PartnerImage", backref="partner",
                                  cascade="all, delete-orphan", lazy="dynamic",
                                  order_by="PartnerImage.sort_order")

    class PartnerImage(db.Model):
        __tablename__ = "partner_images"

        id = db.Column(db.Integer, primary_key=True)
        partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"), nullable=False)
        image_path = db.Column(db.String(512), nullable=False)
        caption = db.Column(db.String(256), nullable=True)
        sort_order = db.Column(db.Integer, nullable=False, default=0)

    class StockMovement(db.Model):
        __tablename__ = "stock_movements"

        id = db.Column(db.Integer, primary_key=True)
        item_type = db.Column(db.String(32), nullable=False)
        item_id = db.Column(db.Integer, nullable=False)
        quantity = db.Column(db.Float, nullable=False)
        reason = db.Column(db.String(256), nullable=True)
        data_json = db.Column(db.Text, nullable=True)  # Structured JSON data for preorders etc.
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=now_cet)

        user = db.relationship("User")

    class GuestBookEntry(db.Model):
        __tablename__ = "guest_book_entries"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        message = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=now_cet)
        likes = db.Column(db.Integer, nullable=False, default=0)

        author = db.relationship("User", backref="guest_book_entries")

    class GuestBookLike(db.Model):
        __tablename__ = "guest_book_likes"

        id = db.Column(db.Integer, primary_key=True)
        entry_id = db.Column(db.Integer, db.ForeignKey("guest_book_entries.id"), nullable=False)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        created_at = db.Column(db.DateTime, default=now_cet)

        __table_args__ = (
            db.UniqueConstraint("entry_id", "user_id", name="uq_guestbook_like"),
        )

    class RatingComment(db.Model):
        __tablename__ = "rating_comments"

        id = db.Column(db.Integer, primary_key=True)
        reviewer_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        comment_type = db.Column(db.String(16), nullable=False)
        content = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=now_cet)

        reviewer = db.relationship("User", foreign_keys=[reviewer_user_id])
        target = db.relationship("User", foreign_keys=[target_user_id])

    class Event(db.Model):
        __tablename__ = "events"

        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(256), nullable=False)
        description = db.Column(db.Text, nullable=True)
        event_date = db.Column(db.Date, nullable=False)
        event_time = db.Column(db.String(16), nullable=True)
        event_type = db.Column(db.String(32), nullable=False, default="public")
        is_published = db.Column(db.Boolean, default=True)
        created_at = db.Column(db.DateTime, default=now_cet)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

        creator = db.relationship("User", backref="created_events")

    class Booking(db.Model):
        __tablename__ = "bookings"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=True)
        booking_date = db.Column(db.Date, nullable=False)
        booking_time = db.Column(db.String(16), nullable=True)
        guest_count = db.Column(db.Integer, nullable=False, default=1)
        event_type_label = db.Column(db.String(64), nullable=True)
        contact_name = db.Column(db.String(128), nullable=False)
        contact_phone = db.Column(db.String(32), nullable=True)
        note = db.Column(db.Text, nullable=True)
        status = db.Column(db.String(32), nullable=False, default="pending")
        created_at = db.Column(db.DateTime, default=now_cet)

        user = db.relationship("User", backref="bookings")
        event = db.relationship("Event", backref="bookings")
        messages = db.relationship("BookingMessage", backref="booking",
                                    cascade="all, delete-orphan", lazy="dynamic",
                                    order_by="BookingMessage.created_at.asc()")

    class BookingMessage(db.Model):
        __tablename__ = "booking_messages"

        id = db.Column(db.Integer, primary_key=True)
        booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        content = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, default=now_cet)

        author = db.relationship("User")

    class BonusConfig(db.Model):
        """Singleton-style config for bonus rates."""
        __tablename__ = "bonus_config"

        id = db.Column(db.Integer, primary_key=True)
        alc_percent = db.Column(db.Float, nullable=False, default=10.0)
        non_alc_percent = db.Column(db.Float, nullable=False, default=5.0)
        food_percent = db.Column(db.Float, nullable=False, default=5.0)
        per_minute_bonus = db.Column(db.Float, nullable=False, default=0.0)

    class BonusEntry(db.Model):
        """Individual bonus record for a user."""
        __tablename__ = "bonus_entries"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        amount = db.Column(db.Float, nullable=False, default=0.0)
        reason = db.Column(db.String(256), nullable=False)
        bonus_type = db.Column(db.String(32), nullable=False, default="feliras")
        created_at = db.Column(db.DateTime, default=now_cet)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

        user = db.relationship("User", foreign_keys=[user_id], backref="bonus_entries")

    return {
        "User": User,
        "Rating": Rating,
        "WorkLog": WorkLog,
        "Due": Due,
        "Advertisement": Advertisement,
        "DeliveryCompany": DeliveryCompany,
        "DeliveryMessage": DeliveryMessage,
        "Contract": Contract,
        "Rank": Rank,
        "Ingredient": Ingredient,
        "MenuItem": MenuItem,
        "MenuItemIngredient": MenuItemIngredient,
        "CompanyDiscount": CompanyDiscount,
        "Partner": Partner,
        "PartnerImage": PartnerImage,
        "StockMovement": StockMovement,
        "GuestBookEntry": GuestBookEntry,
        "GuestBookLike": GuestBookLike,
        "RatingComment": RatingComment,
        "Event": Event,
        "Booking": Booking,
        "BookingMessage": BookingMessage,
        "BonusConfig": BonusConfig,
        "BonusEntry": BonusEntry,
    }
