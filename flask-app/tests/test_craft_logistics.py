import ast
import json
import os
from pathlib import Path
import sys
import unittest
from datetime import date, datetime
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from craft_logistics import item_weight_gram, plan_trips
from models import define_models
from routes import register_all_routes

ROOT = Path(__file__).resolve().parents[1]


def make_app():
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.config.update(SECRET_KEY='isolated-test-only', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      UPLOAD_FOLDER='/tmp/browser/craft-tests/uploads', TESTING=True)
    db = SQLAlchemy(app)
    models = define_models(db, app)
    login = LoginManager(app)
    login.login_view = 'visitor'

    @login.user_loader
    def load_user(user_id):
        return db.session.get(models['User'], int(user_id))

    app.jinja_env.globals['csrf_token'] = lambda: 'test-only'
    from helpers import now_cet
    app.context_processor(lambda: {'now': now_cet})
    register_all_routes(app, db, models)
    return app, db, models


class CraftTests(unittest.TestCase):
    def setUp(self):
        self.app, self.db, self.models = make_app()
        self.context = self.app.app_context()
        self.context.push()
        self.db.create_all()
        User, Ingredient, MenuItem, Recipe = [self.models[key] for key in ('User', 'Ingredient', 'MenuItem', 'MenuItemIngredient')]
        self.user = User(discord_id='test', username='Test', ingame_name='Teszt Elek', is_admin=True)
        self.db.session.add(self.user)
        self.inputs = [Ingredient(name=name, weight_per_unit_gram=100, stock=10000) for name in ('Élesztő', 'Víz', 'Szőlő')]
        self.wine = MenuItem(name='Fehérbor', category='alc', weight_gram=500, price=50)
        self.db.session.add_all([*self.inputs, self.wine])
        self.db.session.flush()
        self.db.session.add_all([Recipe(menu_item_id=self.wine.id, ingredient_id=ing.id, quantity=qty)
                                 for ing, qty in zip(self.inputs, (2, 3, 4))])
        self.db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user.id)
            session['_fresh'] = True
            session['pos_cart'] = [{'id': self.wine.id, 'qty': 100}]

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.context.pop()

    def result(self, query='&occupied_kg=24&capacity_kg=50'):
        response = self.client.get('/fraction/calculator?step=finish&mode=production' + query)
        self.assertEqual(response.status_code, 200)
        from bs4 import BeautifulSoup
        return json.loads(BeautifulSoup(response.data, 'html.parser').find(id='craft-plan-data').string)

    def test_24_of_50kg_and_2_3_4_recipe(self):
        step = self.result()['steps'][0]
        self.assertEqual(step['plan']['batch'], 28)
        self.assertEqual(step['plan']['trips'], 4)
        self.assertEqual(step['plan']['last'], 16)
        self.assertEqual([row['qty'] * 28 for row in step['inputs']], [56, 84, 112])

    def test_output_weight_also_limits_capacity(self):
        self.wine.weight_gram = 2000
        self.db.session.commit()
        self.assertEqual(self.result()['steps'][0]['plan']['batch'], 13)

    def test_gram_input_is_saved_and_read_back(self):
        response = self.client.post('/admin/menuitems', data={'form_type': 'update_menu_item_weight',
                                    'mi_id': self.wine.id, 'weight_gram': '250.5'})
        self.assertEqual(response.status_code, 302)
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(self.models['MenuItem'], self.wine.id).weight_gram, 250.5)
        self.assertEqual(self.result()['steps'][0]['weight_gram'], 250.5)

    def test_food_weight_creation_in_grams(self):
        self.client.post('/admin/menuitems', data={'form_type': 'add_menu_item', 'mi_name': 'Étel',
                         'mi_category': 'food', 'mi_price': '100', 'mi_weight_gram': '300'})
        food = self.models['MenuItem'].query.filter_by(name='Étel').one()
        self.assertEqual(food.weight_gram, 300)

    def test_recipe_fallback_weight(self):
        self.wine.weight_gram = 0
        self.assertEqual(item_weight_gram(self.wine), 900)

    def test_missing_weight_blocks_trip_estimate(self):
        self.inputs[0].weight_per_unit_gram = 0
        self.db.session.commit()
        self.assertNotIn('plan', self.result()['steps'][0])

    def test_no_room_or_one_craft_too_large(self):
        self.assertNotIn('plan', self.result('&occupied_kg=50&capacity_kg=50')['steps'][0])
        self.assertNotIn('plan', self.result('&occupied_kg=49.9&capacity_kg=50')['steps'][0])

    def test_invalid_weights_not_saved(self):
        for value in ('-1', 'nan', 'inf'):
            self.client.post('/admin/menuitems', data={'form_type': 'update_menu_item_weight',
                             'mi_id': self.wine.id, 'weight_gram': value})
            self.assertEqual(self.wine.weight_gram, 500)

    def test_subproducts_planned_first_and_stock_consumed_once(self):
        MenuItem, Recipe = self.models['MenuItem'], self.models['MenuItemIngredient']
        final = MenuItem(name='Fröccs', category='alc', weight_gram=500)
        self.wine.stock = 2
        self.db.session.add(final)
        self.db.session.flush()
        self.db.session.add(Recipe(menu_item_id=final.id, sub_menu_item_id=self.wine.id, quantity=1))
        self.db.session.commit()
        with self.client.session_transaction() as session:
            session['pos_cart'] = [{'id': final.id, 'qty': 10}]
        steps = self.result()['steps']
        self.assertEqual([(s['name'], s['qty']) for s in steps], [('Fehérbor', 8), ('Fröccs', 10)])
        self.assertEqual(self.wine.stock, 2)  # planning is read-only
        self.client.post('/fraction/calculator/confirm', data={'mode': 'production'})
        self.assertEqual(self.wine.stock, 0)
        self.assertEqual(final.stock, 10)
        self.assertEqual([ing.stock for ing in self.inputs], [9984, 9976, 9968])

    def test_recipe_cycles_have_unknown_fallback_weight(self):
        Recipe = self.models['MenuItemIngredient']
        self.wine.weight_gram = 0
        self.db.session.add(Recipe(menu_item_id=self.wine.id, sub_menu_item_id=self.wine.id, quantity=1))
        self.db.session.flush()
        self.assertIsNone(item_weight_gram(self.wine))

    def test_capacity_is_remembered_for_same_session(self):
        self.result()
        self.assertEqual(self.result('')['capacity'], 50)

    def test_additive_migration_preserves_existing_records(self):
        # Run the actual startup migration against an old-schema in-memory DB,
        # never importing app.py (which would open the deployment database).
        with self.db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE menu_items DROP COLUMN weight_gram'))
        tree = ast.parse((ROOT / 'app.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_auto_migrate_db')
        module = ast.Module(body=[function], type_ignores=[])
        namespace = {'db': self.db}
        exec(compile(module, '<actual startup migration>', 'exec'), namespace)
        namespace['_auto_migrate_db']()
        self.db.session.expire_all()
        saved = self.db.session.get(self.models['MenuItem'], self.wine.id)
        self.assertEqual((saved.name, saved.price, saved.weight_gram), ('Fehérbor', 50, 0))
        self.assertEqual(self.models['Ingredient'].query.count(), 3)


if __name__ == '__main__':
    unittest.main()