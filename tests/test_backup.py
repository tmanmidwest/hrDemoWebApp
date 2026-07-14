"""Tests for full-instance backup and restore.

The service functions are exercised directly for the create/round-trip logic;
the UI routes are hit only for auth and confirmation-gate behavior.
"""

from __future__ import annotations

import io
import json
import zipfile

import pyzipper
import pytest
from fastapi.testclient import TestClient

PASSWORD = "verysecure123"


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------


def test_create_backup_unencrypted_contents(client: TestClient) -> None:
    # `client` triggers app startup (migrations + seed). Force the lazily-created
    # JWT and provider keys into existence so the backup bundles all three.
    from app.config import get_settings
    from app.services.backup import create_backup

    settings = get_settings()
    settings.get_or_create_jwt_signing_key()
    settings.get_or_create_provider_secret_key()

    data, filename = create_backup(None)
    assert filename.startswith("hrsot-backup-") and filename.endswith(".zip")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "hrsot.db" in names
        assert "manifest.json" in names
        assert "keys/session_secret" in names
        assert "keys/jwt_signing_key" in names
        assert "keys/provider_secret_key" in names
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["encrypted"] is False
    assert manifest["alembic_revision"]  # a head revision string
    assert set(manifest["members"]) == set(names) - {"manifest.json"}


def test_create_backup_encrypted_requires_password(client: TestClient) -> None:
    from app.services.backup import create_backup

    data, _ = create_backup(PASSWORD)

    # Every member (incl. the manifest) is encrypted — reading without the
    # password fails.
    with pyzipper.AESZipFile(io.BytesIO(data)) as zf:
        with pytest.raises(RuntimeError):
            zf.read("hrsot.db")

    # With the right password it succeeds.
    with pyzipper.AESZipFile(io.BytesIO(data)) as zf:
        zf.setpassword(PASSWORD.encode())
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["encrypted"] is True
        assert zf.read("hrsot.db").startswith(b"SQLite format 3")


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------


def _country_count(name: str) -> int:
    from app.db import get_session_factory
    from app.models import Country

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        return db.query(Country).filter(Country.name == name).count()


def _add_country(name: str) -> None:
    from app.db import get_session_factory
    from app.models import Country

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        db.add(Country(code="ZZ", name=name, is_active=True))
        db.commit()


def test_restore_reverts_changes(client: TestClient) -> None:
    from app.services.backup import create_backup, restore_backup

    # Snapshot, then mutate after the snapshot.
    data, _ = create_backup(None)
    assert _country_count("Atlantis") == 0
    _add_country("Atlantis")
    assert _country_count("Atlantis") == 1

    # Restoring the earlier snapshot should drop the post-snapshot country.
    restore_backup(data, None)
    assert _country_count("Atlantis") == 0


def test_restore_wrong_password_raises(client: TestClient) -> None:
    from app.services.backup import BackupError, create_backup, restore_backup

    data, _ = create_backup(PASSWORD)
    _add_country("Atlantis")
    with pytest.raises(BackupError):
        restore_backup(data, "not-the-password")
    # Data left untouched by the failed restore.
    assert _country_count("Atlantis") == 1


def test_restore_rejects_non_backup(client: TestClient) -> None:
    from app.services.backup import BackupError, restore_backup

    with pytest.raises(BackupError):
        restore_backup(b"this is not a zip", None)


# ---------------------------------------------------------------------------
# Routes — auth + confirmation gate
# ---------------------------------------------------------------------------


def _login_admin(client: TestClient) -> TestClient:
    from app.config import get_settings

    settings = get_settings()
    resp = client.post(
        "/ui/login",
        data={
            "username": settings.initial_admin_username,
            "password": settings.initial_admin_password,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


def _login_role(client: TestClient, role: str) -> TestClient:
    from app.db import get_session_factory
    from app.models import AppUser
    from app.services.passwords import hash_password

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        db.add(
            AppUser(
                username=f"{role}_user",
                password_hash=hash_password(PASSWORD),
                role=role,
                is_active=True,
                is_seeded=False,
            )
        )
        db.commit()
    resp = client.post(
        "/ui/login",
        data={"username": f"{role}_user", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


def test_backup_page_admin_only(client: TestClient) -> None:
    _login_admin(client)
    assert client.get("/ui/settings/backup").status_code == 200


@pytest.mark.parametrize("role", ["management", "view_only"])
def test_backup_page_forbidden_for_non_admins(client: TestClient, role: str) -> None:
    _login_role(client, role)
    resp = client.get("/ui/settings/backup", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/employees"


def test_download_backup_returns_zip(client: TestClient) -> None:
    _login_admin(client)
    resp = client.post("/ui/settings/backup/download", data={"password": ""})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "hrsot.db" in zf.namelist()


def test_restore_requires_confirm_phrase(client: TestClient) -> None:
    _login_admin(client)
    _add_country("Atlantis")
    resp = client.post(
        "/ui/settings/backup/restore",
        data={"password": "", "confirm": "nope"},
        files={"file": ("backup.zip", b"irrelevant", "application/zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # The restore never ran, so the mutation is still present.
    assert _country_count("Atlantis") == 1


def test_restore_via_route_reverts_and_stays_healthy(client: TestClient) -> None:
    """Full round-trip through the HTTP route.

    Regression guard: the route holds a DB session open via its auth dependency,
    so restore must release it before swapping the database file or SQLite throws
    a disk I/O error. After a successful restore the app must still serve.
    """
    _login_admin(client)

    # Baseline backup, then mutate after it.
    dl = client.post("/ui/settings/backup/download", data={"password": ""})
    assert dl.status_code == 200
    baseline = dl.content
    _add_country("Atlantis")
    assert _country_count("Atlantis") == 1

    resp = client.post(
        "/ui/settings/backup/restore",
        data={"password": "", "confirm": "RESTORE"},
        files={"file": ("baseline.zip", baseline, "application/zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Reverted, and the rebuilt engine still serves requests.
    assert _country_count("Atlantis") == 0
    assert client.get("/health").json()["database"] == "ok"
    assert client.get("/ui/settings/backup").status_code == 200


# ---------------------------------------------------------------------------
# Backup export API (bearer auth)
# ---------------------------------------------------------------------------


def test_backup_api_requires_auth(client: TestClient) -> None:
    assert client.post("/api/v1/backup").status_code == 401


def test_backup_api_returns_zip(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/backup")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "hrsot.db" in zf.namelist()


def test_backup_api_encrypts_with_password(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/backup", json={"password": PASSWORD})
    assert resp.status_code == 200
    with pyzipper.AESZipFile(io.BytesIO(resp.content)) as zf:
        with pytest.raises(RuntimeError):
            zf.read("hrsot.db")
        zf.setpassword(PASSWORD.encode())
        assert zf.read("hrsot.db").startswith(b"SQLite format 3")
