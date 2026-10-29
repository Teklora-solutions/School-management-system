"""
make_student_import_xlsx.py
---------------------------
Builds an .xlsx for Admin Portal → Students → Bulk Import
(``/students/import/``).

The column list is imported from ``apps.students.services.BULK_TEMPLATE_HEADERS``
so this generator can never drift from what the importer actually accepts.

    cd backend
    ../.venv/Scripts/python.exe ../scripts/make_student_import_xlsx.py --count 100 \
        --out ../samples/students_import_100.xlsx

    # same, plus rows that are deliberately invalid, to exercise the error report
    ../.venv/Scripts/python.exe ../scripts/make_student_import_xlsx.py --count 90 \
        --with-errors --out ../samples/students_import_with_errors.xlsx

Note: the **class** is not a column — it is chosen on the upload form
("target class").  The ``stream`` column must match a stream that exists on that
class, so it is left blank unless you pass ``--stream``.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django

FEMALE_FIRST = [
    "Ama", "Abena", "Adwoa", "Akosua", "Esi", "Efua", "Naa", "Araba", "Yaa", "Akua",
    "Chiamaka", "Amara", "Zainab", "Halima", "Blessing", "Precious", "Ansiela",
]
MALE_FIRST = [
    "Kwame", "Kofi", "Kojo", "Yaw", "Kwabena", "Fiifi", "Ebo", "Cobbold", "Nii",
    "Chidi", "Ifeanyi", "Musa", "Ibrahim", "Emeka", "Tobi", "Selorm", "Kwaku",
]
SURNAMES = [
    "Mensah", "Owusu", "Asante", "Boateng", "Adjei", "Appiah", "Darko", "Frimpong",
    "Amoah", "Bediako", "Osei", "Prempeh", "Ansah", "Baffour", "Arthur", "Nkrumah",
    "Aidoo", "Tetteh", "Quaye", "Addo",
]
GUARDIAN_FIRST = ["Daniel", "Grace", "Samuel", "Comfort", "Emmanuel", "Ruth", "Peter", "Mary"]
RELATIONSHIPS = ["father", "mother", "guardian", "other"]


def _row(
    *,
    index: int,
    prefix: str,
    domain: str,
    stream: str,
    admission_number: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    gender: str | None = None,
    date_of_birth: str | None = None,
    row_stream: str | None = None,
) -> dict[str, str]:
    adm = admission_number if admission_number is not None else f"{prefix}-{index:03d}"
    female = index % 2 == 0
    if first_name is None:
        first_name = (FEMALE_FIRST if female else MALE_FIRST)[index % 17]
    if last_name is None:
        last_name = SURNAMES[index % 20]
    if email is None:
        email = f"{adm.lower()}@{domain}"
    if gender is None:
        gender = "O" if index % 25 == 0 else ("F" if female else "M")
    if date_of_birth is None:
        date_of_birth = f"{2010 + index % 6:04d}-{1 + index % 12:02d}-{1 + index % 28:02d}"
    gfirst = GUARDIAN_FIRST[index % 8]
    return {
        "admission_number": adm,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "gender": gender,
        "date_of_birth": date_of_birth,
        "stream": row_stream if row_stream is not None else stream,
        "guardian_first_name": gfirst,
        "guardian_last_name": last_name,
        "guardian_email": f"{gfirst.lower()}.{adm.lower()}@{domain}",
        "guardian_phone": f"0244{index:06d}",
        "guardian_relationship": RELATIONSHIPS[index % 4],
    }


def _error_rows(first_adm: str, args) -> list[dict[str, str]]:
    """Rows that trip each documented validation rule, for testing the error report."""
    blank_first = _row(index=901, prefix=args.prefix, domain=args.domain, stream=args.stream)
    blank_first["first_name"] = ""
    duplicate_adm = _row(index=902, prefix=args.prefix, domain=args.domain, stream=args.stream)
    duplicate_adm["admission_number"] = first_adm
    duplicate_email = _row(index=903, prefix=args.prefix, domain=args.domain, stream=args.stream)
    duplicate_email["email"] = _row(
        index=1, prefix=args.prefix, domain=args.domain, stream=args.stream
    )["email"]
    taken_email = _row(index=904, prefix=args.prefix, domain=args.domain, stream=args.stream)
    taken_email["email"] = args.taken_email
    unknown_stream = _row(index=905, prefix=args.prefix, domain=args.domain, stream=args.stream)
    unknown_stream["stream"] = "Purple"
    bad_gender = _row(index=906, prefix=args.prefix, domain=args.domain, stream=args.stream)
    bad_gender["gender"] = "X"
    bad_dob = _row(index=907, prefix=args.prefix, domain=args.domain, stream=args.stream)
    bad_dob["date_of_birth"] = "12-05-2014"
    existing = _row(
        index=908, prefix=args.prefix, domain=args.domain, stream=args.stream,
        admission_number=args.existing_adm,
    )
    return [
        blank_first,
        duplicate_adm,
        duplicate_email,
        taken_email,
        unknown_stream,
        bad_gender,
        bad_dob,
        existing,
    ]


def build(headers: list[str], args) -> tuple[list[dict[str, str]], int]:
    rows = [
        _row(index=i, prefix=args.prefix, domain=args.domain, stream=args.stream)
        for i in range(1, args.count + 1)
    ]
    clean = len(rows)
    if args.with_errors:
        rows += _error_rows(rows[0]["admission_number"], args)
    return [{h: r.get(h, "") for h in headers} for r in rows], clean


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100, help="valid student rows (default 100)")
    parser.add_argument(
        "--out",
        default="../samples/students_import_100.xlsx",
        help="path for the generated .xlsx",
    )
    parser.add_argument("--prefix", default="ADM-IMP", help="admission number prefix")
    parser.add_argument("--domain", default="import.school", help="email domain")
    parser.add_argument(
        "--stream", default="", help="value for the stream column (must exist on the target class)"
    )
    parser.add_argument(
        "--with-errors",
        action="store_true",
        help="append deliberately invalid rows + one already-existing admission number",
    )
    parser.add_argument("--taken-email", default="admin@school.com", help="email already on a user")
    parser.add_argument("--existing-adm", default="74", help="admission number already in the DB")
    args = parser.parse_args()

    django.setup()
    from apps.students.services import BULK_TEMPLATE_HEADERS
    from openpyxl import Workbook

    rows, clean = build(BULK_TEMPLATE_HEADERS, args)

    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    ws.append(BULK_TEMPLATE_HEADERS)
    for row in rows:
        ws.append([row[h] for h in BULK_TEMPLATE_HEADERS])

    out = pathlib.Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)

    print(f"wrote {out}")
    print(f"  columns: {', '.join(BULK_TEMPLATE_HEADERS)}")
    print(f"  data rows: {len(rows)}  (valid: {clean}, invalid/skip: {len(rows) - clean})")
    print(f"  admission numbers: {rows[0]['admission_number']} … "
          f"{rows[min(clean, len(rows)) - 1]['admission_number']}")
    print(f"  stream column: {args.stream or '(blank — no stream needed on the class)'}")
    print("  upload at /students/import/ and pick a target class; the class is not a column.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
