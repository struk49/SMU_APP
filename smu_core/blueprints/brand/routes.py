from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from smu_core.extensions import db
from smu_core.models import BrandBrief


brand_bp = Blueprint("brand", __name__)


@login_required
def brand_brief():
    brief = BrandBrief.query.filter_by(user_id=current_user.id).first()

    if request.method == "POST":
        if not brief:
            brief = BrandBrief(user_id=current_user.id)
            db.session.add(brief)

        brief.business_name = request.form.get("business_name", "").strip()
        brief.niche = request.form.get("niche", "").strip()
        brief.target_audience = request.form.get("target_audience", "").strip()
        brief.offer = request.form.get("offer", "").strip()
        brief.tone_of_voice = request.form.get("tone_of_voice", "").strip()
        brief.content_goals = request.form.get("content_goals", "").strip()
        brief.main_platforms = ",".join(request.form.getlist("main_platforms"))
        brief.cta_style = request.form.get("cta_style", "").strip()
        brief.words_to_avoid = request.form.get("words_to_avoid", "").strip()

        db.session.commit()

        flash("Brand Brief saved successfully.", "success")
        return redirect(url_for("brand_brief"))

    return render_template("brand_brief.html", brief=brief)


@brand_bp.record_once
def register_brand_routes(state):
    state.app.add_url_rule(
        "/brand-brief",
        "brand_brief",
        brand_brief,
        methods=["GET", "POST"],
    )
