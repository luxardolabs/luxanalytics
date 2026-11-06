"""Every file under app/ and alembic/ reaches the image, except the ones named here.

.dockerignore allowlists what the image copies from those two directories, so a file the allowlist
does not cover builds cleanly and only goes missing at run time. The test matches each file against
the patterns of .dockerignore with Docker's rules: the last pattern that matches a path (or one of
its parent directories) decides, a leading `!` re-includes, `*` stays inside one directory and `**`
crosses directories.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROOTS = ("app", "alembic")
# Read by people and by the release step on the host, never by the running app, so the image leaves
# them out: .dockerignore excludes every one of these paths, and the test fails if one ships.
NOT_SHIPPED_DIRS = ("app/change_logs/", "app/release_notes/")
NOT_SHIPPED_SUFFIX = ".md"

Rules = list[tuple[bool, re.Pattern[str]]]


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """The regular expression for one .dockerignore pattern, translated the way Docker does."""
    out = ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                while pattern[i : i + 1] == "*":
                    i += 1
                if pattern[i : i + 1] == "/":
                    i += 1
                    out += "(.*/)?"
                else:
                    out += ".*"
                continue
            out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        elif char == "[":
            end = pattern.index("]", i + 1)
            out += pattern[i : end + 1]
            i = end
        elif char == "\\" and i + 1 < len(pattern):
            i += 1
            out += re.escape(pattern[i])
        else:
            out += re.escape(char)
        i += 1
    return re.compile(f"^{out}$")


def _rules(text: str) -> Rules:
    """(excludes, matcher) for each pattern line, in file order."""
    rules: Rules = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keep = line.startswith("!")
        rules.append((not keep, _pattern_regex(line.removeprefix("!").strip("/"))))
    return rules


def _ships(path: str, rules: Rules) -> bool:
    """True when the image's build context holds the file at `path`."""
    parts = path.split("/")
    candidates = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    excluded = False
    for excludes, matcher in rules:
        if any(matcher.match(candidate) for candidate in candidates):
            excluded = excludes
    return not excluded


def _text() -> str:
    return (REPO / ".dockerignore").read_text()


def _files() -> list[str]:
    return sorted(
        path.relative_to(REPO).as_posix()
        for root in ROOTS
        for path in (REPO / root).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def _is_not_shipped(path: str) -> bool:
    return path.startswith(NOT_SHIPPED_DIRS) and path.endswith(NOT_SHIPPED_SUFFIX)


def test_every_file_under_app_and_alembic_reaches_the_image() -> None:
    rules = _rules(_text())

    missing = [
        path
        for path in _files()
        if not _is_not_shipped(path) and not _ships(path, rules)
    ]

    assert not missing, (
        f"the .dockerignore allowlist drops these from the image: {missing}"
    )


def test_the_files_the_image_leaves_out_are_left_out() -> None:
    rules = _rules(_text())
    left_out = [path for path in _files() if _is_not_shipped(path)]

    shipped = [path for path in left_out if _ships(path, rules)]

    assert left_out, "nothing is named as left out any more: drop NOT_SHIPPED_DIRS"
    assert not shipped, (
        f"these ship although the test names them as left out: {shipped}"
    )


@pytest.mark.parametrize(
    "path",
    [
        "app/static/img/logo.svg",
        "app/static/partials/help.html",
        "app/services/report.js",
        "app/services/mail/body.html",
        "app/services/mail/style.css",
        "app/templates/web/print.css",
        "app/templates/web/widget.js",
        "app/core/icon.svg",
        "app/static/favicon.ico",
        "alembic/versions/notes.txt",
    ],
)
def test_a_file_in_a_directory_the_allowlist_does_not_cover_is_dropped(
    path: str,
) -> None:
    assert not _ships(path, _rules(_text()))


@pytest.mark.parametrize(
    "path",
    [
        "app/main.py",
        "app/core/deep/nested/module.py",
        "app/templates/web/pages/dashboard/index.html",
        "app/templates/web/icons/mark.svg",
        "app/static/css/app.css",
        "app/static/js/app.js",
        "app/static/vendor/htmx/htmx.min.js",
        "alembic/env.py",
        "alembic/versions/001_init.py",
        "alembic/script.py.mako",
    ],
)
def test_a_file_the_allowlist_covers_ships(path: str) -> None:
    assert _ships(path, _rules(_text()))


@pytest.mark.parametrize(
    ("dropped_line", "path"),
    [
        ("!app/**/*.py", "app/main.py"),
        ("!app/static/**/*.js", "app/static/js/app.js"),
        ("!alembic/**/*.py", "alembic/env.py"),
        ("!app/templates/**/*.html", "app/templates/web/layouts/base.html"),
        ("!app/templates/**/*.svg", "app/templates/web/icons/mark.svg"),
        ("!app/static/**/*.css", "app/static/css/app.css"),
        ("!alembic/script.py.mako", "alembic/script.py.mako"),
    ],
)
def test_removing_an_allowlist_line_drops_the_files_it_covers(
    dropped_line: str, path: str
) -> None:
    lines = _text().splitlines()
    assert dropped_line in lines

    rules = _rules("\n".join(line for line in lines if line != dropped_line))

    assert not _ships(path, rules)


def test_the_last_matching_pattern_decides() -> None:
    assert _ships("a/x.py", _rules("a/**\n!a/**/*.py"))
    assert not _ships("a/x.py", _rules("!a/**/*.py\na/**"))


def test_double_star_crosses_directories_and_single_star_does_not() -> None:
    assert not _ships("a/b/c/x.log", _rules("**/*.log"))
    assert not _ships("x.log", _rules("**/*.log"))
    assert not _ships("a/x.log", _rules("a/*.log"))
    assert _ships("a/b/x.log", _rules("a/*.log"))


def test_a_pattern_naming_a_directory_drops_what_is_inside_it() -> None:
    assert not _ships("deploy/prod/.env.prod", _rules("deploy/"))
