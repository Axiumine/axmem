"""Interference in the window between two statements of one repository transaction.

``InterferingAdapter`` hands the repository the real session, but right after the first
UPDATE statement the repository executes it runs a coroutine on its own connection and
transaction. That reproduces another client committing a change at the exact moment the
repository has issued its write and has not yet looked at the outcome.
"""
from contextlib import asynccontextmanager

from sqlalchemy import Update


class InterferingAdapter:
    """Delegates to a real adapter and runs ``interference`` once, after the first UPDATE."""

    def __init__(self, real_adapter, interference):
        self._real_adapter = real_adapter
        self._interference = interference
        self._fired = False

    def system_session(self):
        return self._real_adapter.system_session()

    @asynccontextmanager
    async def session(self, user_id):
        async with self._real_adapter.session(user_id) as session:
            execute = session.execute

            async def execute_then_interfere(statement, *args, **kwargs):
                result = await execute(statement, *args, **kwargs)
                if isinstance(statement, Update) and not self._fired:
                    self._fired = True
                    await self._interference()
                return result

            session.execute = execute_then_interfere
            yield session
