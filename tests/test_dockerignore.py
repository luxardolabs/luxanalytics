"""Every file type under app/ and alembic/ reaches the image.

.dockerignore allowlists what they may ship, so a new file type left out of it builds cleanly and
only goes missing at run time.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED = {
    "app": {".py", ".html", ".svg", ".css", ".js"},
    "alembic": {".py", ".mako"},
}
# Under app/, read by people and by the release step, never by the running app.
NOT_SHIPPED = {"change_logs", "release_notes"}


def _source_files(root: str) -> list[Path]:
    return [
        path
        for path in (REPO / root).rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and not NOT_SHIPPED.intersection(path.parts)
    ]


def test_every_file_under_app_and_alembic_is_a_type_the_image_ships() -> None:
    strays = [
        str(path.relative_to(REPO))
        for root, suffixes in ALLOWED.items()
        for path in _source_files(root)
        if path.suffix not in suffixes
    ]

    assert not strays, f"allowlist these in .dockerignore, or remove them: {strays}"


def test_the_allowlist_names_each_file_type_it_is_checked_against() -> None:
    allowlist = (REPO / ".dockerignore").read_text()

    for root, suffixes in ALLOWED.items():
        for suffix in suffixes:
            assert f"!{root}/" in allowlist
            assert suffix in allowlist, f"{suffix} under {root}/"
