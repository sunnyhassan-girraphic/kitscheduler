"""
Backs up the database, then applies migrations - with clear pass/fail
messages at every step so nothing happens silently.

Run this by double-clicking safe_migrate.bat (which calls this script using
your venv's Python). Don't run this file directly by double-clicking it -
Windows will try to open it with the wrong program.
"""
import os
import re
import shutil
import subprocess
import sys
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"


def fail(message):
    print()
    print("=" * 60)
    print("STOPPED - nothing was changed.")
    print(message)
    print("=" * 60)
    input("\nPress Enter to close this window...")
    sys.exit(1)


def find_pg_dump():
    # 1. Is it already on PATH?
    found = shutil.which("pg_dump")
    if found:
        return found

    # 2. Check the usual Windows install locations, newest version first
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pg_root = program_files / "PostgreSQL"
    if pg_root.exists():
        versions = sorted(
            [p for p in pg_root.iterdir() if p.is_dir()],
            key=lambda p: p.name, reverse=True,
        )
        for version_dir in versions:
            candidate = version_dir / "bin" / "pg_dump.exe"
            if candidate.exists():
                return str(candidate)

    return None


def parse_database_url():
    if not ENV_FILE.exists():
        fail(f"Couldn't find a .env file at:\n  {ENV_FILE}\n\n"
             "This script needs to run from inside your kitscheduler folder, "
             "next to manage.py and .env.")

    text = ENV_FILE.read_text()
    match = re.search(r"^DATABASE_URL\s*=\s*(.+)$", text, re.MULTILINE)
    if not match:
        fail("Couldn't find DATABASE_URL in your .env file.\n"
             "Are you running this against a Postgres setup, or still using sqlite?")

    url = match.group(1).strip()
    # postgres://user:password@host:port/dbname
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]*)@([^:/]+):?(\d*)/(.+)", url)
    if not m:
        fail(f"Couldn't understand this DATABASE_URL:\n  {url}\n\n"
             "Expected something like postgres://user:password@localhost:5432/dbname")

    user, password, host, port, dbname = m.groups()
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port or "5432",
        "dbname": dbname,
    }


def main():
    print("=" * 60)
    print("Step 1 of 3: finding your database settings")
    print("=" * 60)
    db = parse_database_url()
    print(f"  Database: {db['dbname']}")
    print(f"  User:     {db['user']}")
    print(f"  Host:     {db['host']}:{db['port']}")

    pg_dump_path = find_pg_dump()
    if not pg_dump_path:
        fail("Couldn't find pg_dump.exe anywhere on this machine.\n"
             "Is PostgreSQL actually installed here? If it's installed somewhere "
             "unusual, tell Claude the folder and it'll fix this script.")
    print(f"  pg_dump:  {pg_dump_path}")

    print()
    print("=" * 60)
    print("Step 2 of 3: backing up your database")
    print("=" * 60)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = PROJECT_ROOT / f"backup_{timestamp}.dump"

    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]

    result = subprocess.run(
        [
            pg_dump_path,
            "-U", db["user"],
            "-h", db["host"],
            "-p", db["port"],
            "-d", db["dbname"],
            "-F", "c",
            "-f", str(backup_file),
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        fail("The backup failed - see the error below. Nothing has been touched.\n\n"
             f"{result.stderr}")

    if not backup_file.exists() or backup_file.stat().st_size == 0:
        fail("pg_dump said it succeeded, but no backup file appeared. "
             "Don't proceed - tell Claude what you saw.")

    size_kb = backup_file.stat().st_size / 1024
    print(f"  Backup saved: {backup_file.name} ({size_kb:.0f} KB)")

    print()
    print("=" * 60)
    print("Step 3 of 3: applying the update")
    print("=" * 60)
    print("Here's exactly what's about to change:\n")

    plan = subprocess.run(
        [sys.executable, "manage.py", "migrate", "--plan"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    print(plan.stdout)
    if plan.returncode != 0:
        fail(f"Couldn't even preview the migration plan:\n\n{plan.stderr}")

    answer = input("Type YES to apply this, or anything else to stop: ").strip()
    if answer != "YES":
        print(f"\nStopped - nothing was changed. Your backup is still at:\n  {backup_file}")
        input("\nPress Enter to close this window...")
        return

    migrate = subprocess.run(
        [sys.executable, "manage.py", "migrate"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    print(migrate.stdout)
    if migrate.returncode != 0:
        print(migrate.stderr)
        fail(f"The update failed partway through - see above.\n"
             f"Your backup from before any of this started is safe at:\n  {backup_file}\n\n"
             f"Tell Claude exactly what's printed above and it'll help you recover.")

    print()
    print("=" * 60)
    print("Done. Everything applied successfully.")
    print(f"Backup kept at: {backup_file}")
    print("=" * 60)
    input("\nPress Enter to close this window...")


if __name__ == "__main__":
    main()
