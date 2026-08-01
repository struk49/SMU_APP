from datetime import datetime

from smu_core.extensions import db


class BetaApplication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    primary_platform = db.Column(db.String(50), nullable=False)
    posting_frequency = db.Column(db.String(80), nullable=False)
    challenge = db.Column(db.Text, nullable=False)
    consent = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(50), default="new", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow())
