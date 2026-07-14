"""SQLAlchemy ORM models for the Demo HR SoT App.

Import order matters here because of foreign keys — leaf tables first,
then tables that reference them.
"""

from app.models.api_key import ApiKey
from app.models.app_branding import AppBranding
from app.models.app_config import AppConfig
from app.models.app_user import AppUser, UserRole
from app.models.audit_event import AuditEvent
from app.models.auth_provider import AuthProvider
from app.models.country import Country
from app.models.department import Department
from app.models.employee import Employee
from app.models.employment_status import EmploymentStatus
from app.models.job_title import JobTitle
from app.models.location import Location
from app.models.oauth_client import OAuthClient
from app.models.state_province import StateProvince
from app.models.user_identity import UserIdentity

__all__ = [
    "ApiKey",
    "AppBranding",
    "AppConfig",
    "AppUser",
    "AuditEvent",
    "AuthProvider",
    "Country",
    "Department",
    "Employee",
    "EmploymentStatus",
    "JobTitle",
    "Location",
    "OAuthClient",
    "StateProvince",
    "UserIdentity",
    "UserRole",
]
