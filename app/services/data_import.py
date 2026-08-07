"""Data Import wizard engine.

Turns an arbitrary customer HR extract (.xlsx/.csv) into employees, driven by an
admin-defined column mapping instead of a fixed CSV shape. The heavy lifting of
row validation/classification and commit is reused from
:mod:`app.services.employee_import`; this module adds:

* **File parsing** — read .xlsx (openpyxl) or .csv into ``records`` (a list of
  ``{header: value}`` dicts) plus the ordered ``source_columns``.
* **Mapping** — map each source column to a standard field, a custom field, the
  "split Last, First" transform, or ignore. Suggested automatically, editable in
  the UI, and saveable as a reusable :class:`~app.models.import_batch.ImportProfile`.
* **Value mapping** — translate source codes to system values
  (``USA -> United States``, ``A -> Active``).
* **Auto-create lookups** — departments / job titles / locations / states that
  the file needs but the system doesn't have yet are created at commit.
* **Custom fields** — columns mapped to custom attributes create their
  :class:`~app.models.custom_field.CustomFieldDefinition` on commit and populate
  the employee's ``custom_fields`` bag.

The wizard never writes anything until the commit step: preview runs in a
"missing lookups are OK" mode so the operator sees real row errors without the
not-yet-created lookups counting against them.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Country,
    Department,
    EmploymentStatus,
    JobTitle,
    Location,
    StateProvince,
)
from app.services import employee_import
from app.services.custom_fields import (
    coerce_value,
    definitions_by_key,
    ensure_definition,
    slugify_key,
)
from app.services.employee_import import COLUMNS

# ---------------------------------------------------------------------------
# Target vocabulary
# ---------------------------------------------------------------------------

# Special (non-column) targets.
TARGET_IGNORE = "__ignore__"
TARGET_SPLIT_NAME = "__split_name__"
CUSTOM_PREFIX = "custom:"

# Standard fields the wizard can map onto, with a human label and a group for the
# dropdown. Order here is the order shown in the UI.
STANDARD_TARGETS: list[tuple[str, str]] = [
    ("employee_number", "Employee number"),
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("middle_name", "Middle name"),
    ("department", "Department"),
    ("job_title", "Job title"),
    ("location", "Location"),
    ("address_line_1", "Address line 1"),
    ("address_line_2", "Address line 2"),
    ("city", "City"),
    ("state_province", "State / Province"),
    ("postal_code", "Postal code"),
    ("country", "Country"),
    ("employment_status", "Employment status"),
    ("supervisor_employee_number", "Supervisor (emp #)"),
    ("hire_date", "Hire date"),
    ("termination_date", "Termination date"),
    ("date_of_birth", "Date of birth"),
    ("ssn", "SSN"),
    ("home_phone", "Home phone"),
    ("personal_email", "Personal email"),
    ("work_email", "Work email"),
    ("cost_center", "Cost center"),
]
_STANDARD_KEYS = {k for k, _ in STANDARD_TARGETS}

# Reference targets whose source values are resolved by name and can be
# auto-created if missing.
CREATABLE_TARGETS = ("department", "job_title", "location", "state_province")
# Reference targets resolved by name that we do NOT auto-create — they must map
# to a seeded value (via value mapping).
VALUE_MAP_TARGETS = ("country", "employment_status")


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


def parse_upload(
    filename: str, raw: bytes
) -> tuple[list[dict[str, str]], list[str], str | None]:
    """Parse an uploaded .xlsx/.csv into (records, source_columns, error)."""
    lower = (filename or "").lower()
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        return _parse_xlsx(raw)
    return _parse_csv(raw)


def _parse_csv(raw: bytes) -> tuple[list[dict[str, str]], list[str], str | None]:
    text = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return [], [], "Could not read the file — please upload a UTF-8 CSV."
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], [], "The file is empty."
    headers = [h.strip() for h in reader.fieldnames if h and h.strip()]
    records = [
        {k.strip(): (v if v is not None else "") for k, v in row.items() if k}
        for row in reader
    ]
    return records, headers, None


def _parse_xlsx(raw: bytes) -> tuple[list[dict[str, str]], list[str], str | None]:
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - dependency is declared
        return [], [], "Excel support is unavailable on the server."
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - surface any parse failure to the UI
        return [], [], f"Could not read the Excel file: {exc}"
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        return [], [], "The spreadsheet is empty."
    headers = [str(h).strip() for h in header_row if h is not None and str(h).strip()]
    records: list[dict[str, str]] = []
    for values in rows:
        if values is None or all(v is None or str(v).strip() == "" for v in values):
            continue
        record: dict[str, str] = {}
        for idx, header in enumerate(header_row):
            if header is None or str(header).strip() == "":
                continue
            val = values[idx] if idx < len(values) else None
            record[str(header).strip()] = "" if val is None else str(val).strip()
        records.append(record)
    return records, headers, None


# ---------------------------------------------------------------------------
# Auto-mapping suggestions
# ---------------------------------------------------------------------------

# Normalized source-header synonyms -> standard target. Normalized = lowercased,
# non-alphanumerics stripped.
_SYNONYMS: dict[str, str] = {
    "id": "employee_number",
    "employeeid": "employee_number",
    "employeenumber": "employee_number",
    "empid": "employee_number",
    "empno": "employee_number",
    "firstname": "first_name",
    "givenname": "first_name",
    "lastname": "last_name",
    "surname": "last_name",
    "middlename": "middle_name",
    "deptdescr": "department",
    "department": "department",
    "departmentname": "department",
    "jobtitle": "job_title",
    "title": "job_title",
    "position": "job_title",
    "locadescr": "location",
    "locationname": "location",
    "location": "location",
    "locaddress1": "address_line_1",
    "address1": "address_line_1",
    "addressline1": "address_line_1",
    "locaddress2": "address_line_2",
    "address2": "address_line_2",
    "addressline2": "address_line_2",
    "loccity": "city",
    "city": "city",
    "locstate": "state_province",
    "state": "state_province",
    "stateprovince": "state_province",
    "locpostal": "postal_code",
    "postalcode": "postal_code",
    "zip": "postal_code",
    "loccountry": "country",
    "country": "country",
    "paystatus": "employment_status",
    "status": "employment_status",
    "employmentstatus": "employment_status",
    "supvid": "supervisor_employee_number",
    "supervisorid": "supervisor_employee_number",
    "managerid": "supervisor_employee_number",
    "hiredate": "hire_date",
    "startdate": "hire_date",
    "termdate": "termination_date",
    "terminationdate": "termination_date",
    "dob": "date_of_birth",
    "dateofbirth": "date_of_birth",
    "ssn": "ssn",
    "homephone": "home_phone",
    "personalemail": "personal_email",
    "workemail": "work_email",
    "email": "work_email",
    "costcenter": "cost_center",
}

# Headers that name a person's name field to route through the split transform.
_NAME_HEADERS = {"name", "fullname", "employeename"}
# Headers we default to Ignore (redundant / derived).
_IGNORE_HEADERS = {"spvname", "supervisorname", "managername"}


def _norm(header: str) -> str:
    return "".join(ch for ch in header.lower() if ch.isalnum())


def suggest_mapping(source_columns: list[str]) -> dict[str, dict[str, str]]:
    """Propose a target for each source column.

    Standard fields matched by synonym; a name column routed to the split
    transform; obvious derived columns ignored; everything else becomes a custom
    field (target ``custom:<slug>``).
    """
    mapping: dict[str, dict[str, str]] = {}
    used_standard: set[str] = set()
    for col in source_columns:
        norm = _norm(col)
        if norm in _NAME_HEADERS:
            mapping[col] = {"target": TARGET_SPLIT_NAME}
            continue
        if norm in _IGNORE_HEADERS:
            mapping[col] = {"target": TARGET_IGNORE}
            continue
        target = _SYNONYMS.get(norm)
        if target and target not in used_standard:
            mapping[col] = {"target": target}
            used_standard.add(target)
            continue
        # Fall back to a custom field named after the column.
        key = slugify_key(col) or "field"
        mapping[col] = {"target": f"{CUSTOM_PREFIX}{key}", "label": col}
    return mapping


# Common code -> value aliases used to pre-fill value mapping suggestions.
_COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "can": "Canada",
    "ca": "Canada",
    "mex": "Mexico",
    "mx": "Mexico",
    "ind": "India",
    "in": "India",
    "gbr": "United Kingdom",
    "uk": "United Kingdom",
}
_STATUS_ALIASES = {
    "a": "Active",
    "active": "Active",
    "t": "Terminated",
    "term": "Terminated",
    "i": "Not Active",
    "inactive": "Not Active",
}


def suggest_value_map(
    db: Session,
    records: list[dict[str, str]],
    column_map: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Pre-fill value mappings for country/status columns that need translation.

    Only proposes a mapping where the raw source value doesn't already resolve to
    a system value, and a confident alias exists.
    """
    value_map: dict[str, dict[str, str]] = {}
    known_countries = {c.name.lower() for c in db.query(Country)} | {
        c.code.lower() for c in db.query(Country)
    }
    known_statuses = {s.label.lower() for s in db.query(EmploymentStatus)}

    for src, spec in column_map.items():
        target = spec.get("target")
        if target not in ("country", "employment_status"):
            continue
        distinct = {
            (rec.get(src) or "").strip()
            for rec in records
            if (rec.get(src) or "").strip()
        }
        col_map: dict[str, str] = {}
        for val in distinct:
            low = val.lower()
            if target == "country":
                if low in known_countries:
                    continue
                alias = _COUNTRY_ALIASES.get(low)
                if alias:
                    col_map[val] = alias
            else:
                if low in known_statuses:
                    continue
                alias = _STATUS_ALIASES.get(low)
                if alias:
                    col_map[val] = alias
        if col_map:
            value_map[src] = col_map
    return value_map


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


def _apply_value_map(
    value_map: dict[str, dict[str, str]], src: str, raw: str
) -> str:
    col = value_map.get(src)
    if col and raw in col:
        return col[raw]
    return raw


def _split_name(val: str) -> tuple[str, str]:
    """Split "Last, First [Middle]" into (last, first). Falls back gracefully."""
    val = (val or "").strip()
    if "," in val:
        last, _, first = val.partition(",")
        return last.strip(), first.strip()
    # No comma — assume "First Last".
    parts = val.split()
    if len(parts) >= 2:
        return parts[-1], " ".join(parts[:-1])
    return val, ""


def build_rows(
    records: list[dict[str, str]],
    column_map: dict[str, dict[str, str]],
    value_map: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], dict[int, dict[str, str]]]:
    """Project records through the mapping into (standard raw_rows, custom cells).

    ``raw_rows`` are keyed by :data:`employee_import.COLUMNS`; ``custom`` maps a
    1-based line number to ``{custom_key: raw_value}``.
    """
    raw_rows: list[dict[str, str]] = []
    custom: dict[int, dict[str, str]] = {}
    for i, rec in enumerate(records, start=1):
        row = {c: "" for c in COLUMNS}
        custom_cells: dict[str, str] = {}
        for src, spec in column_map.items():
            target = spec.get("target")
            if not target or target == TARGET_IGNORE:
                continue
            val = _apply_value_map(value_map, src, (rec.get(src) or "").strip())
            if target == TARGET_SPLIT_NAME:
                last, first = _split_name(val)
                if last:
                    row["last_name"] = last
                if first:
                    row["first_name"] = first
            elif target.startswith(CUSTOM_PREFIX):
                key = target[len(CUSTOM_PREFIX):]
                if key:
                    custom_cells[key] = val
            elif target in COLUMNS:
                row[target] = val
        raw_rows.append(row)
        if custom_cells:
            custom[i] = custom_cells
    return raw_rows, custom


def _coerce_custom(
    db: Session, custom_raw: dict[int, dict[str, str]]
) -> dict[int, dict[str, object]]:
    """Coerce raw custom cells against existing definitions (text if undefined)."""
    defs = definitions_by_key(db, active_only=False)
    out: dict[int, dict[str, object]] = {}
    for idx, cells in custom_raw.items():
        vals: dict[str, object] = {}
        for key, raw in cells.items():
            definition = defs.get(key)
            data_type = definition.data_type if definition else "text"
            try:
                cv = coerce_value(data_type, raw)
            except Exception:  # noqa: BLE001 - bad cell shouldn't kill the row
                cv = None
            if cv is not None:
                vals[key] = cv
        if vals:
            out[idx] = vals
    return out


# ---------------------------------------------------------------------------
# Plan analysis (Step 3 — resolve values & lookups)
# ---------------------------------------------------------------------------


@dataclass
class ValueMapGroup:
    source_column: str
    target_label: str
    entries: list[dict[str, object]]  # {value, mapped, resolved}


@dataclass
class LookupGroup:
    kind: str  # "department" | "job_title" | "location" | "state_province"
    label: str
    samples: list[str]
    count: int


@dataclass
class CustomFieldPlan:
    key: str
    label: str
    data_type: str
    exists: bool


@dataclass
class ImportPlan:
    value_groups: list[ValueMapGroup] = field(default_factory=list)
    new_lookups: list[LookupGroup] = field(default_factory=list)
    custom_fields: list[CustomFieldPlan] = field(default_factory=list)
    unresolved_values: int = 0  # count of value-map entries with no target

    @property
    def new_lookup_total(self) -> int:
        return sum(g.count for g in self.new_lookups)


def analyze(
    db: Session,
    records: list[dict[str, str]],
    column_map: dict[str, dict[str, str]],
    value_map: dict[str, dict[str, str]],
) -> ImportPlan:
    """Compute what the current mapping would create/require. Writes nothing."""
    plan = ImportPlan()

    # --- value mapping groups (country, employment_status) ---
    known = {
        "country": {c.name.lower() for c in db.query(Country)}
        | {c.code.lower() for c in db.query(Country)},
        "employment_status": {
            s.label.lower() for s in db.query(EmploymentStatus)
        },
    }
    target_labels = dict(STANDARD_TARGETS)
    for src, spec in column_map.items():
        target = spec.get("target")
        if target not in VALUE_MAP_TARGETS:
            continue
        distinct = sorted(
            {
                (rec.get(src) or "").strip()
                for rec in records
                if (rec.get(src) or "").strip()
            }
        )
        entries: list[dict[str, object]] = []
        for val in distinct:
            mapped = _apply_value_map(value_map, src, val)
            resolved = mapped.lower() in known[target]
            entries.append({"value": val, "mapped": mapped, "resolved": resolved})
            if not resolved:
                plan.unresolved_values += 1
        if entries:
            plan.value_groups.append(
                ValueMapGroup(
                    source_column=src,
                    target_label=target_labels.get(target, target),
                    entries=entries,
                )
            )

    # --- new lookups (departments/job titles/locations/states) ---
    raw_rows, _ = build_rows(records, column_map, value_map)
    plan.new_lookups = _missing_lookups(db, raw_rows)

    # --- custom fields ---
    existing_keys = set(definitions_by_key(db, active_only=False).keys())
    seen: set[str] = set()
    for spec in column_map.values():
        target = spec.get("target", "")
        if not target.startswith(CUSTOM_PREFIX):
            continue
        key = target[len(CUSTOM_PREFIX):]
        if not key or key in seen:
            continue
        seen.add(key)
        plan.custom_fields.append(
            CustomFieldPlan(
                key=key,
                label=spec.get("label") or key.replace("_", " ").title(),
                data_type=spec.get("data_type", "text"),
                exists=key in existing_keys,
            )
        )
    return plan


def _missing_lookups(
    db: Session, raw_rows: list[dict[str, str]]
) -> list[LookupGroup]:
    """Distinct department/job-title/location/state values not yet in the DB."""
    existing_depts = {d.name.lower() for d in db.query(Department)}
    existing_locs = {loc.name.lower() for loc in db.query(Location)}
    existing_titles = {(t.name.lower()) for t in db.query(JobTitle)}
    existing_states: set[str] = set()
    for s in db.query(StateProvince):
        existing_states.add(s.name.lower())
        if s.code:
            existing_states.add(s.code.lower())
            existing_states.add(s.code.rsplit("-", 1)[-1].lower())

    depts: set[str] = set()
    titles: set[str] = set()
    locs: set[str] = set()
    states: set[str] = set()
    for row in raw_rows:
        d = row.get("department", "").strip()
        if d and d.lower() not in existing_depts:
            depts.add(d)
        t = row.get("job_title", "").strip()
        if t and t.lower() not in existing_titles:
            titles.add(t)
        loc = row.get("location", "").strip()
        if loc and loc.lower() not in existing_locs:
            locs.add(loc)
        st = row.get("state_province", "").strip()
        if st and st.lower() not in existing_states:
            states.add(st)

    groups: list[LookupGroup] = []
    for kind, label, values in (
        ("department", "Departments", depts),
        ("job_title", "Job titles", titles),
        ("location", "Locations", locs),
        ("state_province", "States / Provinces", states),
    ):
        if values:
            ordered = sorted(values)
            groups.append(
                LookupGroup(
                    kind=kind,
                    label=label,
                    samples=ordered[:5],
                    count=len(ordered),
                )
            )
    return groups


# ---------------------------------------------------------------------------
# Preview + commit
# ---------------------------------------------------------------------------


def preview(
    db: Session,
    records: list[dict[str, str]],
    column_map: dict[str, dict[str, str]],
    value_map: dict[str, dict[str, str]],
    options: dict[str, object],
) -> employee_import.ImportPreview:
    """Dry-run classification of the mapped rows. Writes nothing."""
    auto_create = bool(options.get("auto_create_lookups", True))
    raw_rows, custom_raw = build_rows(records, column_map, value_map)
    custom_by_index = _coerce_custom(db, custom_raw)
    return employee_import.classify_records(
        db,
        raw_rows,
        missing_ok=auto_create,
        custom_by_index=custom_by_index,
    )


def commit(
    db: Session,
    records: list[dict[str, str]],
    column_map: dict[str, dict[str, str]],
    value_map: dict[str, dict[str, str]],
    options: dict[str, object],
) -> tuple[employee_import.CommitResult, dict[str, int]]:
    """Create custom fields + missing lookups, then upsert employees.

    Returns the employee commit result plus a count of created reference data.
    """
    auto_create = bool(options.get("auto_create_lookups", True))

    # 1. Ensure custom field definitions exist for every custom-mapped column.
    created_custom = 0
    existing_keys = set(definitions_by_key(db, active_only=False).keys())
    for spec in column_map.values():
        target = spec.get("target", "")
        if not target.startswith(CUSTOM_PREFIX):
            continue
        key = target[len(CUSTOM_PREFIX):]
        if not key:
            continue
        was_new = key not in existing_keys
        ensure_definition(
            db,
            key=key,
            label=spec.get("label"),
            data_type=spec.get("data_type", "text"),
        )
        if was_new:
            created_custom += 1
            existing_keys.add(key)

    raw_rows, custom_raw = build_rows(records, column_map, value_map)

    # 2. Create missing lookups (dependency order) when enabled.
    lookup_counts = {"departments": 0, "job_titles": 0, "locations": 0, "states": 0}
    if auto_create:
        lookup_counts = _create_missing_lookups(db, raw_rows)
        db.flush()

    # 3. Classify against the now-complete reference data and commit.
    custom_by_index = _coerce_custom(db, custom_raw)
    prev = employee_import.classify_records(
        db, raw_rows, missing_ok=False, custom_by_index=custom_by_index
    )
    result = employee_import.commit_preview(db, prev)

    summary = dict(lookup_counts)
    summary["custom_fields"] = created_custom
    return result, summary


def _create_missing_lookups(
    db: Session, raw_rows: list[dict[str, str]]
) -> dict[str, int]:
    """Create departments, job titles, locations, and states the rows need.

    Works on the DISTINCT set of required values (a name repeats across many
    rows) so we never insert a duplicate within a single run.
    """
    counts = {"departments": 0, "job_titles": 0, "locations": 0, "states": 0}

    # Distinct required values, preserving a representative original casing.
    dept_names = _distinct(raw_rows, "department")
    loc_names = _distinct(raw_rows, "location")
    # (department, title) and (country_raw, state) pairs.
    title_pairs = _distinct_pairs(raw_rows, "department", "job_title")
    state_pairs = _distinct_pairs(raw_rows, "country", "state_province")

    # Departments first (job titles depend on them).
    existing_depts = {d.name.lower() for d in db.query(Department)}
    for name in dept_names:
        if name.lower() not in existing_depts:
            db.add(Department(name=name, is_active=True))
            existing_depts.add(name.lower())
            counts["departments"] += 1

    # Locations (independent).
    existing_locs = {loc.name.lower() for loc in db.query(Location)}
    for name in loc_names:
        if name.lower() not in existing_locs:
            db.add(Location(name=name, is_active=True))
            existing_locs.add(name.lower())
            counts["locations"] += 1

    db.flush()  # departments now have ids for job-title FKs

    # States (need a resolvable country). Match on name, full code, or code
    # suffix so a bare "AL" reuses a seeded "US-AL" instead of duplicating it.
    for country_raw, st in state_pairs:
        country = _find_country(db, country_raw)
        if country is None:
            continue
        tokens: set[str] = set()
        for s in db.query(StateProvince).filter(
            StateProvince.country_id == country.id
        ):
            tokens.add(s.name.lower())
            if s.code:
                tokens.add(s.code.lower())
                tokens.add(s.code.rsplit("-", 1)[-1].lower())
        if st.lower() not in tokens:
            db.add(
                StateProvince(
                    country_id=country.id,
                    name=st,
                    code=st if len(st) <= 10 else None,
                    is_active=True,
                )
            )
            counts["states"] += 1

    # Job titles (need their department).
    for dept_name, title in title_pairs:
        dept = _find_ci(db, Department, Department.name, dept_name)
        if dept is None:
            continue
        exists = (
            db.query(JobTitle)
            .filter(
                JobTitle.department_id == dept.id,
                func.lower(JobTitle.name) == title.lower(),
            )
            .first()
        )
        if exists is None:
            db.add(JobTitle(department_id=dept.id, name=title, is_active=True))
            counts["job_titles"] += 1
    db.flush()
    return counts


def _distinct(raw_rows: list[dict[str, str]], col: str) -> list[str]:
    """Distinct non-empty values of a column, first-seen casing, order-stable."""
    seen: dict[str, str] = {}
    for row in raw_rows:
        val = (row.get(col) or "").strip()
        if val and val.lower() not in seen:
            seen[val.lower()] = val
    return list(seen.values())


def _distinct_pairs(
    raw_rows: list[dict[str, str]], col_a: str, col_b: str
) -> list[tuple[str, str]]:
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    for row in raw_rows:
        a = (row.get(col_a) or "").strip()
        b = (row.get(col_b) or "").strip()
        if a and b and (a.lower(), b.lower()) not in seen:
            seen[(a.lower(), b.lower())] = (a, b)
    return list(seen.values())


def _find_ci(db: Session, model: type, name_col: object, value: str) -> object | None:
    return db.query(model).filter(func.lower(name_col) == value.lower()).first()


def _find_country(db: Session, raw: str) -> Country | None:
    low = raw.lower()
    return (
        db.query(Country)
        .filter((func.lower(Country.name) == low) | (func.lower(Country.code) == low))
        .first()
    )
