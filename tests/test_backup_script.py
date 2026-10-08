"""scripts/backup.sh, the prod backup loop (LUXANALYTI-14), run once against a stub pg_dump."""

import os
import stat
import subprocess
import time
from datetime import UTC, datetime, timedelta
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


def _run(
    backups: Path,
    tmp_path: Path,
    *,
    ok: bool,
    keep: int | str = 3,
    stamp: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """One iteration. `stamp` stubs `date` (the dump's name), so runs need not be a second apart."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub_pg_dump(bin_dir, ok=ok)
    date_stub = bin_dir / "date"
    date_stub.unlink(missing_ok=True)
    if stamp is not None:
        date_stub.write_text(f"#!/bin/sh\necho {stamp}\n")
        date_stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BACKUP_DIR": str(backups),
        "BACKUP_KEEP": str(keep),
        "BACKUP_ONCE": "1",
    }
    return subprocess.run(
        ["sh", str(SCRIPT)], env=env, check=check, capture_output=True
    )


def _dated(backups: Path, days_ago: int) -> str:
    """A scheduled dump made `days_ago` days ago: its stamp and its mtime agree."""
    when = datetime.now(UTC) - timedelta(days=days_ago)
    name = f"luxanalytics-{when:%Y%m%dT%H%M%S}Z.sql.gz"
    (backups / name).write_text("good")
    os.utime(backups / name, (when.timestamp(), when.timestamp()))
    return name


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


def test_a_burst_of_restarts_keeps_the_daily_history(
    backups: Path, tmp_path: Path
) -> None:
    """Every start dumps at once: 14 restarts in a day (an incident, reboots) must not push out
    the last two weeks' daily dumps, which a count-only rotation did (adversarial pass 4)."""
    daily = [_dated(backups, d) for d in range(1, 15)]
    for n in range(14):
        _run(backups, tmp_path, ok=True, keep=14, stamp=f"20991231T0000{n:02d}Z")
    left = {p.name for p in backups.iterdir()}
    assert set(daily[:13]) <= left  # 1..13 days old: inside the 14 days kept
    assert daily[13] not in left  # 14 days old, and more than 14 dumps exist


def test_a_dump_stamped_in_the_future_never_displaces_a_real_one(
    backups: Path, tmp_path: Path
) -> None:
    future = time.time() + 3 * 365 * 86400
    for n in range(14):
        name = f"luxanalytics-209901{n + 1:02d}T000000Z.sql.gz"
        (backups / name).write_text("ahead")
        os.utime(backups / name, (future, future))
    _run(backups, tmp_path, ok=True, keep=14)
    assert [p for p in backups.iterdir() if not p.name.startswith("luxanalytics-2099")]


@pytest.mark.parametrize("keep", ["0", "14d", "-3"])
def test_a_bad_keep_stops_before_touching_anything(
    backups: Path, tmp_path: Path, keep: str
) -> None:
    old = _dated(backups, 30)
    result = _run(backups, tmp_path, ok=True, keep=keep, check=False)
    assert result.returncode != 0
    assert [p.name for p in backups.iterdir()] == [old]
