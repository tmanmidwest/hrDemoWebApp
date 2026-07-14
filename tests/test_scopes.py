"""Unit tests for the API-key scope helpers."""

from __future__ import annotations

from app.services import scopes


def test_admin_wildcard_matches_everything() -> None:
    assert scopes.has_scope({"admin"}, "employees:write") is True
    assert scopes.has_scope({"admin"}, "backup:create") is True


def test_exact_scope_match() -> None:
    granted = {"employees:read", "lookups:read"}
    assert scopes.has_scope(granted, "employees:read") is True
    assert scopes.has_scope(granted, "employees:write") is False


def test_empty_grants_nothing() -> None:
    assert scopes.has_scope(set(), "employees:read") is False


def test_validate_drops_unknown_scopes() -> None:
    result = scopes.validate(["employees:read", "bogus:scope", "backup:create"])
    assert result == ["employees:read", "backup:create"]  # catalog order, unknowns gone


def test_presets_are_valid_scopes() -> None:
    for preset_scopes in scopes.PRESETS.values():
        assert scopes.validate(preset_scopes) == scopes.validate(preset_scopes)
        for s in preset_scopes:
            assert s in scopes.VALID_SCOPES


def test_serialize_is_stable_and_deduped() -> None:
    assert scopes.serialize(["backup:create", "employees:read", "employees:read"]) == (
        "employees:read backup:create"
    )
