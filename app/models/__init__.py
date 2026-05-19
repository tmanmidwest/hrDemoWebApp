"""SQLAlchemy ORM models for the Demo HR SoT App.

Import order matters here because of foreign keys — leaf tables first,
then tables that reference them.
"""

from app.models.api_key import ApiKey
from app.models.app_user import AppUser
from app.models.country import Country
from app.models.department import Department
from app.models.employee import Employee
from app.models.employment_status import EmploymentStatus
from app.models.job_title import JobTitle
from app.models.oauth_client import OAuthClient
from app.models.state_province import StateProvince

__all__ = [
    "ApiKey",
    "AppUser",
    "Country",
    "Department",
    "Employee",
    "EmploymentStatus",
    "JobTitle",
    "OAuthClient",
    "StateProvince",
]
