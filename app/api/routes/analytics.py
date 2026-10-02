"""Placeholder until reporting is designed."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import require_widget

router = APIRouter()


@router.api_route("/analytics", methods=["GET", "POST"])
async def analytics(request: Request, raw: dict = Depends(require_widget)) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"status": "ERROR", "errorMessage": "Analytics is not implemented yet"},
    )
