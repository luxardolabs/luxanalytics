"""Shared templates instance for all web routers.

Breaks circular imports: routers import templates from here
instead of from web/__init__.py which imports routers.
"""

from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined

from app.core.config import settings
from app.web.template_filters import register_filters

# The fleet-canonical web template root (fw.web_templates_at_fleet_path); a sibling medium
# (email, llm) would get its own peer under app/templates/.
templates = Jinja2Templates(directory="app/templates/web")

# A missing variable fails the render loudly, never renders as "" (fw.jinja_strict_undefined;
# luxarch --playbook jinja-strict-undefined). route-smoke renders every route under it.
templates.env.undefined = StrictUndefined

# Register all filters (date, time, timeago, num, etc.)
register_filters(templates)

# Every page shows the running version (the footer); a Jinja global, so each render passes a
# literal template name straight to Starlette's TemplateResponse.
templates.env.globals["version"] = settings.APP_VERSION


# Plain instance (no injected globals) for the emitted exception handler
# (app/utils/exception_handlers.py reads app.state.templates).
error_templates = Jinja2Templates(directory="app/templates/web")
error_templates.env.undefined = StrictUndefined
register_filters(error_templates)
