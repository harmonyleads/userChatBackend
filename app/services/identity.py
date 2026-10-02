"""Stable page identity so later calls land on the same page row."""

from __future__ import annotations

import hashlib


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:32]


def canonical_page_id(
    page_id: str | None,
    page_url: str | None,
    asset_id: str | None,
    asset_name: str | None,
) -> str:
    if page_id:
        return page_id
    if page_url:
        return f"url_{_digest(page_url.strip().rstrip('/'))}"
    if asset_id:
        return f"asset_{_digest(asset_id)}"
    if asset_name:
        return f"name_{_digest(asset_name)}"
    raise ValueError("page_url, asset_id, or asset_name is required")
