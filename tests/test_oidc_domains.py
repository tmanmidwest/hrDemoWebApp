"""Tests for the OIDC email-domain allowlist (per-provider access control).

Covers the pure helpers in app.services.oidc that decide whether an
authenticated subject may sign in given a provider's `allowed_domains`.
"""

from __future__ import annotations

import pytest

from app.models import AuthProvider
from app.services.oidc import domain_allowed, parse_allowed_domains


def _provider(allowed_domains: str) -> AuthProvider:
    return AuthProvider(
        slug="google",
        display_name="Google",
        issuer_url="https://accounts.google.com",
        client_id="client-abc",
        client_secret_encrypted="",
        scopes="openid email profile",
        allowed_domains=allowed_domains,
        is_enabled=True,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        (None, []),
        ("saviynt.com", ["saviynt.com"]),
        ("@saviynt.com", ["saviynt.com"]),
        ("saviynt.com, example.org", ["saviynt.com", "example.org"]),
        ("saviynt.com  example.org", ["saviynt.com", "example.org"]),
        ("Saviynt.COM", ["saviynt.com"]),
    ],
)
def test_parse_allowed_domains(raw: str | None, expected: list[str]) -> None:
    assert parse_allowed_domains(raw) == expected


def test_no_allowlist_allows_any_account() -> None:
    provider = _provider("")
    assert domain_allowed(provider, {"sub": "1", "email": "anyone@gmail.com"}) is True


def test_hd_claim_matches_allowlist() -> None:
    provider = _provider("saviynt.com")
    claims = {"sub": "1", "email": "user@saviynt.com", "hd": "saviynt.com"}
    assert domain_allowed(provider, claims) is True


def test_personal_gmail_rejected_when_domain_restricted() -> None:
    provider = _provider("saviynt.com")
    # Personal Google accounts carry no `hd`; the gmail.com email domain isn't
    # on the list, so the sign-in is blocked.
    assert domain_allowed(provider, {"sub": "1", "email": "someone@gmail.com"}) is False


def test_email_domain_fallback_when_no_hd() -> None:
    provider = _provider("saviynt.com")
    claims = {"sub": "1", "email": "user@saviynt.com", "email_verified": True}
    assert domain_allowed(provider, claims) is True


def test_unverified_email_domain_rejected() -> None:
    provider = _provider("saviynt.com")
    claims = {"sub": "1", "email": "user@saviynt.com", "email_verified": False}
    assert domain_allowed(provider, claims) is False


def test_missing_email_and_hd_rejected_when_restricted() -> None:
    provider = _provider("saviynt.com")
    assert domain_allowed(provider, {"sub": "1"}) is False


def test_case_insensitive_domain_match() -> None:
    provider = _provider("saviynt.com")
    claims = {"sub": "1", "email": "User@Saviynt.com", "hd": "Saviynt.com"}
    assert domain_allowed(provider, claims) is True
