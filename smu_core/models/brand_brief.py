from datetime import datetime

from smu_core.extensions import db


class BrandBrief(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)

    business_name = db.Column(db.String(200))
    niche = db.Column(db.String(200))
    target_audience = db.Column(db.Text)
    offer = db.Column(db.Text)
    tone_of_voice = db.Column(db.String(200))
    content_goals = db.Column(db.Text)
    main_platforms = db.Column(db.String(300))
    cta_style = db.Column(db.String(200))
    words_to_avoid = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow())
    updated_at = db.Column(db.DateTime, default=datetime.utcnow(), onupdate=datetime.utcnow())
