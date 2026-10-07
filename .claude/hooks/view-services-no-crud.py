#!/usr/bin/env python3
"""Block CRUD imports in view services - they must use core services."""
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

    # Only check view service files
    if not file_path.endswith("_view_service.py"):
        sys.exit(0)

    # Check for bypass comment: # noqa: crud-import (reason)
    # Bypass requires a reason in parentheses
    if re.search(r"#\s*noqa:\s*crud-import\s*\([^)]+\)", new_string):
        sys.exit(0)

    # Block imports from crud module
    # e.g., "from crud.tenant_crud import tenant_crud" is blocked
    crud_import = re.search(r"from\s+crud\.", new_string)

    if crud_import:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "View services must not import from crud. "
                    "Pattern: ViewService -> CoreService -> CRUD"
                )
            }
        }))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
