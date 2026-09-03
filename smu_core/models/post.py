from smu_core.extensions import db
from smu_core.services.time_utils import utc_now


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_url = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)
    prompt = db.Column(db.Text)
    caption = db.Column(db.Text)
    status = db.Column(db.String(50), default="draft")
    created_at = db.Column(db.DateTime, default=utc_now)
    sent_at = db.Column(db.DateTime)
    scheduled_time = db.Column(db.DateTime, nullable=True)
    group_id = db.Column(db.String(100), nullable=True)
    post_type = db.Column(db.String(50), default="single")
    platforms = db.Column(db.String(200), default="instagram,facebook")
    sort_order = db.Column(db.Integer, default=0)
    is_cover = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    grade_result = db.Column(db.Text, nullable=True)
    grade_score = db.Column(db.Float, nullable=True)
    graded_at = db.Column(db.DateTime, nullable=True)
    # AI Improved Version
    improved_caption = db.Column(db.Text, nullable=True)
    improved_at = db.Column(db.DateTime, nullable=True)
    # Brand Coach
    brand_score = db.Column(db.Float, nullable=True)
    brand_feedback = db.Column(db.Text, nullable=True)
    zernio_post_id = db.Column(db.String(255), nullable=True)
    zernio_status = db.Column(db.String(50), nullable=True)
    zernio_platforms = db.Column(db.String(200), nullable=True)
    zernio_published_url = db.Column(db.String(500), nullable=True)
    zernio_error = db.Column(db.Text, nullable=True)

    revisions = db.relationship(
    "PostRevision",
    backref="post",
    lazy=True,
    cascade="all, delete-orphan"
)
