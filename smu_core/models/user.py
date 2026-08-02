from datetime import datetime

from flask_login import UserMixin

from smu_core.extensions import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    brand_brief = db.relationship(
        "BrandBrief",
        backref="user",
        uselist=False,
        lazy=True
    )
    connected_account = db.relationship(
    "ConnectedAccount",
    backref="user",
    uselist=False,
    lazy=True
)

    posts = db.relationship("Post", backref="user", lazy=True)

