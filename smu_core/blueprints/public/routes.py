from flask import Blueprint, render_template
from flask_login import login_required


public_bp = Blueprint("public", __name__)


def landing_page():
    return render_template("landing.html")


def privacy_policy():
    return render_template("privacy.html")


def terms_of_service():
    return render_template("terms.html")


def maintenance():
    return render_template("maintenance.html"), 503


@login_required
def help_centre():
    return render_template("help.html")


@public_bp.record_once
def register_public_routes(state):
    app = state.app
    routes = [
        ("/landing", "landing_page", landing_page),
        ("/privacy", "privacy_policy", privacy_policy),
        ("/terms", "terms_of_service", terms_of_service),
        ("/maintenance", "maintenance", maintenance),
        ("/help", "help_centre", help_centre),
    ]

    for rule, endpoint, view_func in routes:
        app.add_url_rule(rule, endpoint, view_func)

