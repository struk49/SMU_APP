from datetime import datetime

from smu_core.extensions import db


class PostRevision(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    version_number = db.Column(db.Integer, nullable=False)
    caption = db.Column(db.Text, nullable=False)
    score = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(50), default="manual")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

