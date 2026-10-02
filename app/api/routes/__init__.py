from fastapi import APIRouter

from app.api.routes import analytics, answers, health, initialize, page_exit


def build_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(initialize.router)
    router.include_router(answers.router)
    router.include_router(page_exit.router)
    router.include_router(analytics.router)
    return router
