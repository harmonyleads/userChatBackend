from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    persistence = getattr(request.app.state, "persistence", None)
    pending = persistence.pending_count if persistence is not None else 0
    return {"status": "SUCCESS", "pending_jobs": pending}
