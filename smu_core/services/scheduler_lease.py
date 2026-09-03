from datetime import timedelta

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from smu_core.services.time_utils import utc_now


class SchedulerLeaseCoordinator:
    def __init__(
        self,
        *,
        lease_model,
        db_session,
        owner_id,
        lease_name="background_jobs",
        lease_seconds=90,
        now_provider=utc_now,
    ):
        self.lease_model = lease_model
        self.db_session = db_session
        self.owner_id = owner_id
        self.lease_name = lease_name
        self.lease_seconds = lease_seconds
        self.now_provider = now_provider

    def acquire_or_renew(self):
        now = self.now_provider()
        expires_at = now + timedelta(seconds=self.lease_seconds)

        try:
            result = self.db_session.execute(
                update(self.lease_model)
                .where(
                    self.lease_model.name == self.lease_name,
                    or_(
                        self.lease_model.owner_id == self.owner_id,
                        self.lease_model.lease_expires_at <= now,
                    ),
                )
                .values(
                    owner_id=self.owner_id,
                    lease_expires_at=expires_at,
                    updated_at=now,
                )
            )
            if result.rowcount == 1:
                self.db_session.commit()
                return True

            self.db_session.rollback()
            try:
                self.db_session.add(
                    self.lease_model(
                        name=self.lease_name,
                        owner_id=self.owner_id,
                        lease_expires_at=expires_at,
                        updated_at=now,
                    )
                )
                self.db_session.commit()
                return True
            except IntegrityError:
                self.db_session.rollback()
                return self._take_expired_lease(now, expires_at)
        except Exception:
            self.db_session.rollback()
            return False

    def _take_expired_lease(self, now, expires_at):
        try:
            result = self.db_session.execute(
                update(self.lease_model)
                .where(
                    self.lease_model.name == self.lease_name,
                    self.lease_model.lease_expires_at <= now,
                )
                .values(
                    owner_id=self.owner_id,
                    lease_expires_at=expires_at,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                self.db_session.rollback()
                return False
            self.db_session.commit()
            return True
        except Exception:
            self.db_session.rollback()
            return False

    def release(self):
        now = self.now_provider()
        try:
            result = self.db_session.execute(
                update(self.lease_model)
                .where(
                    self.lease_model.name == self.lease_name,
                    self.lease_model.owner_id == self.owner_id,
                )
                .values(lease_expires_at=now, updated_at=now)
            )
            self.db_session.commit()
            return result.rowcount == 1
        except Exception:
            self.db_session.rollback()
            return False
