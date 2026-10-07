#!/usr/bin/env python3
"""Enforce web routers use ViewServices, not core services directly."""
import json
import re
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    new_string = tool_input.get("new_string", "")

    # Only check web router files
    if not re.search(r"web/routers/.*\.py$", file_path):
        sys.exit(0)

    # Check for bypass comment: # noqa: core-service (reason)
    # Bypass requires a reason in parentheses
    if re.search(r"#\s*noqa:\s*core-service\s*\([^)]+\)", new_string):
        sys.exit(0)

    # Look for imports from services/ that don't have "view" in them
    # e.g., "from services.station_service import StationService" is blocked
    # but "from services.station_view_service import ..." is allowed
    # Use findall to check ALL imports, not just the first one
    service_imports = re.findall(
        r"from\s+services\.(\w+_service)\s+import",
        new_string
    )

    for service_name in service_imports:
        if "view" not in service_name:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Web routers must use *_view_service, not {service_name}. "
                        "Pattern: WebRouter -> ViewService -> CoreService -> CRUD"
                    )
                }
            }))
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
