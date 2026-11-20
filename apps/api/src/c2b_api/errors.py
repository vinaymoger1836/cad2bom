"""RFC 7807-style error responses with a `detail` written for the person using the app."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from c2b_api.magic import UploadRefused

PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def problem_response(status: int, code: str, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail, "code": code},
        status_code=status,
        media_type=PROBLEM_JSON,
    )


def unauthorized() -> ProblemError:
    return ProblemError(
        401,
        "unauthorized",
        "This link is not valid",
        "This job link is not valid, or it is missing its access key. "
        "Open the full link you were given when the job was created.",
    )


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(_: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(exc.status, exc.code, exc.title, exc.detail)

    @app.exception_handler(UploadRefused)
    async def _refused(_: Request, exc: UploadRefused) -> JSONResponse:
        return problem_response(exc.status, exc.code, exc.title, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        msg = first.get("msg", "invalid value")
        detail = f"{where}: {msg}" if where else str(msg)
        return problem_response(422, "invalid_request", "The request is not valid", detail)
