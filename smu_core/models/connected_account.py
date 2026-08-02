from datetime import datetime

from smu_core.extensions import db


class ConnectedAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)

    instagram_connected = db.Column(db.Boolean, default=False)
    facebook_connected = db.Column(db.Boolean, default=False)
    linkedin_connected = db.Column(db.Boolean, default=False)
    pinterest_connected = db.Column(db.Boolean, default=False)
    reddit_connected = db.Column(db.Boolean, default=False)
    x_connected = db.Column(db.Boolean, default=False)

    make_webhook_single = db.Column(db.String(500))
    make_webhook_carousel = db.Column(db.String(500))

    created_at = db.Column(db.DateTime, default=datetime.utcnow())
    updated_at = db.Column(db.DateTime, default=datetime.utcnow(), onupdate=datetime.utcnow())

