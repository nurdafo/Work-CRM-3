"""Copy the existing local CRM records into an empty migrated PostgreSQL database.

Run from the api directory with DATABASE_URL set to the cloud PostgreSQL URL.
The migration is intentionally one-shot and refuses a non-empty target.
"""

from pathlib import Path

from sqlalchemy import create_engine, func, select

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401 - register all tables


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must point to the cloud PostgreSQL database")

    source_path = Path(__file__).resolve().parents[1] / "crm.db"
    if not source_path.is_file():
        raise RuntimeError("Local api/crm.db was not found")

    source = create_engine(f"sqlite:///{source_path.as_posix()}")
    target = create_engine(settings.database_url, pool_pre_ping=True)
    totals: dict[str, int] = {}
    with source.connect() as source_db, target.begin() as target_db:
        for table in Base.metadata.sorted_tables:
            target_count = target_db.scalar(select(func.count()).select_from(table))
            if target_count:
                raise RuntimeError(f"Cloud table {table.name} is not empty; no records were copied")

        for table in Base.metadata.sorted_tables:
            records = [dict(row._mapping) for row in source_db.execute(select(table))]
            if records:
                target_db.execute(table.insert(), records)
            totals[table.name] = len(records)

    print("Migration complete. Records per table:")
    for table_name, count in totals.items():
        print(f"  {table_name}: {count}")


if __name__ == "__main__":
    main()
