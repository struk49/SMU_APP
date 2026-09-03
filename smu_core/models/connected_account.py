from smu_core.extensions import db
from smu_core.services.time_utils import utc_now


class ConnectedAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)

    instagram_connected = db.Column(db.Boolean, default=False)
    facebook_connected = db.Column(db.Boolean, default=False)
    linkedin_connected = db.Column(db.Boolean, default=False)
    pinterest_connected = db.Column(db.Boolean, default=False)
    reddit_connected = db.Column(db.Boolean, default=False)
    x_connected = db.Column(db.Boolean, default=False)

    linkedin_access_token = db.Column(db.String(1000))
    linkedin_access_token_expires_at = db.Column(db.DateTime)
    linkedin_scopes = db.Column(db.String(500))
    linkedin_member_id = db.Column(db.String(255))
    linkedin_member_urn = db.Column(db.String(255))
    linkedin_display_name = db.Column(db.String(255))
    linkedin_refresh_token = db.Column(db.String(1000))
    linkedin_refresh_token_expires_at = db.Column(db.DateTime)

    zernio_profile_id = db.Column(db.String(255))
    zernio_instagram_account_id = db.Column(db.String(255))
    zernio_facebook_account_id = db.Column(db.String(255))

    make_webhook_single = db.Column(db.String(500))
    make_webhook_carousel = db.Column(db.String(500))

    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
