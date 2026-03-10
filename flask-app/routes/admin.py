"""Admin routes — dashboard, user management, inventory, reports, etc."""

import os
import json
from datetime import date, datetime, timedelta
from flask import (
    render_template, redirect, url_for, request, flash
)
from flask_login import current_user
from werkzeug.utils import secure_filename
from helpers import now_cet, admin_required, allowed_file, period_range


def admin_post_handler(db, models, app):
    """Shared POST handler for all admin sub-routes."""
    User = models["User"]
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
    WorkLog = models["WorkLog"]
    Event = models["Event"]
    Booking = models["Booking"]

    form_type = request.form.get("form_type")

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

    elif form_type == "edit_due":
        due_id = request.form.get("due_id", type=int)
        due = db.session.get(Due, due_id)
        if due:
            new_name = request.form.get("due_name", "").strip()
            new_amount = request.form.get("due_amount", type=float)
            new_date_str = request.form.get("due_date", "")
            if new_name:
                due.name = new_name
            if new_amount is not None:
                due.amount = new_amount
            if new_date_str:
                try:
                    due.due_date = date.fromisoformat(new_date_str)
                except ValueError:
                    pass
            new_company_id = request.form.get("due_company_id", "")
            if new_company_id == "":
                due.company_id = None
            else:
                try:
                    due.company_id = int(new_company_id)
                except (ValueError, TypeError):
                    pass
            db.session.commit()
            flash("Tartozás módosítva.", "success")

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
            due.paid_at = now_cet()
            db.session.commit()
            flash("Tartozás rendezve.", "success")

    elif form_type == "settle_company_dues":
        company_id = request.form.get("company_id", type=int)
        if company_id:
            unpaid = Due.query.filter_by(company_id=company_id, is_paid=False).all()
            for due in unpaid:
                due.is_paid = True
                due.paid_at = now_cet()
            db.session.commit()
            flash(f"{len(unpaid)} tartozás rendezve.", "success")

    elif form_type == "monthly_close_company":
        company_id = request.form.get("company_id", type=int)
        if company_id:
            now = now_cet()
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
                due.paid_at = now_cet()
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
            fname = secure_filename(f"contract_{now_cet().timestamp()}_{file.filename}")
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
        weight = request.form.get("ing_weight", type=float, default=0.0)
        if name and price is not None:
            db.session.add(Ingredient(name=name, unit=unit, price_per_unit=price, weight_per_unit_gram=weight or 0.0))
            db.session.commit()
            flash("Ingredient added.", "success")

    elif form_type == "update_weight":
        ing_id = request.form.get("ing_id", type=int)
        ing = db.session.get(Ingredient, ing_id)
        if ing:
            w = request.form.get("weight_gram", type=float, default=0.0)
            ing.weight_per_unit_gram = w if w else 0.0
            db.session.commit()
            flash(f"Súly frissítve: {ing.name} → {ing.weight_per_unit_gram:.1f} g", "success")

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
            fname = secure_filename(f"menu_{now_cet().timestamp()}_{file.filename}")
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
                fname = secure_filename(f"menu_{now_cet().timestamp()}_{file.filename}")
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
                    return
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
            fname = secure_filename(f"partner_{now_cet().timestamp()}_{file.filename}")
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
                fname = secure_filename(f"partner_{now_cet().timestamp()}_{file.filename}")
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
            fname = secure_filename(f"partner_img_{now_cet().timestamp()}_{file.filename}")
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


def register_admin_routes(app, db, models):
    User = models["User"]
    Due = models["Due"]
    WorkLog = models["WorkLog"]
    Ingredient = models["Ingredient"]
    MenuItem = models["MenuItem"]
    DeliveryCompany = models["DeliveryCompany"]
    CompanyDiscount = models["CompanyDiscount"]
    Advertisement = models["Advertisement"]
    Contract = models["Contract"]
    Rank = models["Rank"]
    Partner = models["Partner"]
    StockMovement = models["StockMovement"]
    GuestBookEntry = models["GuestBookEntry"]
    GuestBookLike = models["GuestBookLike"]
    RatingComment = models["RatingComment"]
    Event = models["Event"]
    Booking = models["Booking"]
    BonusConfig = models["BonusConfig"]
    BonusEntry = models["BonusEntry"]

    def _do_post():
        admin_post_handler(db, models, app)

    # ── Dashboard ──

    @app.route("/admin")
    @admin_required
    def admin():
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

    # ── Bonuses (Combined: Felírás + Idő) ──

    @app.route("/admin/bonuses", methods=["GET", "POST"])
    @admin_required
    def admin_bonuses():
        if request.method == "POST":
            form_type = request.form.get("form_type", "")

            if form_type == "update_config":
                cfg = BonusConfig.query.first()
                if not cfg:
                    cfg = BonusConfig()
                    db.session.add(cfg)
                cfg.alc_percent = request.form.get("alc_percent", type=float, default=10.0)
                cfg.non_alc_percent = request.form.get("non_alc_percent", type=float, default=5.0)
                cfg.food_percent = request.form.get("food_percent", type=float, default=5.0)
                cfg.per_minute_bonus = request.form.get("per_minute_bonus", type=float, default=0.0)
                db.session.commit()
                flash("Bónusz beállítások frissítve.", "success")

            elif form_type == "add_bonus":
                uid = request.form.get("bonus_user_id", type=int)
                amount = request.form.get("amount", type=float)
                reason = request.form.get("reason", "Manuális bónusz").strip()
                if uid and amount:
                    db.session.add(BonusEntry(
                        user_id=uid, amount=abs(amount), reason=reason,
                        bonus_type="manual", created_by=current_user.id
                    ))
                    db.session.commit()
                    flash(f"Bónusz hozzáadva: {'%.0f' % abs(amount)} Ft", "success")

            elif form_type == "withdraw":
                uid = request.form.get("bonus_user_id", type=int)
                amount = request.form.get("amount", type=float)
                reason = request.form.get("reason", "Bónusz kifizetés").strip()
                if uid and amount:
                    db.session.add(BonusEntry(
                        user_id=uid, amount=-abs(amount), reason=reason,
                        bonus_type="withdrawal", created_by=current_user.id
                    ))
                    db.session.commit()
                    flash(f"Kivétel rögzítve: {'%.0f' % abs(amount)} Ft", "success")

            elif form_type == "delete_entry":
                eid = request.form.get("entry_id", type=int)
                entry = db.session.get(BonusEntry, eid)
                if entry:
                    db.session.delete(entry)
                    db.session.commit()
                    flash("Bónusz bejegyzés törölve.", "success")

            elif form_type == "time_deduction":
                uid = request.form.get("user_id", type=int)
                amount = request.form.get("amount", type=float)
                reason = request.form.get("reason", "Idő bónusz levonás").strip()
                if uid and amount:
                    db.session.add(BonusEntry(
                        user_id=uid, amount=-abs(amount), reason=reason,
                        bonus_type="time_deduction", created_by=current_user.id
                    ))
                    db.session.commit()
                    flash(f"Idő bónusz levonás rögzítve: {'%.0f' % abs(amount)} Ft", "success")

            elif form_type == "time_addition":
                uid = request.form.get("user_id", type=int)
                amount = request.form.get("amount", type=float)
                reason = request.form.get("reason", "Idő bónusz hozzáadás").strip()
                if uid and amount:
                    db.session.add(BonusEntry(
                        user_id=uid, amount=abs(amount), reason=reason,
                        bonus_type="time_addition", created_by=current_user.id
                    ))
                    db.session.commit()
                    flash(f"Idő bónusz hozzáadva: {'%.0f' % abs(amount)} Ft", "success")

            active_tab = "time" if form_type in ("time_addition", "time_deduction") else ""
            return redirect(url_for("admin_bonuses",
                                     period=request.form.get("period", "month"),
                                     offset=request.args.get("offset", 0),
                                     user_id=request.form.get("user_id", ""),
                                     tab=active_tab))

        # GET
        period = request.args.get("period", "month")
        offset = request.args.get("offset", 0, type=int)
        start, end, period_label = period_range(period, offset)
        active_tab = request.args.get("tab", "")

        bonus_cfg = BonusConfig.query.first()
        per_minute_rate = bonus_cfg.per_minute_bonus if bonus_cfg else 0.0

        fraction_members = User.query.filter_by(has_fraction_permission=True).order_by(User.username).all()

        # ── Felírás tab data ──
        period_feliras_total = 0.0
        period_withdrawal_total = 0.0
        period_manual_total = 0.0
        user_balances = []
        for u in fraction_members:
            entries = BonusEntry.query.filter_by(user_id=u.id).all()
            # Period-filtered for summary
            period_entries = [e for e in entries if e.created_at and start <= e.created_at < end]
            feliras_total = sum(e.amount for e in entries if e.bonus_type == "feliras")
            withdrawal_total = sum(e.amount for e in entries if e.bonus_type == "withdrawal")
            manual_total = sum(e.amount for e in entries if e.bonus_type == "manual")
            balance = sum(e.amount for e in entries)
            period_feliras_total += sum(e.amount for e in period_entries if e.bonus_type == "feliras")
            period_withdrawal_total += sum(e.amount for e in period_entries if e.bonus_type == "withdrawal")
            period_manual_total += sum(e.amount for e in period_entries if e.bonus_type == "manual")
            user_balances.append(type('obj', (object,), {
                'id': u.id, 'display_name': u.display_name, 'avatar_url': u.avatar_url,
                'feliras_total': feliras_total, 'withdrawal_total': withdrawal_total,
                'manual_total': manual_total, 'balance': balance
            })())

        selected_user = None
        user_entries = []
        sel_id = request.args.get("user_id", type=int)
        if sel_id:
            selected_user = db.session.get(User, sel_id)
            if selected_user:
                user_entries = BonusEntry.query.filter_by(user_id=sel_id).order_by(BonusEntry.created_at.desc()).all()

        # ── Time tab data ──
        time_user_stats = []
        grand_total_seconds = 0
        grand_total_bonus = 0.0
        for member in fraction_members:
            logs = WorkLog.query.filter(
                WorkLog.user_id == member.id,
                WorkLog.clock_in >= start,
                WorkLog.clock_in < end
            ).all()
            total_secs = sum(l.duration_seconds for l in logs)
            total_minutes = total_secs / 60
            calculated_bonus = round(total_minutes * per_minute_rate, 2)
            time_adjustments = BonusEntry.query.filter(
                BonusEntry.user_id == member.id,
                BonusEntry.bonus_type.in_(["time_deduction", "time_addition"]),
                BonusEntry.created_at >= start,
                BonusEntry.created_at < end
            ).all()
            adjustment_total = sum(e.amount for e in time_adjustments)
            bonus = calculated_bonus + adjustment_total
            h, rem = divmod(int(total_secs), 3600)
            m, _ = divmod(rem, 60)
            time_user_stats.append({
                "user": member, "total_formatted": f"{h}h {m}m",
                "total_seconds": total_secs, "bonus": bonus,
                "calculated_bonus": calculated_bonus, "adjustment_total": adjustment_total,
            })
            grand_total_seconds += total_secs
            grand_total_bonus += bonus
        time_user_stats.sort(key=lambda x: x["total_seconds"], reverse=True)
        gh, grem = divmod(int(grand_total_seconds), 3600)
        gm, _ = divmod(grem, 60)

        return render_template("admin_bonuses_combined.html",
                               bonus_cfg=bonus_cfg, user_balances=user_balances,
                               selected_user=selected_user, user_entries=user_entries,
                               time_user_stats=time_user_stats,
                               per_minute_rate=per_minute_rate,
                               grand_total_formatted=f"{gh}h {gm}m",
                               grand_total_bonus=grand_total_bonus,
                               period=period, offset=offset, period_label=period_label,
                               period_feliras_total=period_feliras_total,
                               period_withdrawal_total=period_withdrawal_total,
                               period_manual_total=period_manual_total,
                               active_tab=active_tab)

    # ── Reviews ──

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

    @app.route("/admin/reviews/guestbook/<int:entry_id>/approve", methods=["POST"])
    @admin_required
    def admin_approve_guestbook(entry_id):
        entry = db.session.get(GuestBookEntry, entry_id)
        if entry:
            entry.is_approved = not entry.is_approved
            db.session.commit()
            status = "jóváhagyva" if entry.is_approved else "elrejtve"
            flash(f"Bejegyzés {status}.", "success")
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

    # ── Sub-routes (CRUD pages) ──

    @app.route("/admin/users", methods=["GET", "POST"])
    @admin_required
    def admin_users():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_users"))
        users = User.query.order_by(User.username).all()
        return render_template("admin_users.html", users=users)

    @app.route("/admin/ranks", methods=["GET", "POST"])
    @admin_required
    def admin_ranks():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_ranks"))
        ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
        return render_template("admin_ranks_page.html", ranks=ranks)

    @app.route("/admin/members", methods=["GET", "POST"])
    @admin_required
    def admin_members():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_members"))
        ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
        fraction_members = User.query.filter_by(has_fraction_permission=True).all()
        fraction_members.sort(key=lambda u: (u.rank.sort_order if u.rank else 9999, u.display_name))
        return render_template("admin_members.html", fraction_members=fraction_members, ranks=ranks)

    @app.route("/admin/dues", methods=["GET", "POST"])
    @admin_required
    def admin_dues():
        if request.method == "POST":
            _do_post()
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
        all_companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
        return render_template("admin_dues.html", grouped_dues=grouped_dues,
                               dues_no_company=dues_no_company, all_companies=all_companies)

    @app.route("/admin/ads", methods=["GET", "POST"])
    @admin_required
    def admin_ads():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_ads"))
        ads = Advertisement.query.order_by(Advertisement.created_at.desc()).all()
        return render_template("admin_ads.html", ads=ads)

    @app.route("/admin/companies", methods=["GET", "POST"])
    @admin_required
    def admin_companies():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_companies"))
        companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
        return render_template("admin_companies.html", companies=companies)

    @app.route("/admin/contracts", methods=["GET", "POST"])
    @admin_required
    def admin_contracts():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_contracts"))
        contracts = Contract.query.order_by(Contract.created_at.desc()).all()
        return render_template("admin_contracts_page.html", contracts=contracts)

    @app.route("/admin/worklogs", methods=["GET", "POST"])
    @admin_required
    def admin_worklogs():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_worklogs"))
        wh_period = request.args.get("wh_period", "day")
        offset = request.args.get("offset", 0, type=int)
        wh_start, wh_end, period_label = period_range(wh_period, offset)

        fraction_members = User.query.filter_by(has_fraction_permission=True).all()
        fraction_members.sort(key=lambda u: (u.rank.sort_order if u.rank else 9999, u.display_name))
        workhour_stats = []
        for member in fraction_members:
            logs = WorkLog.query.filter(
                WorkLog.user_id == member.id,
                WorkLog.clock_in >= wh_start,
                WorkLog.clock_in < wh_end
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
        return render_template("admin_worklogs.html", workhour_stats=workhour_stats,
                               wh_period=wh_period, offset=offset, period_label=period_label)

    @app.route("/admin/time-bonuses", methods=["GET", "POST"])
    @admin_required
    def admin_time_bonuses():
        if request.method == "POST":
            form_type = request.form.get("form_type", "")
            if form_type == "time_deduction":
                uid = request.form.get("user_id", type=int)
                amount = request.form.get("amount", type=float)
                reason = request.form.get("reason", "Idő bónusz levonás").strip()
                if uid and amount:
                    db.session.add(BonusEntry(
                        user_id=uid, amount=-abs(amount), reason=reason,
                        bonus_type="time_deduction", created_by=current_user.id
                    ))
                    db.session.commit()
                    flash(f"Idő bónusz levonás rögzítve: {'%.0f' % abs(amount)} Ft", "success")
            elif form_type == "time_addition":
                uid = request.form.get("user_id", type=int)
                amount = request.form.get("amount", type=float)
                reason = request.form.get("reason", "Idő bónusz hozzáadás").strip()
                if uid and amount:
                    db.session.add(BonusEntry(
                        user_id=uid, amount=abs(amount), reason=reason,
                        bonus_type="time_addition", created_by=current_user.id
                    ))
                    db.session.commit()
                    flash(f"Idő bónusz hozzáadva: {'%.0f' % abs(amount)} Ft", "success")
            return redirect(url_for("admin_time_bonuses", period=request.form.get("period", "month")))

        period = request.args.get("period", "month")
        offset = request.args.get("offset", 0, type=int)
        start, end, period_label = period_range(period, offset)

        bonus_cfg = BonusConfig.query.first()
        per_minute_rate = bonus_cfg.per_minute_bonus if bonus_cfg else 0.0

        fraction_members = User.query.filter_by(has_fraction_permission=True).all()
        user_stats = []
        grand_total_seconds = 0
        grand_total_bonus = 0.0

        for member in fraction_members:
            logs = WorkLog.query.filter(
                WorkLog.user_id == member.id,
                WorkLog.clock_in >= start,
                WorkLog.clock_in < end
            ).all()
            total_secs = sum(l.duration_seconds for l in logs)
            total_minutes = total_secs / 60
            calculated_bonus = round(total_minutes * per_minute_rate, 2)

            # Get manual time adjustments (additions/deductions)
            time_adjustments = BonusEntry.query.filter(
                BonusEntry.user_id == member.id,
                BonusEntry.bonus_type.in_(["time_deduction", "time_addition"]),
                BonusEntry.created_at >= start,
                BonusEntry.created_at < end
            ).all()
            adjustment_total = sum(e.amount for e in time_adjustments)

            bonus = calculated_bonus + adjustment_total
            h, rem = divmod(int(total_secs), 3600)
            m, _ = divmod(rem, 60)
            user_stats.append({
                "user": member,
                "total_formatted": f"{h}h {m}m",
                "total_seconds": total_secs,
                "bonus": bonus,
                "calculated_bonus": calculated_bonus,
                "adjustment_total": adjustment_total,
            })
            grand_total_seconds += total_secs
            grand_total_bonus += bonus

        user_stats.sort(key=lambda x: x["total_seconds"], reverse=True)
        gh, grem = divmod(int(grand_total_seconds), 3600)
        gm, _ = divmod(grem, 60)

        return render_template("admin_time_bonuses.html",
                               user_stats=user_stats, period=period,
                               per_minute_rate=per_minute_rate,
                               grand_total_formatted=f"{gh}h {gm}m",
                               grand_total_bonus=grand_total_bonus,
                               offset=offset, period_label=period_label)

    @app.route("/admin/ingredients", methods=["GET", "POST"])
    @admin_required
    def admin_ingredients():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_ingredients"))
        ingredients = Ingredient.query.order_by(Ingredient.name).all()
        return render_template("admin_ingredients.html", ingredients=ingredients)

    @app.route("/admin/menuitems", methods=["GET", "POST"])
    @admin_required
    def admin_menuitems():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_menuitems"))
        menu_items = MenuItem.query.order_by(MenuItem.category, MenuItem.name).all()
        ingredients = Ingredient.query.order_by(Ingredient.name).all()
        return render_template("admin_menuitems.html", menu_items=menu_items, ingredients=ingredients)

    @app.route("/admin/discounts", methods=["GET", "POST"])
    @admin_required
    def admin_discounts():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_discounts"))
        discounts = CompanyDiscount.query.all()
        companies = DeliveryCompany.query.order_by(DeliveryCompany.name).all()
        return render_template("admin_discounts.html", discounts=discounts, companies=companies)

    @app.route("/admin/partners", methods=["GET", "POST"])
    @admin_required
    def admin_partners_page():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_partners_page"))
        partners = Partner.query.order_by(Partner.sort_order).all()
        return render_template("admin_partners_page.html", partners=partners)

    @app.route("/admin/reports")
    @admin_required
    def admin_reports_page():
        now = now_cet()
        report_period = request.args.get("report_period", "week")
        offset = request.args.get("offset", 0, type=int)
        report_start, report_end, period_label = period_range(report_period, offset)
        num_days = (report_end - report_start).days

        report_dues = Due.query.filter(
            Due.created_at >= report_start,
            Due.created_at < report_end
        ).all()
        report_revenue = sum(d.amount for d in report_dues)
        report_revenue_count = len(report_dues)
        report_movements = StockMovement.query.filter(
            StockMovement.created_at >= report_start,
            StockMovement.created_at < report_end
        ).all()
        report_stock_in_count = sum(1 for m in report_movements if m.quantity > 0)
        report_stock_out_count = sum(1 for m in report_movements if m.quantity < 0)
        report_wlogs = WorkLog.query.filter(
            WorkLog.clock_in >= report_start,
            WorkLog.clock_in < report_end
        ).all()
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
                               report_chart_workhours=chart_workhours,
                               offset=offset, period_label=period_label)

    @app.route("/admin/events", methods=["GET", "POST"])
    @admin_required
    def admin_events_page():
        if request.method == "POST":
            _do_post()
            return redirect(url_for("admin_events_page"))
        events = Event.query.order_by(Event.event_date.desc()).all()
        all_bookings = Booking.query.order_by(Booking.created_at.desc()).all()
        pending_bookings_count = sum(1 for b in all_bookings if b.status == "pending")
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
