"""Service that provisions the tenant behind every authenticated request
"""
import logging

from app.exceptions import ConflictError
from app.models.user_models import User, UserCreate
from app.protocols.user_protocol import UserRepository

logger = logging.getLogger(__name__)


class TenantService:
    """Looks a tenant up by name and creates it on first use

    A tenant is the isolation key of the data: every memory belongs to exactly one tenant
    and is only ever read or changed on behalf of that tenant. Its record holds nothing
    but the name, so an existing record is never updated.
    """

    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    async def get_or_create_tenant(self, tenant_id: str) -> User:
        """The record of the named tenant, created on first use

        Args:
            tenant_id: Tenant name, as taken from the credential

        Returns:
            The tenant record (its ``id`` is the key the repositories filter on)

        Raises:
            RuntimeError: If a concurrent request created the record and then removed it
                again before it could be read back
        """
        existing = await self.user_repo.get_user_by_external_id(external_id=tenant_id)
        if existing:
            return existing

        logger.info("Creating tenant", extra={"tenant": tenant_id})
        try:
            return await self.user_repo.create_user(
                user=UserCreate(external_id=tenant_id, name=tenant_id, email=""),
            )
        except ConflictError:
            # Another request created this tenant between our SELECT and INSERT:
            # use the winner's row.
            logger.info("Tenant creation race condition, fetching existing tenant",
                        extra={"tenant": tenant_id})
            winner = await self.user_repo.get_user_by_external_id(external_id=tenant_id)
            if winner is None:
                raise RuntimeError("Tenant could not be provisioned") from None
            return winner
