"""Store the page view and issue a session token."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import require_widget
from app.auth.tokens import issue_session_token
from app.schemas.common import parse_model
from app.schemas.requests import InitializeRequest
from app.services.events import build_initialize_event
from app.services.identity import canonical_page_id

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/initialize")
async def initialize(request: Request, raw: dict = Depends(require_widget)) -> JSONResponse:
    settings = request.app.state.settings
    model = parse_model(InitializeRequest, raw)
    session_id = model.session_id or str(uuid.uuid4())
    page_id = canonical_page_id(model.page_id, model.page_url, model.asset_id, model.asset_name)
    issued_at = int(time.time())
    expires_at_unix = issued_at + settings.auth.session_ttl_seconds
    token = issue_session_token(
        {
            "v": 1,
            "user_id": model.user_id or "",
            "session_id": session_id,
            "page_id": page_id,
            "iat": issued_at,
            "exp": expires_at_unix,
        },
        settings.auth.signing_secret,
    )
    event = build_initialize_event(
        user_id=model.user_id,
        page_id=page_id,
        session_id=session_id,
        page_url=model.page_url,
        page_title=model.page_title,
        asset_id=model.asset_id,
        asset_name=model.asset_name,
        asset_type=model.asset_type,
        asset_version=model.asset_version,
    )
    stored = await request.app.state.persistence.save("initialize", event)
    if not stored:
        logger.error("initialize_not_stored id=%s user_id=%s", event["id"], model.user_id)
        return JSONResponse(
            status_code=500,
            content={
                "status": "ERROR",
                "errorMessage": "The page visit could not be stored",
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "status": "SUCCESS",
            "session_id": session_id,
            "page_id": page_id,
            "session_token": token,
            "expires_at": datetime.fromtimestamp(expires_at_unix, timezone.utc).isoformat(),
        },
    )
