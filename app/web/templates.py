"""Shared templates instance for all web routers.

Breaks circular imports: routers import templates from here
instead of from web/__init__.py which imports routers.
"""

from datetime import UTC, datetime

from fastapi.templating import Jinja2Templates

from app.web.template_context import AutoContextTemplates
from app.web.template_filters import register_filters

# Shared templates instance with automatic context injection
# The fleet-canonical web template root (fw.web_templates_at_fleet_path); a sibling medium
# (email, llm) would get its own peer under app/templates/.
templates = AutoContextTemplates(directory="app/templates/web")

# Register all filters (date, time, timeago, num, etc.)
register_filters(templates)

# Register template globals
templates.env.globals["now"] = lambda: datetime.now(UTC)

# Plain instance (no injected globals) for the emitted exception handler
# (app/utils/exception_handlers.py reads app.state.templates).
error_templates = Jinja2Templates(directory="app/templates/web")
register_filters(error_templates)
