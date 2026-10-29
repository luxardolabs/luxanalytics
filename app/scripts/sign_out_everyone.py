"""Sign every dashboard session out: move the session generation to a fresh, random one.

`make restore` and `make prod-restore` run it after restoring a dump, with the app stopped, since a
dump taken before dumps left the session generation out carries one a later logout may have
revoked. `python -m app.scripts.sign_out_everyone` in a one-off app container.
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
