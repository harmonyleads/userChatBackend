"""Close the open page visit and record how long it lasted."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import require_widget
from app.auth.guard import verify_session
from app.schemas.common import parse_model
from app.schemas.requests import PageExitRequest
from app.services.events import build_page_exit_event
from app.services.identity import canonical_page_id

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/page_exit")
async def page_exit(request: Request, raw: dict = Depends(require_widget)) -> JSONResponse:
    model = parse_model(PageExitRequest, raw)
    page_id = canonical_page_id(model.page_id, model.page_url, model.asset_id, model.asset_name)
    claims = verify_session(
        request,
        raw,
        user_id=model.user_id,
        session_id=model.session_id,
        page_id=page_id,
        allow_expired=True,
    )
    session_id = model.session_id or str(claims["session_id"])
    event = build_page_exit_event(
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
    stored = await request.app.state.persistence.save("page_exit", event)
    if not stored:
        logger.error("page_exit_not_stored id=%s user_id=%s", event["id"], model.user_id)
        return JSONResponse(
            status_code=500,
            content={
                "status": "ERROR",
                "errorMessage": "The page exit could not be stored",
            },
        )
    return JSONResponse(status_code=200, content={"status": "SUCCESS"})
