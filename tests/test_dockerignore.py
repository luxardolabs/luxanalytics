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
    return re.compile(out, re.DOTALL)


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
    """True when the image's build context holds the file at `path`.

    The way BuildKit walks it (moby's `MatchesUsingParentResults`): each directory from the root
    down is matched, and a path takes the result of every pattern its parent directory matched
    without matching it again. A pattern that cannot change the verdict is not evaluated: a plain
    pattern once the path is excluded, a `!` pattern while it is not. So a later exclude is skipped
    on a directory an earlier pattern excluded, and an allowlisted file inside such a directory
    ships (`app/.aws/c.py`), as does everything inside a directory the allowlist names
    (`app/foo.py/dump.tar`).
    """
    parent: list[bool] | None = None
    excluded = False
    parts = path.split("/")
    for depth in range(1, len(parts) + 1):
        candidate = "/".join(parts[:depth])
        excluded = False
        hits: list[bool] = []
        for index, (excludes, matcher) in enumerate(rules):
            hit = parent[index] if parent is not None else False
            if not hit:
                if excludes == excluded:
                    hits.append(False)
                    continue
                hit = matcher.fullmatch(candidate) is not None
            if hit:
                excluded = excludes
            hits.append(hit)
        parent = hits
    return not excluded


def _text() -> str:
    return (REPO / ".dockerignore").read_text()


def _entries(*, directories: bool) -> list[str]:
    """The directories (or the files) under app/ and alembic/. A symlink is a file entry, judged by
    its own name as the build does, whatever it points at."""
    return sorted(
        path.relative_to(REPO).as_posix()
        for root in ROOTS
        for path in (REPO / root).rglob("*")
        if "__pycache__" not in path.parts
        and (
            (path.is_dir() and not path.is_symlink())
            if directories
            else (path.is_file() or path.is_symlink())
        )
    )


def _files() -> list[str]:
    return _entries(directories=False)


def _directories_that_ship(directories: list[str], rules: Rules) -> list[str]:
    """The directories whose own name an allow pattern covers: everything inside one ships."""
    return [d for d in directories if d not in ROOTS and _ships(d, rules)]


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


def test_no_directory_is_named_like_a_file_the_allowlist_ships() -> None:
    """`app/foo.py/` matches `!app/**/*.py`, and then every file inside it ships, whatever it is."""
    rules = _rules(_text())

    named = _directories_that_ship(_entries(directories=True), rules)

    assert not named, f"rename these: all that is inside them ships: {named}"


@pytest.mark.parametrize(
    "directory",
    ["app/foo.py", "app/static/img.css", "app/templates/x.html", "alembic/v.py"],
)
def test_a_directory_named_like_an_allowed_type_is_found(directory: str) -> None:
    rules = _rules(_text())

    assert _directories_that_ship(["app", "app/api", directory], rules) == [directory]


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


@pytest.mark.parametrize(
    "path",
    [
        "app/.aws/c.py",
        "app/.git/hooks/h.py",
        "app/sub/foo.key/c.py",
        "app/__pycache__/m.py",
        "app/static/node_modules/a.js",
        "app/foo.py/dump.tar",
    ],
)
def test_an_allowed_file_ships_inside_a_directory_a_secret_pattern_names(
    path: str,
) -> None:
    """BuildKit skips a later exclude on a directory an earlier pattern already excluded (measured
    with a real build): `app/**` excluded the directory, so `**/.aws/` never ran on it, and the file
    the allowlist names comes through."""
    assert _ships(path, _rules(_text()))


@pytest.mark.parametrize("path", ["app/.aws/c.txt", "app/.git/config", "app/x.py\n"])
def test_a_file_the_allowlist_does_not_name_is_dropped_in_any_directory(
    path: str,
) -> None:
    assert not _ships(path, _rules(_text()))


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
