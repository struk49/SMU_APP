"""Private beta access checks."""

from smu_core.models import BetaApplication


APPROVED_STATUS = "approved"


def normalize_email(email):
    return (email or "").strip().lower()


def is_email_approved_for_beta(email):
    normalized_email = normalize_email(email)
    if not normalized_email:
        return False

    return (
        BetaApplication.query.filter_by(
            email=normalized_email,
            status=APPROVED_STATUS,
        ).first()
        is not None
    )
