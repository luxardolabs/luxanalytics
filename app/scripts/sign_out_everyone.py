"""Sign every dashboard session out: move the session generation to a fresh, random one.

Run after restoring the database from a dump (`make restore` does): the restored row carries the
generation of the dump's time, which a cookie revoked by a later logout may still hold.
`python -m app.scripts.sign_out_everyone` inside the app container.
"""

import asyncio

from app.crud.dashboard_session_crud import reset_session_generation


def main() -> None:
    asyncio.run(reset_session_generation())
    print("Every dashboard session is signed out.")


if __name__ == "__main__":
    main()
