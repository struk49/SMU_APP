from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from smu_core.extensions import db
from smu_core.models import User


auth_bp = Blueprint("auth", __name__)


def _log_event(event_name, **fields):
    log_event = current_app.extensions.get("smu_log_event")
    if log_event:
        log_event(event_name, **fields)


def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not email or not password:
            flash("Please enter an email and password.", "danger")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register"))

        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            flash("An account with that email already exists.", "danger")
            return redirect(url_for("register"))

        user = User(
            email=email,
            password_hash=generate_password_hash(password)
        )

        db.session.add(user)
        db.session.commit()

        login_user(user)

        flash("Account created successfully.", "success")
        return redirect(url_for("index"))

    return render_template("register.html")


def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            _log_event(
                "login_failure",
                email_present=bool(email),
                reason="invalid_credentials",
            )
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        login_user(user)
        _log_event(
            "login_success",
            user_id=user.id,
        )

        flash("Logged in successfully.", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


@auth_bp.record_once
def register_auth_routes(state):
    app = state.app
    routes = [
        ("/register", "register", register, ["GET", "POST"]),
        ("/login", "login", login, ["GET", "POST"]),
        ("/logout", "logout", logout, ["GET"]),
    ]

    for rule, endpoint, view_func, methods in routes:
        app.add_url_rule(rule, endpoint, view_func, methods=methods)
