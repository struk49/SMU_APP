from smu_core.extensions import db
from smu_core.services.time_utils import utc_now


class UserUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        unique=True,
        nullable=False,
    )
    plan = db.Column(db.String(50), nullable=False, default="pro")
    ai_images_used = db.Column(db.Integer, nullable=False, default=0)
    content_packs_used = db.Column(db.Integer, nullable=False, default=0)
    usage_period_start = db.Column(db.DateTime, nullable=False)
    usage_period_end = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
