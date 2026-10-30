# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The tenant: the isolation key of every stored memory."""

from dataclasses import dataclass
from uuid import UUID, uuid5

# A tenant has no record of its own. Its id is derived from its name under this fixed namespace, so the
# same name is the same tenant on every instance, and an export from one instance imports into another.
TENANT_NAMESPACE = UUID("6f1d8a3c-2b47-5e90-9c1a-4d7e0b5a3f21")


@dataclass(frozen=True)
class Tenant:
    """A tenant, named by the credential of the request (or ``DEFAULT_TENANT`` for the local one)."""

    name: str

    @property
    def id(self) -> UUID:
        """The key every stored memory and link carries and every query filters on."""
        return uuid5(TENANT_NAMESPACE, self.name)
