#!/usr/bin/env python3
"""Enforce routers never import CRUD directly - must go through services."""
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

    # Only check router files (both API and web routers)
    if not re.search(r"routers/.*\.py$", file_path):
        sys.exit(0)

    # Check for bypass comment: # noqa: crud-import (reason)
    # Bypass requires a reason in parentheses
    if re.search(r"#\s*noqa:\s*crud-import\s*\([^)]+\)", new_string):
        sys.exit(0)

    # Look for imports from crud/
    # e.g., "from crud.station_crud import station_crud" is blocked
    crud_imports = re.findall(
        r"from\s+crud\.(\w+)\s+import",
        new_string
    )

    if crud_imports:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Routers must NEVER import CRUD directly. "
                    "Use services instead. Pattern: Router -> Service -> CRUD"
                )
            }
        }))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
