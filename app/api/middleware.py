"""Response headers and an early body-size check."""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_body_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={"status": "ERROR", "errorMessage": "Request body is too large"},
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                outgoing = MutableHeaders(scope=message)
                outgoing["cache-control"] = "no-store"
                outgoing["x-content-type-options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, send_wrapper)
