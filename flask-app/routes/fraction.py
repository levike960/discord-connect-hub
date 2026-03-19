"""Fraction (borászat) routes — internal staff features."""

import json
from datetime import date, timedelta
from flask import (
    render_template, redirect, url_for, request, flash, session
)
from flask_login import login_required, current_user
from helpers import now_cet, fraction_required, period_range


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

    # Default card order for fraction main page
    DEFAULT_CARDS = [
        "members", "clock", "workhours", "dues", "calculator", "ads",
        "deliveries", "brewery", "contracts", "warehouse", "bookings",
        "vince", "preorder"
    ]

    @app.route("/fraction")
    @fraction_required
    def fraction():
        import json as _json
        order_entry = UserCardOrder.query.filter_by(user_id=current_user.id).first()
        if order_entry:
            try:
                card_order = _json.loads(order_entry.card_order)
                # Add any new cards that weren't in saved order
                for c in DEFAULT_CARDS:
                    if c not in card_order:
                        card_order.append(c)
                # Remove cards that no longer exist
                card_order = [c for c in card_order if c in DEFAULT_CARDS]
            except Exception:
                card_order = list(DEFAULT_CARDS)
        else:
            card_order = list(DEFAULT_CARDS)
        return render_template("fraction.html", card_order=card_order)

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
        return render_template("fraction_dues.html", grouped_dues=grouped_dues,
                               dues_no_company=dues_no_company, all_companies=all_companies)

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
                ingredient_totals = {}
                total_time = 0
                total_cost = 0.0
                for item in cart:
                    mi = db.session.get(MenuItem, item["item_id"])
                    if mi:
                        total_time += mi.production_time_seconds * item["qty"]
                        for ri in mi.recipe_items.all():
                            if ri.ingredient is None:
                                continue
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
