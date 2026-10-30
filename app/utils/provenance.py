# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Provenance defaults utility

Applies environment-level provenance defaults to Create and Update models.
When ENFORCE_ENV_OVERWRITE is False (default), env vars fill in None fields only on create.
When ENFORCE_ENV_OVERWRITE is True, env vars always override agent-provided values
on both create and update operations.
"""

from typing import Any

from pydantic import BaseModel

from app.config.settings import settings

PROVENANCE_ENV_MAP = {
    "encoding_agent": "ENCODING_AGENT",
    "encoding_version": "ENCODING_VERSION",
    "agent_id": "AGENT_ID",
    "agent_version": "AGENT_VERSION",
    "agent_model": "AGENT_MODEL",
}


def apply_provenance_defaults[Model: BaseModel](data: Model) -> Model:
    """Apply env-level provenance defaults to a Create model.

    - ENFORCE_ENV_OVERWRITE=False (default): env vars fill in None fields only
    - ENFORCE_ENV_OVERWRITE=True: env vars always override agent-provided values
    """
    updates: dict[str, Any] = {}
    enforce = settings.ENFORCE_ENV_OVERWRITE

    for field, setting_name in PROVENANCE_ENV_MAP.items():
        env_value = getattr(settings, setting_name, None)
        if env_value:
            current = getattr(data, field, None)
            if enforce or current is None:
                updates[field] = env_value

    return data.model_copy(update=updates) if updates else data


def apply_provenance_defaults_for_update[Model: BaseModel](data: Model) -> Model:
    """Apply env-level provenance enforcement to Update models.

    Only active when ENFORCE_ENV_OVERWRITE=True:
    - Overrides any explicitly-set provenance fields with env values
    - Does NOT inject provenance fields that weren't in the update (preserves PATCH semantics)

    When ENFORCE_ENV_OVERWRITE=False, returns unchanged — env defaults were applied
    during create, and agent update requests are respected.
    """
    if not settings.ENFORCE_ENV_OVERWRITE:
        return data

    updates: dict[str, Any] = {}
    for field, setting_name in PROVENANCE_ENV_MAP.items():
        env_value = getattr(settings, setting_name, None)
        if env_value and field in data.model_fields_set:
            updates[field] = env_value

    return data.model_copy(update=updates) if updates else data
