"""Shared templates instance for all web routers.

Breaks circular imports: routers import templates from here
instead of from web/__init__.py which imports routers.
"""

from datetime import UTC, datetime

from app.web.template_context import AutoContextTemplates
from app.web.template_filters import register_filters

# Shared templates instance with automatic context injection
templates = AutoContextTemplates(directory="app/templates")

# Register all filters (date, time, timeago, num, etc.)
register_filters(templates)

# Register template globals
templates.env.globals["now"] = lambda: datetime.now(UTC)
