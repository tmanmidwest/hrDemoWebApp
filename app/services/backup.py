"""Full-instance backup and restore.

A backup is a `.zip` containing the SQLite database plus the on-disk secret
files (session secret, JWT signing key, provider secret key). Bundling the keys
means a restore is a faithful clone — in particular, OIDC provider client
secrets (encrypted at rest with ``provider_secret_key``; see
:mod:`app.services.secret_box`) stay decryptable on the restored instance.

The zip is optionally AES-256 encrypted with a user-supplied password via
``pyzipper`` (the stdlib ``zipfile`` cannot write encrypted archives).

Caveat: the session-cookie signing secret is read once by ``SessionMiddleware``
at app startup, so a restored ``session_secret`` only takes full effect after a
process restart. The live swap performed by :func:`restore_backup` covers the
database and the provider/JWT secrets immediately (it resets the engine and the
relevant caches); existing login sessions are expected to require re-login.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pyzipper

from app.config import get_settings

log = logging.getLogger(__name__)

# Zip member names.
DB_MEMBER = "hrsot.db"
MANIFEST_NAME = "manifest.json"

# zip-member name -> Settings path attribute for each secret file we bundle.
KEY_FILES: dict[str, str] = {
    "keys/session_secret": "session_secret_path",
    "keys/jwt_signing_key": "jwt_signing_key_path",
    "keys/provider_secret_key": "provider_secret_key_path",
}


class BackupError(Exception):
    """A backup could not be created or a restore could not be applied."""


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def _snapshot_database() -> tuple[bytes, str | None]:
    """Return a consistent copy of the SQLite DB and its alembic revision.

    Uses SQLite's online-backup API so the snapshot is consistent even while the
    app holds the live database open in WAL mode.
    """
    database_path = get_settings().database_path
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".db")
    os.close(tmp_fd)
    try:
        src = sqlite3.connect(str(database_path))
        try:
            dst = sqlite3.connect(tmp_name)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        revision: str | None = None
        probe = sqlite3.connect(tmp_name)
        try:
            row = probe.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = row[0] if row else None
        except sqlite3.OperationalError:
            revision = None  # table absent — shouldn't happen post-migration
        finally:
            probe.close()

        return Path(tmp_name).read_bytes(), revision
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def create_backup(password: str | None) -> tuple[bytes, str]:
    """Build a backup zip of the whole instance.

    Returns ``(zip_bytes, filename)``. When ``password`` is a non-empty string
    the archive is AES-256 encrypted; otherwise it is a plain (unencrypted) zip.
    """
    settings = get_settings()
    db_bytes, revision = _snapshot_database()

    members: list[str] = [DB_MEMBER]
    key_payloads: dict[str, bytes] = {}
    for member, path_attr in KEY_FILES.items():
        path: Path = getattr(settings, path_attr)
        if path.exists():
            key_payloads[member] = path.read_bytes()
            members.append(member)

    encrypted = bool(password)
    manifest = {
        "app_version": settings.app_version,
        "alembic_revision": revision,
        "created_at": datetime.now(UTC).isoformat(),
        "encrypted": encrypted,
        "members": members,
    }

    buf = io.BytesIO()
    zip_kwargs = {"compression": pyzipper.ZIP_DEFLATED}
    if encrypted:
        zip_kwargs["encryption"] = pyzipper.WZ_AES
    with pyzipper.AESZipFile(buf, "w", **zip_kwargs) as zf:
        if encrypted:
            zf.setpassword(password.encode("utf-8"))
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        zf.writestr(DB_MEMBER, db_bytes)
        for member, payload in key_payloads.items():
            zf.writestr(member, payload)

    log.info(
        "backup_created",
        extra={"encrypted": encrypted, "members": len(members), "size": buf.tell()},
    )
    return buf.getvalue(), f"hrsot-backup-{_timestamp()}.zip"


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _read_members(file_bytes: bytes, password: str | None) -> dict[str, bytes]:
    """Extract all members from a (possibly encrypted) backup zip.

    Wraps every failure mode (not a zip, wrong/missing password) in BackupError.
    """
    try:
        with pyzipper.AESZipFile(io.BytesIO(file_bytes)) as zf:
            if password:
                zf.setpassword(password.encode("utf-8"))
            names = zf.namelist()
            if DB_MEMBER not in names:
                raise BackupError(
                    "This does not look like a valid backup (missing hrsot.db)."
                )
            return {name: zf.read(name) for name in names}
    except BackupError:
        raise
    except Exception as exc:
        # pyzipper raises RuntimeError for a bad/missing password and its own
        # BadZipFile for a non-zip upload; treat any read failure as bad input.
        raise BackupError(
            "Could not read the backup. Check the password and that the file is a "
            "valid backup zip."
        ) from exc


def restore_backup(file_bytes: bytes, password: str | None) -> dict:
    """Replace this instance's data with the contents of a backup zip.

    Swaps the database and secret files in place, resets the DB engine and
    caches, then runs migrations to bring an older backup up to the current
    schema. Returns the backup's manifest dict (best-effort).

    Raises BackupError on any failure to read or apply the backup.
    """
    settings = get_settings()
    members = _read_members(file_bytes, password)

    manifest: dict = {}
    if MANIFEST_NAME in members:
        try:
            manifest = json.loads(members[MANIFEST_NAME].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            manifest = {}

    # Reset the live engine before touching files on disk.
    import app.db as db_module

    try:
        db_module.get_engine().dispose()
    except Exception:  # pragma: no cover - dispose should not raise
        log.warning("backup_restore_engine_dispose_failed", exc_info=True)
    db_module._engine = None  # type: ignore[attr-defined]
    db_module._SessionLocal = None  # type: ignore[attr-defined]

    settings.ensure_data_dir()

    # Swap the database file and drop stale WAL sidecars.
    settings.database_path.write_bytes(members[DB_MEMBER])
    for sidecar in ("-wal", "-shm"):
        Path(str(settings.database_path) + sidecar).unlink(missing_ok=True)

    # Restore whichever secret files the backup carried.
    for member, path_attr in KEY_FILES.items():
        if member in members:
            path: Path = getattr(settings, path_attr)
            path.write_bytes(members[member])
            path.chmod(0o600)

    # Invalidate caches that hold the now-replaced data/secrets.
    from app.services import branding, secret_box, system_config

    secret_box._fernet.cache_clear()  # type: ignore[attr-defined]
    branding.invalidate()
    system_config._cache = None  # type: ignore[attr-defined]

    # Bring an older backup's schema up to head.
    from app.services.migrations import run_migrations

    try:
        run_migrations()
    except Exception as exc:
        raise BackupError(f"Restore failed while upgrading the schema: {exc}") from exc

    log.warning(
        "backup_restored",
        extra={
            "app_version": manifest.get("app_version"),
            "alembic_revision": manifest.get("alembic_revision"),
        },
    )
    return manifest
