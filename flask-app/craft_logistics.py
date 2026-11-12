"""Weight and trip planning; no database writes or Flask dependencies."""

import math


def valid_weight(value):
    """Unknown/zero weights must never imply unlimited carrying capacity."""
    try:
        weight = float(value or 0)
    except (TypeError, ValueError):
        return None
    return weight if math.isfinite(weight) and weight > 0 else None


def item_weight_gram(item, ancestors=()):
    explicit = valid_weight(item.weight_gram)
    if explicit is not None:
        return explicit
    if item.id in ancestors:
        return None
    recipe = item.recipe_items.all()
    if not recipe:
        return None
    total = 0
    for row in recipe:
        quantity = float(row.quantity or 0)
        if quantity <= 0:
            continue
        if row.sub_menu_item_id and row.sub_menu_item:
            weight = item_weight_gram(row.sub_menu_item, (*ancestors, item.id))
        elif row.ingredient:
            weight = valid_weight(row.ingredient.weight_per_unit_gram)
        else:
            return None
        if weight is None:
            return None
        total += quantity * weight
    return valid_weight(total)


def craft_step(item, quantity):
    inputs = []
    for row in item.recipe_items.all():
        if (row.quantity or 0) <= 0:
            continue
        if row.sub_menu_item_id and row.sub_menu_item:
            source = row.sub_menu_item
            weight = item_weight_gram(source)
            unit = "db"
        elif row.ingredient:
            source = row.ingredient
            weight = valid_weight(source.weight_per_unit_gram)
            unit = source.unit
        else:
            inputs.append({"name": "Hiányzó recept-összetevő", "qty": row.quantity,
                           "unit": "?", "weight_gram": None})
            continue
        inputs.append({"name": source.name, "qty": row.quantity,
                       "unit": unit, "weight_gram": weight})
    return {"id": item.id, "name": item.name, "qty": quantity,
            "weight_gram": item_weight_gram(item), "inputs": inputs}


def plan_trips(step, occupied_kg, capacity_kg):
    """Inputs are loaded together; outputs are unloaded after each trip.

    Both the input and output weight must fit. As each craft consumes the same
    recipe, weight changes monotonically, so their maximum is the peak load.
    """
    if not all(math.isfinite(v) for v in (occupied_kg, capacity_kg)):
        raise ValueError("Invalid capacity")
    free = (capacity_kg - occupied_kg) * 1000
    if occupied_kg < 0 or capacity_kg <= 0 or free <= 0:
        raise ValueError("No free capacity")
    output_weight = valid_weight(step["weight_gram"])
    weights = [valid_weight(row["weight_gram"]) for row in step["inputs"]]
    if output_weight is None or not weights or any(w is None for w in weights):
        raise ValueError("Unknown weight")
    input_weight = sum(row["qty"] * weight for row, weight in zip(step["inputs"], weights))
    per_unit = max(input_weight, output_weight)
    batch = math.floor(free / per_unit + 1e-9)
    if batch < 1:
        raise ValueError("One craft does not fit")
    trips = math.ceil(step["qty"] / batch)
    last = step["qty"] - batch * (trips - 1)
    return {"batch": batch, "trips": trips, "last": last,
            "input_gram": input_weight, "output_gram": output_weight}