"""Core package for the incremental SMU Flask refactor."""

import os

from dotenv import load_dotenv
from flask import Flask

from smu_core.extensions import db, login_manager


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def create_app(config_object=None):
    load_dotenv()

    from config import Config

    app = Flask(
        "app",
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )
    app.config.from_object(Config)

    if isinstance(config_object, dict):
        app.config.update(config_object)
    elif config_object:
        app.config.from_object(config_object)

    db.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message_category = "warning"

    from smu_core.blueprints.auth import auth_bp
    from smu_core.blueprints.beta import beta_bp
    from smu_core.blueprints.brand import brand_bp
    from smu_core.blueprints.feedback import feedback_bp
    from smu_core.blueprints.public import public_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(beta_bp)
    app.register_blueprint(brand_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(public_bp)

    return app
