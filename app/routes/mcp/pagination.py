# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

def clamp_list_pagination(limit: int, offset: int) -> tuple[int, int]:
    """Clamp list pagination for MCP tools (limit 1-100, offset >= 0)."""
    return max(1, min(limit, 100)), max(0, offset)
