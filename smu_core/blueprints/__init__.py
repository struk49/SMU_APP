"""Blueprint package for incremental route extraction."""

from smu_core.blueprints.accounts import accounts_bp
from smu_core.blueprints.auth import auth_bp
from smu_core.blueprints.beta import beta_bp
from smu_core.blueprints.brand import brand_bp
from smu_core.blueprints.feedback import feedback_bp
from smu_core.blueprints.public import public_bp


__all__ = [
    "accounts_bp",
    "auth_bp",
    "beta_bp",
    "brand_bp",
    "feedback_bp",
    "public_bp",
]
