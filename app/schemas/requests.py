"""Request bodies for the chat page."""

from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.common import APIModel


class IdentityRequest(APIModel):
    user_id: str | None = None
    page_id: str | None = None
    page_url: str | None = None
    page_title: str | None = None
    session_id: str | None = None
    asset_id: str | None = None
    asset_name: str | None = None
    asset_type: str | None = None
    asset_version: str | None = None

    @model_validator(mode="after")
    def require_page_identity(self) -> "IdentityRequest":
        if not self.page_url and not self.asset_id and not self.asset_name:
            raise ValueError("page_url, asset_id, or asset_name is required")
        return self


class InitializeRequest(IdentityRequest):
    pass


class GetAnswersRequest(IdentityRequest):
    sarah_auth_code: str | None = Field(default=None, alias="sarahAuthCode")
    question: str | None = None

    @model_validator(mode="after")
    def require_question(self) -> "GetAnswersRequest":
        if not self.question:
            raise ValueError("question is required")
        return self


class PageExitRequest(IdentityRequest):
    pass
