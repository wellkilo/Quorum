"""Database migration and connectivity commands without credential disclosure."""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from quorum.database import DatabaseSettings, create_database_engine

PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parents[1]


def _migration_paths() -> tuple[Path, Path]:
    """Locate Alembic assets in either a source checkout or an installed wheel."""

    candidates = (
        (SOURCE_ROOT / "alembic.ini", SOURCE_ROOT / "migrations"),
        (PACKAGE_ROOT / "alembic.ini", PACKAGE_ROOT / "migrations"),
    )
    for config_path, script_path in candidates:
        if config_path.is_file() and script_path.is_dir():
            return config_path, script_path
    raise RuntimeError("Alembic migration assets are missing from this installation")


def _config() -> Config:
    config_path, script_path = _migration_paths()
    config = Config(config_path)
    config.set_main_option("script_location", str(script_path))
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Quorum business database.")
    parser.add_argument("command", choices=("upgrade", "current", "check"))
    args = parser.parse_args()

    if args.command == "upgrade":
        command.upgrade(_config(), "head")
    elif args.command == "current":
        command.current(_config(), verbose=True)
    else:
        engine = create_database_engine(DatabaseSettings.from_environment())
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
        engine.dispose()
        print("database connection ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
