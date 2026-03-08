"""Public-facing routes — visitor, profile, ratings, guestbook, bookings, partners."""

import os
from datetime import date
from flask import (
    render_template, redirect, url_for, request, flash, abort
)
from flask_login import login_required, current_user
from helpers import now_cet, allowed_file


def register_visitor_routes(app, db, models):
    User = models["User"]
    Rating = models["Rating"]
    Partner = models["Partner"]
    GuestBookEntry = models["GuestBookEntry"]
    GuestBookLike = models["GuestBookLike"]
    RatingComment = models["RatingComment"]
    Event = models["Event"]
    Booking = models["Booking"]
    BookingMessage = models["BookingMessage"]

    # ── Visitor & Profile ──

    @app.route("/")
    def visitor():
        workers = User.query.filter_by(has_fraction_permission=True).all()
        workers.sort(key=lambda w: w.average_rating, reverse=True)
        partners = Partner.query.order_by(Partner.sort_order).all()
        guest_book = GuestBookEntry.query.filter_by(is_approved=True).order_by(GuestBookEntry.created_at.desc()).limit(50).all()
        top_entry = GuestBookEntry.query.filter(GuestBookEntry.is_approved == True, GuestBookEntry.likes > 0).order_by(GuestBookEntry.likes.desc()).first()
        user_likes = set()
        if hasattr(current_user, 'id') and current_user.is_authenticated:
            user_likes = {l.entry_id for l in GuestBookLike.query.filter_by(user_id=current_user.id).all()}
        worker_comments = {}
        for w in workers:
            worker_comments[w.id] = RatingComment.query.filter_by(target_user_id=w.id)\
                .order_by(RatingComment.created_at.desc()).limit(20).all()
        upcoming_events = Event.query.filter(
            Event.is_published == True,
            Event.event_date >= date.today()
        ).order_by(Event.event_date.asc()).all()
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

    # ── Ratings ──

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

    # ── Guestbook ──

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

    # ── Bookings & Events ──

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
        if booking.user_id != current_user.id and not current_user.is_admin and not current_user.has_fraction_permission:
            abort(403)
        messages = booking.messages.all()
        can_message = (booking.user_id == current_user.id or current_user.is_admin)
        return render_template("booking_detail.html", booking=booking, messages=messages, can_message=can_message)
