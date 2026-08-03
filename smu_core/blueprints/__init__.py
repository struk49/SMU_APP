"""Blueprint package for incremental route extraction."""

from smu_core.blueprints.auth import auth_bp
from smu_core.blueprints.beta import beta_bp
from smu_core.blueprints.public import public_bp


__all__ = ["auth_bp", "beta_bp", "public_bp"]
