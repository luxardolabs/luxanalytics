"""scripts/backup.sh, the prod backup loop (LUXANALYTI-14), run once against a stub pg_dump."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"
OURS = "luxanalytics-20261001T000000Z.sql.gz"


def _stub_pg_dump(bin_dir: Path, *, ok: bool) -> None:
    """A pg_dump that writes its -f file and succeeds, or writes half of one and fails."""
    stub = bin_dir / "pg_dump"
    body = 'while [ "$1" != -f ]; do shift; done; echo dump > "$2"; '
    stub.write_text(f"#!/bin/sh\n{body}exit {0 if ok else 1}\n")
    stub.chmod(0o755)


def _run(backups: Path, tmp_path: Path, *, ok: bool, keep: int = 3) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub_pg_dump(bin_dir, ok=ok)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BACKUP_DIR": str(backups),
        "BACKUP_KEEP": str(keep),
        "BACKUP_ONCE": "1",
    }
    subprocess.run(["sh", str(SCRIPT)], env=env, check=True, capture_output=True)


def _scheduled(n: int) -> list[str]:
    return [f"luxanalytics-2026090{i}T000000Z.sql.gz" for i in range(1, n + 1)]


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


def test_a_failed_dump_deletes_nothing(backups: Path, tmp_path: Path) -> None:
    for name in [*_scheduled(5), OURS]:
        (backups / name).write_text("old")
        os.utime(backups / name, (0, 0))  # far past any age cut-off
    _run(backups, tmp_path, ok=False)
    assert sorted(p.name for p in backups.iterdir()) == sorted([*_scheduled(5), OURS])


def test_rotation_keeps_the_newest_and_never_touches_other_dumps(
    backups: Path, tmp_path: Path
) -> None:
    manual = "luxanalytics-backup-20260101.sql.gz"
    for name in [*_scheduled(5), manual]:
        (backups / name).write_text("old")
        os.utime(backups / name, (0, 0))
    sub = backups / "pre-009"
    sub.mkdir()
    (sub / "luxanalytics-20250101T000000Z.sql.gz").write_text("safety")
    os.utime(sub / "luxanalytics-20250101T000000Z.sql.gz", (0, 0))

    _run(backups, tmp_path, ok=True, keep=3)

    names = sorted(p.name for p in backups.iterdir() if p.is_file())
    scheduled = [n for n in names if n not in (manual,)]
    assert len(scheduled) == 3
    assert _scheduled(5)[-2:] == [
        n for n in scheduled if n.startswith("luxanalytics-2026090")
    ]
    assert manual in names
    assert (sub / "luxanalytics-20250101T000000Z.sql.gz").exists()


def test_dumps_are_private_and_stale_parts_are_cleared(
    backups: Path, tmp_path: Path
) -> None:
    (backups / "luxanalytics-20260101T000000Z.sql.gz.part").write_text("interrupted")
    _run(backups, tmp_path, ok=True)
    files = list(backups.iterdir())
    assert not [p for p in files if p.name.endswith(".part")]
    (dump,) = files
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
