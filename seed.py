import json
from app import app, db

OUTPUT_FILE = "seed_data.json"


def serialize_model(obj):
    data = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.name)

        # datetime/date kezelés
        if hasattr(value, "isoformat"):
            value = value.isoformat()

        data[column.name] = value
    return data


def export_seed():
    with app.app_context():
        seed = {}

        for table_name, model in db.Model.registry._class_registry.items():
            if not hasattr(model, "__table__"):
                continue

            records = model.query.all()
            if not records:
                continue

            seed[model.__name__] = [
                serialize_model(r) for r in records
            ]

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(seed, f, indent=4)

        print("✔ Seed export kész:", OUTPUT_FILE)


def restore_seed():
    from datetime import datetime, date
    from sqlalchemy import inspect

    with app.app_context():
        with open("seed_data.json", "r", encoding="utf-8") as f:
            seed = json.load(f)

        total_inserted = 0

        for model_name, records in seed.items():
            model = db.Model.registry._class_registry.get(model_name)
            if not model:
                continue

            inspector = inspect(model)
            pk_columns = [col.name for col in inspector.primary_key]

            for record in records:
                # dátum visszaalakítás
                for key, value in record.items():
                    if isinstance(value, str):
                        try:
                            if "T" in value:
                                record[key] = datetime.fromisoformat(value)
                            elif "-" in value and len(value) == 10:
                                record[key] = date.fromisoformat(value)
                        except Exception:
                            pass

                # 🔎 Meglévő rekord keresése PK alapján
                filter_kwargs = {pk: record.get(pk) for pk in pk_columns}

                existing = model.query.filter_by(**filter_kwargs).first()

                if not existing:
                    obj = model(**record)
                    db.session.add(obj)
                    total_inserted += 1

        db.session.commit()

        print(f"✔ Restore kész. Új rekordok beszúrva: {total_inserted}")