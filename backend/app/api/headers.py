"""Headers every API response carries.

Found by the OWASP ZAP baseline on the staging stack (docs/24-m7-closure.md
§3.1): API responses had neither `X-Content-Type-Options` nor any
`Cache-Control`. Both are defaults, so a route that has a reason to say
otherwise still can:

* `nosniff` — an API answer is data, never something a browser should guess
  is a page or a script;
* `no-store` — `/me`, the ledger and a job's result are one account's data,
  and nothing between us and the browser should keep a copy.

Pure ASGI rather than `BaseHTTPMiddleware`: it only edits the response start,
so it must not buffer the body of a large result on its way out.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULTS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_defaults(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in DEFAULTS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_defaults)
