"""Ask Sarah and store the question, answer, and raw exchange."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import require_widget
from app.auth.guard import verify_session
from app.schemas.common import parse_model
from app.schemas.requests import GetAnswersRequest
from app.services.events import build_answer_event, utc_now
from app.services.identity import canonical_page_id
from app.services.sarah import SarahOutcome, resolve_sarah_auth_code

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/get-answers")
async def get_answers(request: Request, raw: dict = Depends(require_widget)) -> JSONResponse:
    settings = request.app.state.settings
    model = parse_model(GetAnswersRequest, raw)
    assert model.question is not None
    page_id = canonical_page_id(model.page_id, model.page_url, model.asset_id, model.asset_name)
    claims = verify_session(
        request,
        raw,
        user_id=model.user_id,
        session_id=model.session_id,
        page_id=page_id,
        allow_expired=False,
    )
    session_id = model.session_id or str(claims["session_id"])
    auth_code = resolve_sarah_auth_code(settings, model.sarah_auth_code)
    created_at = utc_now()
    try:
        outcome = await request.app.state.sarah.ask(question=model.question, auth_code=auth_code)
    except Exception:
        logger.exception("sarah_ask_failed user_id=%s", model.user_id)
        outcome = SarahOutcome(
            status="ERROR",
            answer=None,
            error_message="The answer service is unavailable",
            url=settings.sarah.url_template.replace("{sarah_auth_code}", auth_code),
            request_body={"question": model.question},
            response_body={"error": "unexpected_failure"},
            latency_ms=0,
        )
    completed_at = utc_now()
    event = build_answer_event(
        user_id=model.user_id,
        page_id=page_id,
        session_id=session_id,
        page_url=model.page_url,
        page_title=model.page_title,
        asset_id=model.asset_id,
        asset_name=model.asset_name,
        asset_type=model.asset_type,
        asset_version=model.asset_version,
        question=model.question,
        answer=outcome.answer,
        status=outcome.status,
        sarah_request={
            "url": outcome.url,
            "body": outcome.request_body,
            "sarah_auth_code": auth_code,
        },
        sarah_response=outcome.response_body,
        sarah_latency_ms=outcome.latency_ms,
        created_at=created_at,
        completed_at=completed_at,
        max_response_bytes=settings.persistence.max_response_bytes,
    )
    stored = await request.app.state.persistence.save("answer", event)
    body: dict = {"answer": outcome.answer, "status": outcome.status}
    if outcome.error_message:
        body["errorMessage"] = outcome.error_message
    if not stored:
        logger.error("answer_not_stored id=%s user_id=%s", event["id"], model.user_id)
        if outcome.status == "SUCCESS":
            body["status"] = "ERROR"
            body["errorMessage"] = "The chat exchange could not be stored"
            return JSONResponse(status_code=500, content=body)
    return JSONResponse(status_code=200, content=body)
