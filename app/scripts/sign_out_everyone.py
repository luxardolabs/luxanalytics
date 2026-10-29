"""Sign every dashboard session out now: move the session generation to a fresh, random one.

A manual tool (`python -m app.scripts.sign_out_everyone` in the app container). A restore needs it
not: `make restore` / `make prod-restore` drop any restored generation (scripts/restore_tail.sql),
and the app writes a fresh one when it starts.
"""

import asyncio

from app.crud.dashboard_session_crud import reset_session_generation


def main() -> None:
    if asyncio.run(reset_session_generation()):
        print("Every dashboard session is signed out.")
    else:
        print(
            "No session table yet: the app creates it at a fresh generation when it starts."
        )


if __name__ == "__main__":
    main()
