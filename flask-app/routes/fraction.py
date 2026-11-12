"""Fraction (borászat) routes — internal staff features."""

import json
from datetime import date, timedelta, datetime
import calendar as _cal
from flask import (
    render_template, redirect, url_for, request, flash, session, jsonify, abort
)
from flask_login import login_required, current_user
from helpers import now_cet, fraction_required, period_range, HU_MONTHS, HU_DAYS



def register_fraction_routes(app, db, models):
    User = models["User"]
    WorkLog = models["WorkLog"]
    Due = models["Due"]
    Advertisement = models["Advertisement"]
    DeliveryCompany = models["DeliveryCompany"]
    DeliveryMessage = models["DeliveryMessage"]
    Contract = models["Contract"]
    Rank = models["Rank"]
    Ingredient = models["Ingredient"]
    MenuItem = models["MenuItem"]
    CompanyDiscount = models["CompanyDiscount"]
    StockMovement = models["StockMovement"]
    Booking = models["Booking"]
    BonusConfig = models["BonusConfig"]
    BonusEntry = models["BonusEntry"]
    UserCardOrder = models["UserCardOrder"]
    PersonalEvent = models["PersonalEvent"]

    # Default card order for fraction main page
    DEFAULT_CARDS = [
        "members", "clock", "workhours", "dues", "calculator", "ads",
        "deliveries", "brewery", "contracts", "warehouse", "bookings",
        "calendar", "vince", "preorder"
    ]

    @app.route("/fraction")
    @fraction_required
    def fraction():
        import json as _json
        order_entry = UserCardOrder.query.filter_by(user_id=current_user.id).first()
        if order_entry:
            try:
                card_order = _json.loads(order_entry.card_order)
                for c in DEFAULT_CARDS:
                    if c not in card_order:
                        card_order.append(c)
                card_order = [c for c in card_order if c in DEFAULT_CARDS]
            except Exception:
                card_order = list(DEFAULT_CARDS)
        else:
            card_order = list(DEFAULT_CARDS)

        pending_bookings = Booking.query.filter_by(status="pending").count()
        today = now_cet().date()
        upcoming_personal = PersonalEvent.query.filter(
            PersonalEvent.event_date >= today,
            (PersonalEvent.user_id == current_user.id) | (PersonalEvent.is_shared == True)
        ).count()

        return render_template(
            "fraction.html",
            card_order=card_order,
            pending_bookings=pending_bookings,
            upcoming_personal=upcoming_personal,
        )


    @app.route("/fraction/save-card-order", methods=["POST"])
    @fraction_required
    def fraction_save_card_order():
        import json as _json
        data = request.get_json(silent=True)
        if not data or "order" not in data:
            return {"ok": False}, 400
        order = data["order"]
        # Validate
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            return {"ok": False}, 400
        entry = UserCardOrder.query.filter_by(user_id=current_user.id).first()
        if not entry:
            entry = UserCardOrder(user_id=current_user.id)
            db.session.add(entry)
        entry.card_order = _json.dumps(order)
        db.session.commit()
        return {"ok": True}

    @app.route("/fraction/members")
    @fraction_required
    def fraction_members():
        members = User.query.filter_by(has_fraction_permission=True).all()
        ranks = Rank.query.order_by(Rank.sort_order.asc()).all()
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
                    log.clock_out = now_cet()
                    db.session.commit()
                    flash(f"Clocked out. Duration: {log.duration_formatted}", "success")
                else:
                    flash("You are not clocked in.", "warning")
            return redirect(url_for("fraction_clock"))

        active_logs = WorkLog.query.filter_by(clock_out=None).all()
        clocked_in_users = [log.user for log in active_logs]
        return render_template("fraction_clock.html", clocked_in_users=clocked_in_users)

    @app.route("/fraction/workhours")
    @fraction_required
    def fraction_workhours():
        period = request.args.get("period", "day")
        offset = request.args.get("offset", 0, type=int)
        start, end, period_label = period_range(period, offset)

        logs = WorkLog.query.filter(
            WorkLog.user_id == current_user.id,
            WorkLog.clock_in >= start,
            WorkLog.clock_in < end
        ).order_by(WorkLog.clock_in.desc()).all()

        total_seconds = sum(l.duration_seconds for l in logs)
        h, remainder = divmod(int(total_seconds), 3600)
        m, s = divmod(remainder, 60)
        total_formatted = f"{h}h {m}m"

        bonus_cfg = BonusConfig.query.first()
        time_bonus = 0.0
        if bonus_cfg and bonus_cfg.per_minute_bonus > 0:
            total_minutes = total_seconds / 60
            time_bonus = round(total_minutes * bonus_cfg.per_minute_bonus, 2)

        # Time adjustments (additions/deductions by admin)
        time_adjustments = BonusEntry.query.filter(
            BonusEntry.user_id == current_user.id,
            BonusEntry.bonus_type.in_(["time_deduction", "time_addition"]),
            BonusEntry.created_at >= start,
            BonusEntry.created_at < end
        ).all()
        time_adjustment_total = sum(b.amount for b in time_adjustments)
        time_bonus_final = time_bonus + time_adjustment_total

        feliras_bonuses = BonusEntry.query.filter(
            BonusEntry.user_id == current_user.id,
            BonusEntry.bonus_type == "feliras",
            BonusEntry.created_at >= start,
            BonusEntry.created_at < end
        ).all()
        feliras_bonus_total = sum(b.amount for b in feliras_bonuses)

        all_bonuses = BonusEntry.query.filter_by(user_id=current_user.id).all()
        total_balance = sum(b.amount for b in all_bonuses) + time_bonus

        return render_template("fraction_workhours.html",
                               logs=logs, period=period, total_formatted=total_formatted,
                               time_bonus=time_bonus, time_bonus_final=time_bonus_final,
                               time_adjustment_total=time_adjustment_total,
                               feliras_bonus_total=feliras_bonus_total,
                               total_balance=total_balance, bonus_cfg=bonus_cfg,
                               feliras_bonuses=feliras_bonuses,
                               time_adjustments=time_adjustments,
                               offset=offset, period_label=period_label)

    @app.route("/fraction/dues", methods=["GET", "POST"])
    @fraction_required
    def fraction_dues():
        if request.method == "POST":
            from routes.admin import admin_post_handler
            admin_post_handler(db, models, app)
            return redirect(url_for("fraction_dues"))
        from collections import defaultdict
        all_dues = Due.query.order_by(Due.created_at.desc().nullslast(), Due.id.desc()).all()
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
        return render_template("fraction_dues.html", grouped_dues=grouped_dues,
                               dues_no_company=dues_no_company, all_companies=all_companies)

    def resolve_production(cart_rows):
        """Recursively resolve production needs.
        Sub-items are taken from warehouse stock first; any shortfall is crafted
        on the spot, i.e. its recipe is unpacked into raw ingredients + time."""
        raw, used, crafted = {}, {}, {}
        temp_stock = {}
        state = {"time": 0}

        def process(mi, qty, depth=0):
            if depth > 20 or qty <= 0:
                return
            state["time"] += (mi.production_time_seconds or 0) * qty
            for ri in mi.recipe_items.all():
                need = (ri.quantity or 0) * qty
                if ri.sub_menu_item_id and ri.sub_menu_item:
                    sub = ri.sub_menu_item
                    temp_stock.setdefault(sub.id, max(0.0, float(sub.stock or 0)))
                    take = min(temp_stock[sub.id], need)
                    short = need - take
                    if take > 0:
                        temp_stock[sub.id] -= take
                        u = used.setdefault(sub.id, {"id": sub.id, "name": sub.name, "qty": 0.0})
                        u["qty"] += take
                    if short > 0:
                        cr = crafted.setdefault(sub.id, {"id": sub.id, "name": sub.name, "qty": 0.0})
                        cr["qty"] += short
                        process(sub, short, depth + 1)
                elif ri.ingredient:
                    ing = ri.ingredient
                    r = raw.setdefault(ing.id, {"id": ing.id, "name": ing.name, "unit": ing.unit,
                                                "unit_price": ing.price_per_unit or 0, "qty": 0.0,
                                                "stock": ing.stock or 0})
                    r["qty"] += need

        for item_id, qty in cart_rows:
            mi = db.session.get(MenuItem, item_id)
            if mi:
                process(mi, qty)
        total = 0.0
        for r in raw.values():
            r["cost"] = r["qty"] * r["unit_price"]
            total += r["cost"]
        return {"raw_ingredients": list(raw.values()), "stock_used": list(used.values()),
                "crafted": list(crafted.values()), "total_time": state["time"], "total_cost": total}

    @app.route("/fraction/calculator")
    @fraction_required
    def fraction_calculator():
        step = request.args.get("step", "categories")
        cat = request.args.get("cat", "")
        item_id = request.args.get("item_id", type=int)
        mode = request.args.get("mode", "")
        company_id = request.args.get("company_id", type=int)

        if "pos_cart" not in session:
            session["pos_cart"] = []

        remove_idx = request.args.get("remove", type=int)
        if remove_idx is not None:
            cart = session.get("pos_cart", [])
            if 0 <= remove_idx < len(cart):
                cart.pop(remove_idx)
                session["pos_cart"] = cart
            return redirect(url_for("fraction_calculator"))

        if request.args.get("clear"):
            session["pos_cart"] = []
            return redirect(url_for("fraction_calculator"))

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
                res = resolve_production(cart_rows=[(i["item_id"], i["qty"]) for i in cart])
                ctx["production_ingredients"] = res["raw_ingredients"]
                ctx["production_stock_used"] = res["stock_used"]
                ctx["production_crafted"] = res["crafted"]
                ctx["production_time"] = res["total_time"]
                ctx["production_cost"] = res["total_cost"]

        return render_template("fraction_calculator.html", **ctx)

    @app.route("/fraction/calculator/add", methods=["POST"])
    @fraction_required
    def fraction_calculator_add():
        item_id = request.form.get("item_id", type=int)
        qty = request.form.get("qty", type=int, default=1)
        if item_id and qty and qty > 0:
            cart = session.get("pos_cart", [])
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
        mode = request.form.get("mode", "")
        cart = session.get("pos_cart", [])

        if not cart:
            flash("A kosár üres.", "warning")
            return redirect(url_for("fraction_calculator"))

        if mode in ("basic", "discount"):
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
            label = "POS eladás (alapár)" if mode == "basic" else "POS eladás (kedvezményes)"
            due = Due(
                name=label,
                amount=sale_total,
                due_date=date.today(),
                is_paid=True,
                paid_at=now_cet(),
                created_by=current_user.id,
            )
            db.session.add(due)
            db.session.commit()
            session["pos_cart"] = []
            flash("Eladás rögzítve, raktár frissítve.", "success")

        elif mode == "production":
            res = resolve_production(cart_rows=[(c["id"], c["qty"]) for c in cart])
            names = []
            for c in cart:
                mi = db.session.get(MenuItem, c["id"])
                if mi:
                    names.append(mi.name)
                    mi.stock += c["qty"]
                    db.session.add(StockMovement(
                        item_type="menu_item", item_id=mi.id, quantity=c["qty"],
                        reason="POS gyártás", user_id=current_user.id))
            label = ", ".join(names)
            for u in res["stock_used"]:
                sub = db.session.get(MenuItem, u["id"])
                if sub:
                    sub.stock = max(0, (sub.stock or 0) - u["qty"])
                    db.session.add(StockMovement(
                        item_type="menu_item", item_id=sub.id, quantity=-u["qty"],
                        reason=f"POS gyártás (raktárból felhasznált altétel): {label}",
                        user_id=current_user.id))
            for ing_row in res["raw_ingredients"]:
                ing = db.session.get(Ingredient, ing_row["id"])
                if ing:
                    ing.stock = max(0, (ing.stock or 0) - ing_row["qty"])
                    db.session.add(StockMovement(
                        item_type="ingredient", item_id=ing.id, quantity=-ing_row["qty"],
                        reason=f"POS gyártás: {label}", user_id=current_user.id))
            db.session.commit()
            session["pos_cart"] = []
            flash("Gyártás rögzítve, raktár frissítve.", "success")

        return redirect(url_for("fraction_calculator"))

    @app.route("/fraction/calculator/record_due", methods=["POST"])
    @fraction_required
    def fraction_calculator_record_due():
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
                cart = session.get("pos_cart", [])
                bonus_cfg = BonusConfig.query.first()
                bonus_amount = 0.0
                # Load company discounts so bonus is calculated on discounted price
                company_discounts = {d.category: d.discount_percent
                                     for d in CompanyDiscount.query.filter_by(company_id=company_id).all()}
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
                        if bonus_cfg:
                            line_total = mi.price * c["qty"]
                            # Apply company discount to get the real price
                            cat_discount = company_discounts.get(mi.category, 0.0)
                            if cat_discount > 0:
                                line_total = line_total * (1 - cat_discount / 100)
                            if mi.category == "alc":
                                bonus_amount += line_total * (bonus_cfg.alc_percent / 100)
                            else:
                                pct = bonus_cfg.food_percent if mi.category == "food" else bonus_cfg.non_alc_percent
                                bonus_amount += line_total * (pct / 100)
                if bonus_amount > 0:
                    db.session.add(BonusEntry(
                        user_id=current_user.id,
                        amount=round(bonus_amount, 2),
                        reason=f"Felírás bónusz — {company.name}",
                        bonus_type="feliras",
                        created_by=current_user.id
                    ))
                db.session.commit()
                session["pos_cart"] = []
                bonus_msg = f" (+{'%.0f' % bonus_amount} Ft bónusz)" if bonus_amount > 0 else ""
                flash(f"Tartozás felírva + raktár frissítve: {company.name} — {'%.0f' % discount_total} Ft{bonus_msg}", "success")
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

    @app.route("/fraction/vince", methods=["GET", "POST"])
    @fraction_required
    def fraction_vince():
        menu_items = MenuItem.query.order_by(MenuItem.category, MenuItem.name).all()
        if request.method == "POST":
            sale_total = 0.0
            items_sold = []
            for mi in menu_items:
                qty = request.form.get(f"qty_{mi.id}", 0, type=int)
                if qty and qty > 0:
                    mi.stock = max(0, mi.stock - qty)
                    sale_total += mi.price * qty
                    items_sold.append(f"{qty}x {mi.name}")
                    db.session.add(StockMovement(
                        item_type="menu_item", item_id=mi.id,
                        quantity=-qty,
                        reason="Vince feltöltése",
                        user_id=current_user.id
                    ))
            if items_sold:
                due = Due(
                    name="Vince feltöltése",
                    amount=sale_total,
                    due_date=date.today(),
                    is_paid=True,
                    paid_at=now_cet(),
                    created_by=current_user.id,
                )
                db.session.add(due)
                db.session.commit()
                flash(f"Vince feltöltve: {', '.join(items_sold)} — {sale_total:.0f} Ft", "success")
            else:
                flash("Nem adtál meg mennyiséget.", "warning")
            return redirect(url_for("fraction_vince"))

        vince_logs_raw = StockMovement.query.filter_by(reason="Vince feltöltése") \
            .order_by(StockMovement.created_at.desc()).limit(200).all()
        vince_log_entries = []
        for sm in vince_logs_raw:
            mi = db.session.get(MenuItem, sm.item_id)
            vince_log_entries.append({
                "user": sm.user,
                "item_name": mi.name if mi else "?",
                "quantity": abs(sm.quantity),
                "created_at": sm.created_at,
            })
        return render_template("fraction_vince.html", menu_items=menu_items, vince_logs=vince_log_entries)

    @app.route("/fraction/preorder")
    @fraction_required
    def fraction_preorder():
        """Előrendelés – kalkulátor (csak kliens oldali számolás)."""
        ingredients = Ingredient.query.order_by(Ingredient.name).all()

        return render_template("fraction_preorder.html", ingredients=ingredients)

    @app.route("/fraction/warehouse", methods=["GET", "POST"])
    @login_required
    @fraction_required
    def fraction_warehouse():
        if request.method == "POST":
            form_type = request.form.get("form_type")
            item_type = request.form.get("item_type", "")
            item_id = request.form.get("item_id", type=int)
            quantity = request.form.get("quantity", type=float)
            reason = request.form.get("reason", "").strip()

            if form_type == "bulk_stock_set":
                bulk_reason = request.form.get("bulk_reason", "").strip() or "Tömeges leltár"
                updated = 0
                for ing in Ingredient.query.all():
                    key = f"stock_ing_{ing.id}"
                    val = request.form.get(key, "").strip()
                    if val == "":
                        continue
                    try:
                        new_s = max(0.0, float(val))
                    except ValueError:
                        continue
                    if abs(new_s - ing.stock) < 0.001:
                        continue
                    delta = new_s - ing.stock
                    ing.stock = new_s
                    db.session.add(StockMovement(
                        item_type="ingredient", item_id=ing.id,
                        quantity=delta, reason=bulk_reason,
                        user_id=current_user.id
                    ))
                    updated += 1
                for mi in MenuItem.query.all():
                    key = f"stock_mi_{mi.id}"
                    val = request.form.get(key, "").strip()
                    if val == "":
                        continue
                    try:
                        new_s = max(0.0, float(val))
                    except ValueError:
                        continue
                    if abs(new_s - mi.stock) < 0.001:
                        continue
                    delta = new_s - mi.stock
                    mi.stock = new_s
                    db.session.add(StockMovement(
                        item_type="menu_item", item_id=mi.id,
                        quantity=delta, reason=bulk_reason,
                        user_id=current_user.id
                    ))
                    updated += 1
                if updated:
                    db.session.commit()
                    flash(f"{updated} tétel leltára frissítve.", "success")
                else:
                    flash("Nem történt változás.", "info")
                return redirect(url_for("fraction_warehouse"))


            if item_id and quantity and quantity != 0:
                if form_type == "stock_add":
                    qty = abs(quantity)
                elif form_type == "stock_remove":
                    qty = -abs(quantity)
                elif form_type == "stock_set":
                    pass
                else:
                    flash("Érvénytelen művelet.", "danger")
                    return redirect(url_for("fraction_warehouse"))

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

        ingredients = Ingredient.query.order_by(Ingredient.name).all()
        menu_items = MenuItem.query.order_by(MenuItem.category, MenuItem.name).all()
        recent_movements = StockMovement.query.order_by(
            StockMovement.created_at.desc()).limit(50).all()

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

    @app.route("/fraction/bookings")
    @fraction_required
    def fraction_bookings():
        bookings = Booking.query.order_by(Booking.booking_date.desc()).all()
        return render_template("fraction_bookings.html", bookings=bookings)

    # ── Calendar / Planner ──

    @app.route("/fraction/calendar")
    @fraction_required
    def fraction_calendar():
        try:
            year = int(request.args.get("year", now_cet().year))
            month = int(request.args.get("month", now_cet().month))
        except ValueError:
            year, month = now_cet().year, now_cet().month
        if month < 1:
            month = 12; year -= 1
        if month > 12:
            month = 1; year += 1

        first_day = date(year, month, 1)
        _, days_in_month = _cal.monthrange(year, month)
        last_day = date(year, month, days_in_month)

        # Personal events (own + shared) in this month
        p_events = PersonalEvent.query.filter(
            PersonalEvent.event_date >= first_day,
            PersonalEvent.event_date <= last_day,
            (PersonalEvent.user_id == current_user.id) | (PersonalEvent.is_shared == True)
        ).order_by(PersonalEvent.event_date, PersonalEvent.event_time).all()

        # Confirmed bookings appear too (for context)
        confirmed = Booking.query.filter(
            Booking.booking_date >= first_day,
            Booking.booking_date <= last_day,
        ).all()

        # Build events-by-day map
        events_by_day = {}
        for e in p_events:
            events_by_day.setdefault(e.event_date.day, []).append({
                "type": "personal",
                "id": e.id,
                "title": e.title,
                "time": e.event_time or "",
                "color": e.color or "#7c5cfc",
                "owner": e.user.display_name if e.user else "",
                "shared": e.is_shared,
                "own": e.user_id == current_user.id,
                "description": e.description or "",
            })
        for b in confirmed:
            events_by_day.setdefault(b.booking_date.day, []).append({
                "type": "booking",
                "id": b.id,
                "title": f"Foglalás: {b.contact_name}",
                "time": b.booking_time or "",
                "color": "#e74c3c" if b.status == "pending" else "#2ecc71",
                "status": b.status,
                "guest_count": b.guest_count,
            })

        # Calendar grid (Mon-start)
        _cal.setfirstweekday(_cal.MONDAY)
        weeks = _cal.monthcalendar(year, month)

        prev_month = month - 1 or 12
        prev_year = year - 1 if month == 1 else year
        next_month = month + 1 if month < 12 else 1
        next_year = year + 1 if month == 12 else year

        return render_template(
            "fraction_calendar.html",
            year=year, month=month, month_label=HU_MONTHS[month],
            weeks=weeks, events_by_day=events_by_day,
            today=now_cet().date(),
            prev_year=prev_year, prev_month=prev_month,
            next_year=next_year, next_month=next_month,
            hu_days=HU_DAYS,
            upcoming=[e for e in p_events if e.event_date >= now_cet().date()][:20],
        )

    @app.route("/fraction/calendar/add", methods=["POST"])
    @fraction_required
    def fraction_calendar_add():
        title = request.form.get("title", "").strip()
        event_date_str = request.form.get("event_date", "").strip()
        event_time = request.form.get("event_time", "").strip()
        description = request.form.get("description", "").strip()
        color = request.form.get("color", "#7c5cfc").strip() or "#7c5cfc"
        is_shared = bool(request.form.get("is_shared"))

        if not title or not event_date_str:
            flash("A cím és a dátum kötelező.", "warning")
            return redirect(url_for("fraction_calendar"))
        try:
            ev_date = date.fromisoformat(event_date_str)
        except ValueError:
            flash("Érvénytelen dátum.", "danger")
            return redirect(url_for("fraction_calendar"))

        ev = PersonalEvent(
            user_id=current_user.id,
            title=title[:160],
            description=description[:1000] or None,
            event_date=ev_date,
            event_time=event_time or None,
            color=color[:16],
            is_shared=is_shared,
        )
        db.session.add(ev)
        db.session.commit()
        flash("Esemény hozzáadva a naptárhoz.", "success")
        return redirect(url_for("fraction_calendar", year=ev_date.year, month=ev_date.month))

    @app.route("/fraction/calendar/<int:event_id>/delete", methods=["POST"])
    @fraction_required
    def fraction_calendar_delete(event_id):
        ev = db.session.get(PersonalEvent, event_id)
        if not ev:
            abort(404)
        if ev.user_id != current_user.id and not current_user.is_admin:
            abort(403)
        y, m = ev.event_date.year, ev.event_date.month
        db.session.delete(ev)
        db.session.commit()
        flash("Esemény törölve.", "success")
        return redirect(url_for("fraction_calendar", year=y, month=m))

