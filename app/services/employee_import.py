"""CSV import/export for employees.

The DB stores relationships as integer foreign keys (``department_id``,
``job_title_id``, ...), but a human filling out a spreadsheet types *names*
("Engineering", "Senior Engineer"). This module is the translation layer:

* :func:`build_template_csv` / :func:`export_employees_csv` emit CSV in a
  human-readable column shape (names, not IDs) so "export → edit → re-import"
  round-trips.
* :func:`parse_and_classify` reads an uploaded CSV, resolves every name back to
  an ID, validates each row (reusing :mod:`app.services.employee_validation`),
  and classifies it as **New**, **Update**, or **Error** — without writing
  anything. The UI shows that preview; the commit step re-runs this same
  function (stateless) and then persists the committable rows.

Design decisions baked in here:

* **Match key is ``employee_number``** (case-insensitive). If a row's number
  already exists → Update; otherwise → New.
* **On an Update, a blank cell means "leave unchanged."** Only non-empty cells
  overwrite. This mirrors how the edit form treats a blank SSN field, and lets
  a user send partial updates.
* **Supervisors are referenced by ``employee_number``** and resolved in two
  phases so a supervisor can appear anywhere in the file (or already be in the
  DB): a forward reference to another valid row in the same file is accepted.
* **SSN is never emitted on export** (data-leak safety) — the column is present
  but blank. Import still accepts it.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import cast

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    Location,
    StateProvince,
)
from app.services.employee_validation import (
    normalize_ssn,
    validate_dob,
    validate_ssn_format,
    validate_ssn_unique,
    validate_supervisor,
)

# ---------------------------------------------------------------------------
# Column spec — the CSV's human-readable shape
# ---------------------------------------------------------------------------

# Header order for both the template and the export. Keep these two in sync;
# tests assert the round-trip.
COLUMNS: list[str] = [
    "employee_number",
    "first_name",
    "middle_name",
    "last_name",
    "date_of_birth",
    "ssn",
    "address_line_1",
    "address_line_2",
    "city",
    "state_province",
    "postal_code",
    "country",
    "home_phone",
    "personal_email",
    "work_email",
    "cost_center",
    "employment_status",
    "department",
    "job_title",
    "location",
    "hire_date",
    "termination_date",
    "supervisor_employee_number",
]

# Columns a NEW row must have a non-blank value for. (Everything the DB marks
# NOT NULL, expressed in CSV terms.)
REQUIRED_FOR_NEW: list[str] = [
    "employee_number",
    "first_name",
    "last_name",
    "country",
    "employment_status",
    "department",
    "job_title",
    "hire_date",
]

# Human labels for the "changed fields" hint shown on Update rows.
FIELD_LABELS: dict[str, str] = {
    "first_name": "First name",
    "middle_name": "Middle name",
    "last_name": "Last name",
    "date_of_birth": "Date of birth",
    "ssn": "SSN",
    "address_line_1": "Address 1",
    "address_line_2": "Address 2",
    "city": "City",
    "state_province_id": "State/Province",
    "postal_code": "Postal code",
    "country_id": "Country",
    "home_phone": "Home phone",
    "personal_email": "Personal email",
    "work_email": "Work email",
    "cost_center": "Cost center",
    "employment_status_id": "Employment status",
    "department_id": "Department",
    "job_title_id": "Job title",
    "location_id": "Location",
    "hire_date": "Hire date",
    "termination_date": "Termination date",
    "supervisor_id": "Supervisor",
}

# One illustrative row shipped in the blank template so the format is obvious.
_EXAMPLE_ROWS: list[dict[str, str]] = [
    {
        "employee_number": "E10001",
        "first_name": "Jordan",
        "middle_name": "",
        "last_name": "Rivera",
        "date_of_birth": "1990-04-12",
        "ssn": "",
        "address_line_1": "123 Main St",
        "address_line_2": "",
        "city": "Chicago",
        "state_province": "Illinois",
        "postal_code": "60601",
        "country": "United States",
        "home_phone": "312-555-0100",
        "personal_email": "jordan.rivera@example.com",
        "work_email": "jrivera@company.com",
        "cost_center": "CC-100",
        "employment_status": "Active",
        "department": "Engineering",
        "job_title": "Software Engineer",
        "location": "Chicago HQ",
        "hire_date": "2026-01-15",
        "termination_date": "",
        "supervisor_employee_number": "E00001",
    },
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ImportRow:
    """One parsed data row and its verdict."""

    line: int  # 1-based data line number (header is line 0)
    employee_number: str
    display_name: str
    intent: str  # "new" | "update"
    errors: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)  # human labels (updates)
    # Resolved, ready-to-apply data for committable rows. supervisor handled
    # separately in phase B via `supervisor_ref`.
    data: dict[str, object] = field(default_factory=dict)
    existing_id: int | None = None
    supervisor_ref: str | None = None  # target employee_number, if any

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ImportPreview:
    rows: list[ImportRow]
    parse_error: str | None = None  # fatal problem (bad header, empty file, ...)

    @property
    def new_count(self) -> int:
        return sum(1 for r in self.rows if r.ok and r.intent == "new")

    @property
    def update_count(self) -> int:
        return sum(1 for r in self.rows if r.ok and r.intent == "update")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.rows if not r.ok)

    @property
    def committable(self) -> list[ImportRow]:
        return [r for r in self.rows if r.ok]


# ---------------------------------------------------------------------------
# CSV builders (export / template)
# ---------------------------------------------------------------------------


def _write_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def build_template_csv() -> str:
    """A blank template: header row + one illustrative example row."""
    return _write_csv(_EXAMPLE_ROWS)


def export_employees_csv(db: Session) -> str:
    """Export the working roster (non-archived employees) in the import shape.

    SSN is intentionally left blank — we never emit full SSNs into a
    downloadable file. Import still accepts the column if the user fills it in.
    """
    # Reference managers are excluded from the export just like the API roster —
    # they're static stand-ins, not employees to round-trip through import.
    employees = (
        db.query(Employee)
        .filter(
            Employee.is_archived.is_(False),
            Employee.is_reference_manager.is_(False),
        )
        .order_by(Employee.employee_number)
        .all()
    )
    rows: list[dict[str, str]] = []
    for e in employees:
        rows.append(
            {
                "employee_number": e.employee_number,
                "first_name": e.first_name,
                "middle_name": e.middle_name or "",
                "last_name": e.last_name,
                "date_of_birth": e.date_of_birth.isoformat() if e.date_of_birth else "",
                "ssn": "",  # never exported
                "address_line_1": e.address_line_1 or "",
                "address_line_2": e.address_line_2 or "",
                "city": e.city or "",
                "state_province": e.state_province.name if e.state_province else "",
                "postal_code": e.postal_code or "",
                "country": e.country.name if e.country else "",
                "home_phone": e.home_phone or "",
                "personal_email": e.personal_email or "",
                "work_email": e.work_email or "",
                "cost_center": e.cost_center or "",
                "employment_status": e.employment_status.label
                if e.employment_status
                else "",
                "department": e.department.name if e.department else "",
                "job_title": e.job_title.name if e.job_title else "",
                "location": e.location.name if e.location else "",
                "hire_date": e.hire_date.isoformat() if e.hire_date else "",
                "termination_date": e.termination_date.isoformat()
                if e.termination_date
                else "",
                "supervisor_employee_number": e.supervisor.employee_number
                if e.supervisor
                else "",
            }
        )
    return _write_csv(rows)


# ---------------------------------------------------------------------------
# Value parsing / name resolution
# ---------------------------------------------------------------------------

# Date formats we accept on import. ISO first (what we emit); the others cover
# spreadsheets that reformat dates on the user.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


def _parse_date(raw: str) -> tuple[date | None, str | None]:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date(), None
        except ValueError:
            continue
    return None, f"'{raw}' is not a valid date (use YYYY-MM-DD)."


def _resolve_country(db: Session, raw: str) -> tuple[int | None, str | None]:
    lowered = raw.lower()
    country = (
        db.query(Country)
        .filter(
            (func.lower(Country.name) == lowered) | (func.lower(Country.code) == lowered)
        )
        .first()
    )
    if country is None:
        return None, f"Country '{raw}' not found."
    return country.id, None


def _resolve_state(
    db: Session, raw: str, country_id: int | None
) -> tuple[int | None, str | None]:
    if country_id is None:
        return None, "Cannot resolve State/Province without a valid Country."
    lowered = raw.lower()
    state = (
        db.query(StateProvince)
        .filter(
            StateProvince.country_id == country_id,
            (func.lower(StateProvince.name) == lowered)
            | (func.lower(StateProvince.code) == lowered),
        )
        .first()
    )
    if state is None:
        return None, f"State/Province '{raw}' not found for the given country."
    return state.id, None


def _resolve_status(db: Session, raw: str) -> tuple[int | None, str | None]:
    status = (
        db.query(EmploymentStatus)
        .filter(func.lower(EmploymentStatus.label) == raw.lower())
        .first()
    )
    if status is None:
        labels = ", ".join(
            s.label for s in db.query(EmploymentStatus).order_by(EmploymentStatus.value)
        )
        return None, f"Employment status '{raw}' not found. Known: {labels}."
    return status.id, None


def _resolve_department(db: Session, raw: str) -> tuple[int | None, str | None]:
    dept = (
        db.query(Department).filter(func.lower(Department.name) == raw.lower()).first()
    )
    if dept is None:
        return None, f"Department '{raw}' not found."
    return dept.id, None


def _resolve_job_title(
    db: Session, raw: str, department_id: int | None
) -> tuple[int | None, str | None]:
    if department_id is None:
        return None, "Cannot resolve Job title without a valid Department."
    matches = (
        db.query(JobTitle)
        .filter(
            JobTitle.department_id == department_id,
            func.lower(JobTitle.name) == raw.lower(),
        )
        .all()
    )
    if not matches:
        return None, f"Job title '{raw}' not found in that department."
    if len(matches) > 1:
        return None, f"Job title '{raw}' is ambiguous in that department."
    return matches[0].id, None


def _resolve_location(db: Session, raw: str) -> tuple[int | None, str | None]:
    loc = db.query(Location).filter(func.lower(Location.name) == raw.lower()).first()
    if loc is None:
        return None, f"Location '{raw}' not found."
    return loc.id, None


# ---------------------------------------------------------------------------
# Core: parse + classify
# ---------------------------------------------------------------------------


def parse_and_classify(db: Session, csv_text: str) -> ImportPreview:
    """Parse CSV text into a classified, validated preview. Writes nothing."""
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        raw_rows = list(reader)
    except csv.Error as exc:
        return ImportPreview(rows=[], parse_error=f"Could not read CSV: {exc}")

    if reader.fieldnames is None:
        return ImportPreview(rows=[], parse_error="The file is empty.")

    headers = {h.strip() for h in reader.fieldnames if h}
    missing = [c for c in REQUIRED_FOR_NEW if c not in headers]
    if missing:
        return ImportPreview(
            rows=[],
            parse_error=(
                "The CSV is missing required column(s): "
                + ", ".join(missing)
                + ". Download the template for the expected format."
            ),
        )

    if not raw_rows:
        return ImportPreview(rows=[], parse_error="The file has no data rows.")

    # Phase A: resolve everything except supervisor.
    rows: list[ImportRow] = []
    for idx, raw in enumerate(raw_rows, start=1):
        rows.append(_process_row(db, idx, raw))

    # Phase B: supervisor resolution, now that we know which employee_numbers
    # this file introduces. A supervisor reference is valid if it points at an
    # existing DB employee OR at another *error-free* row in this same file.
    _resolve_supervisors(db, rows)

    return ImportPreview(rows=rows)


def _cell(raw: dict[str, str], col: str) -> str:
    value = raw.get(col)
    return value.strip() if isinstance(value, str) else ""


def _process_row(db: Session, line: int, raw: dict[str, str]) -> ImportRow:
    emp_no = _cell(raw, "employee_number")
    first = _cell(raw, "first_name")
    last = _cell(raw, "last_name")
    display = " ".join(p for p in (first, last) if p) or "(unnamed)"

    existing: Employee | None = None
    if emp_no:
        existing = (
            db.query(Employee)
            .filter(func.lower(Employee.employee_number) == emp_no.lower())
            .first()
        )
    intent = "update" if existing is not None else "new"

    row = ImportRow(
        line=line,
        employee_number=emp_no,
        display_name=display,
        intent=intent,
        existing_id=existing.id if existing else None,
    )

    if not emp_no:
        row.errors.append("Employee number is required.")
        return row

    is_new = existing is None
    data: dict[str, object] = {}
    changes: list[str] = []

    # employee_number is the match key: set it on new rows; on updates leave the
    # existing value untouched (matching is case-insensitive, so we don't want a
    # differently-cased cell to silently rewrite it).
    if is_new:
        data["employee_number"] = emp_no

    def keep(field_name: str) -> object:  # current value on the existing record
        return getattr(existing, field_name) if existing is not None else None

    def note_change(model_field: str, new_value: object) -> None:
        """Record a resolved value, and (for updates) whether it changed."""
        data[model_field] = new_value
        if existing is not None and getattr(existing, model_field) != new_value:
            changes.append(FIELD_LABELS.get(model_field, model_field))

    # --- required-on-new presence check (blank on update = keep) ---
    if is_new:
        for col in REQUIRED_FOR_NEW:
            if not _cell(raw, col):
                label = FIELD_LABELS.get(col, col.replace("_", " ").title())
                row.errors.append(f"{label} is required for a new employee.")

    # --- plain string fields ---
    string_fields = [
        "first_name",
        "middle_name",
        "last_name",
        "address_line_1",
        "address_line_2",
        "city",
        "postal_code",
        "home_phone",
        "personal_email",
        "work_email",
        "cost_center",
    ]
    for f_name in string_fields:
        raw_val = _cell(raw, f_name)
        if raw_val == "":
            # Blank: keep existing on update, None on new.
            note_change(f_name, keep(f_name) if not is_new else None)
        else:
            note_change(f_name, raw_val)

    row.data = data  # attach now; the resolvers below keep mutating it

    # --- country (required-on-new) ---
    country_raw = _cell(raw, "country")
    country_id = cast("int | None", keep("country_id"))
    if country_raw:
        country_id, err = _resolve_country(db, country_raw)
        if err:
            row.errors.append(err)
    note_change("country_id", country_id)

    # --- state/province (optional, must belong to country) ---
    state_raw = _cell(raw, "state_province")
    state_id = keep("state_province_id")
    if state_raw:
        state_id, err = _resolve_state(db, state_raw, country_id)
        if err:
            row.errors.append(err)
    note_change("state_province_id", state_id)

    # --- employment status (required-on-new) ---
    status_raw = _cell(raw, "employment_status")
    status_id = keep("employment_status_id")
    if status_raw:
        status_id, err = _resolve_status(db, status_raw)
        if err:
            row.errors.append(err)
    note_change("employment_status_id", status_id)

    # --- department (required-on-new) ---
    dept_raw = _cell(raw, "department")
    dept_id = cast("int | None", keep("department_id"))
    if dept_raw:
        dept_id, err = _resolve_department(db, dept_raw)
        if err:
            row.errors.append(err)
    note_change("department_id", dept_id)

    # --- job title (required-on-new, must belong to the effective department) ---
    title_raw = _cell(raw, "job_title")
    title_id = keep("job_title_id")
    if title_raw:
        title_id, err = _resolve_job_title(db, title_raw, dept_id)
        if err:
            row.errors.append(err)
    else:
        # Blank on update keeps the old title, but if the department changed the
        # old title may no longer belong to it — validate the effective pair.
        if not is_new and title_id is not None and dept_id is not None:
            kept = db.get(JobTitle, title_id)
            if kept is not None and kept.department_id != dept_id:
                row.errors.append(
                    f"Existing job title '{kept.name}' does not belong to the "
                    "new department; set the job_title column too."
                )
    note_change("job_title_id", title_id)

    # --- location (optional) ---
    loc_raw = _cell(raw, "location")
    loc_id = keep("location_id")
    if loc_raw:
        loc_id, err = _resolve_location(db, loc_raw)
        if err:
            row.errors.append(err)
    note_change("location_id", loc_id)

    # --- dates ---
    dob = keep("date_of_birth")
    dob_raw = _cell(raw, "date_of_birth")
    if dob_raw:
        dob, err = _parse_date(dob_raw)
        if err:
            row.errors.append(err)
        elif dob is not None:
            try:
                validate_dob(dob)
            except HTTPException as exc:
                row.errors.append(exc.detail)
    note_change("date_of_birth", dob)

    hire = cast("date | None", keep("hire_date"))
    hire_raw = _cell(raw, "hire_date")
    if hire_raw:
        hire, err = _parse_date(hire_raw)
        if err:
            row.errors.append(err)
    note_change("hire_date", hire)

    term = cast("date | None", keep("termination_date"))
    term_raw = _cell(raw, "termination_date")
    if term_raw:
        term, err = _parse_date(term_raw)
        if err:
            row.errors.append(err)
    note_change("termination_date", term)

    if hire is not None and term is not None and term < hire:
        row.errors.append("Termination date must be on or after hire date.")

    # --- SSN (optional; blank on update keeps existing) ---
    ssn_raw = _cell(raw, "ssn")
    ssn = keep("ssn")
    if ssn_raw:
        ssn = normalize_ssn(ssn_raw)
        try:
            if ssn is not None:
                validate_ssn_format(ssn)
                validate_ssn_unique(db, ssn, excluding_employee_id=row.existing_id)
        except HTTPException as exc:
            row.errors.append(exc.detail)
    note_change("ssn", ssn)

    # Remember the supervisor reference for phase B.
    row.supervisor_ref = _cell(raw, "supervisor_employee_number") or None
    row.changes = changes
    return row


def _resolve_supervisors(db: Session, rows: list[ImportRow]) -> None:
    """Phase B: resolve ``supervisor_employee_number`` for every row.

    Valid targets are existing DB employees or other error-free rows in this
    same file (forward references). Self-reference and inactive/archived
    supervisors are rejected. On an update, a blank supervisor cell keeps the
    existing supervisor.
    """
    # employee_numbers this file introduces via committable (phase-A-clean) rows,
    # mapped to the row so we can check an in-file supervisor's effective status.
    file_rows: dict[str, ImportRow] = {
        r.employee_number.lower(): r
        for r in rows
        if r.ok and r.employee_number
    }

    active_status_ids = {
        s.id
        for s in db.query(EmploymentStatus).filter(
            EmploymentStatus.is_active_status.is_(True)
        )
    }

    for row in rows:
        ref = row.supervisor_ref
        if ref is None:
            # Keep existing supervisor on update; None on new.
            if row.existing_id is not None:
                existing = db.get(Employee, row.existing_id)
                row.data["supervisor_id"] = existing.supervisor_id if existing else None
            else:
                row.data["supervisor_id"] = None
            continue

        if ref.lower() == row.employee_number.lower():
            row.errors.append("An employee cannot be their own supervisor.")
            continue

        db_sup = (
            db.query(Employee)
            .filter(func.lower(Employee.employee_number) == ref.lower())
            .first()
        )
        if db_sup is not None:
            try:
                validate_supervisor(
                    db, db_sup.id, excluding_employee_id=row.existing_id
                )
            except HTTPException as exc:
                row.errors.append(exc.detail)
                continue
            row.data["supervisor_id"] = db_sup.id
            if (
                row.existing_id is not None
                and db_sup.id != getattr(db.get(Employee, row.existing_id), "supervisor_id", None)
            ):
                if FIELD_LABELS["supervisor_id"] not in row.changes:
                    row.changes.append(FIELD_LABELS["supervisor_id"])
            continue

        # Not in the DB — is it another valid row in this file?
        in_file = file_rows.get(ref.lower())
        if in_file is None:
            row.errors.append(
                f"Supervisor '{ref}' was not found (not in the system or this file)."
            )
            continue
        # Its effective employment status must be active.
        if in_file.data.get("employment_status_id") not in active_status_ids:
            row.errors.append(
                f"Supervisor '{ref}' (in this file) is not in an active status."
            )
            continue
        # Deferred: linked during commit once the row is inserted. Mark the
        # data so commit knows to resolve by employee_number.
        row.data["supervisor_id"] = None
        row.data["_supervisor_ref"] = ref
        if FIELD_LABELS["supervisor_id"] not in row.changes:
            row.changes.append(FIELD_LABELS["supervisor_id"])


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


@dataclass
class AppliedRow:
    action: str  # "created" | "updated"
    employee_id: int
    employee_number: str
    label: str


@dataclass
class CommitResult:
    created: list[AppliedRow] = field(default_factory=list)
    updated: list[AppliedRow] = field(default_factory=list)
    skipped: int = 0  # rows with errors that were not applied


def _model_data(data: dict[str, object]) -> dict[str, object]:
    """Drop private helper keys (e.g. ``_supervisor_ref``) before setattr."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


def commit_preview(db: Session, preview: ImportPreview) -> CommitResult:
    """Persist the committable rows of a preview in a single transaction.

    Error rows are skipped (the caller has already shown them to the user).
    Supervisors that pointed at another new row in the same file are linked in
    a second pass, once every row has a database id.
    """
    result = CommitResult(skipped=preview.error_count)

    # Pass 1: insert / update everything except deferred in-file supervisors.
    # number -> Employee, so pass 2 can resolve forward references.
    by_number: dict[str, Employee] = {}
    deferred: list[tuple[Employee, str]] = []

    for row in preview.committable:
        payload = _model_data(row.data)
        if row.existing_id is not None:
            employee = db.get(Employee, row.existing_id)
            if employee is None:  # vanished between preview and commit
                continue
            for f_name, value in payload.items():
                setattr(employee, f_name, value)
            result.updated.append(
                AppliedRow("updated", employee.id, employee.employee_number, row.display_name)
            )
        else:
            employee = Employee(**payload)
            db.add(employee)
            result.created.append(
                AppliedRow("created", 0, row.employee_number, row.display_name)
            )
        by_number[row.employee_number.lower()] = employee

        ref = row.data.get("_supervisor_ref")
        if isinstance(ref, str):
            deferred.append((employee, ref))

    db.flush()  # assign ids to the newly-created rows

    # Pass 2: link deferred (in-file) supervisors now that ids exist.
    for employee, ref in deferred:
        supervisor = by_number.get(ref.lower())
        if supervisor is not None:
            employee.supervisor_id = supervisor.id

    db.commit()

    # Backfill the ids we didn't have when we recorded created rows.
    for applied in result.created:
        emp = by_number.get(applied.employee_number.lower())
        if emp is not None:
            applied.employee_id = emp.id

    return result
