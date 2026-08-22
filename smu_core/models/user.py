from flask_login import UserMixin

from smu_core.extensions import db
from smu_core.services.time_utils import utc_now


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    subscription_status = db.Column(db.String(50), nullable=True)
    subscription_current_period_end = db.Column(db.DateTime, nullable=True)

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
