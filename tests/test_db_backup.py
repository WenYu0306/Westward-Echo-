"""Tests for src/db_backup.py — bounded point-in-time copies."""

from src.db_backup import backup_sqlite


class TestBackupSqlite:
    def test_missing_database_returns_none(self, tmp_path):
        assert backup_sqlite(tmp_path / "nope.db") is None

    def test_copy_is_byte_identical(self, tmp_path):
        db = tmp_path / "store.db"
        db.write_bytes(b"sqlite-bytes")
        out = backup_sqlite(db)
        assert out is not None and out.exists()
        assert out.read_bytes() == db.read_bytes()

    def test_retention_prunes_oldest(self, tmp_path):
        db = tmp_path / "store.db"
        db.write_bytes(b"x")
        made = [backup_sqlite(db, retain=3) for _ in range(5)]
        remaining = sorted(tmp_path.glob("store.db.bak_*"))
        assert len(remaining) == 3
        assert made[-1].exists()
