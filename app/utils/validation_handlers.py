"""The answer to a request the web routes refuse as invalid (a 422).

A machine client on `/api` keeps FastAPI's JSON body. A browser gets HTML, negotiated like the
unexpected-error handler in `exception_handlers.py` (which only covers a 500): an HTMX request
gets an error partial carrying `HX-Error-Swap: true`, which `static/js/htmx-error-swap.js` swaps
into the failing target, and any other request gets an error page. Neither echoes the submitted
values; they name the refused parameters, which are the route's own.
"""

from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.templating import Jinja2Templates
from starlette.responses import Response


def _is_api(request: Request) -> bool:
    """An `/api` path, or one under `/api/`, below the app's root path."""
    path = str(request.scope.get("path") or "")
    path = path.removeprefix(str(request.scope.get("root_path") or ""))
    return path == "/api" or path.startswith("/api/")


def _refused(exc: RequestValidationError) -> list[str]:
    """The names of the refused parameters, in the order they were found, each once."""
    names: list[str] = []
    for error in exc.errors():
        name = str(error["loc"][-1]) if error["loc"] else "request"
        if name not in names:
            names.append(name)
    return names


async def request_validation_handler(request: Request, exc: Exception) -> Response:
    """JSON for `/api`; an HTML partial (HTMX) or page (otherwise) for the web routes, status 422.
    Registered for RequestValidationError."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    if _is_api(request):
        return await request_validation_exception_handler(request, exc)

    templates: Jinja2Templates = request.app.state.templates
    context = {"refused": _refused(exc)}
    if request.headers.get("HX-Request") is not None:
        response = templates.TemplateResponse(
            request, "partials/errors/invalid_request.html", context, status_code=422
        )
        response.headers["HX-Error-Swap"] = "true"
        return response
    return templates.TemplateResponse(
        request, "pages/errors/422.html", context, status_code=422
    )
