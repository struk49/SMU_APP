from smu_core.extensions import db
from smu_core.services.time_utils import utc_now


class SchedulerLease(db.Model):
    name = db.Column(db.String(100), primary_key=True)
    owner_id = db.Column(db.String(255), nullable=False)
    lease_expires_at = db.Column(db.DateTime, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)
