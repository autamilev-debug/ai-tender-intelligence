"""Explicit migration CLI. No automatic schema creation on app startup."""
from ai_tender_intelligence.assets import root as asset_root

import argparse
from pathlib import Path
from alembic import command
from alembic.config import Config
from .database import Database, DatabaseSettings


def migration_config():
    root = asset_root()
    return Config(str(root / 'alembic.ini'))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit local PostgreSQL migrations; no provider calls.')
    parser.add_argument('action', choices=['upgrade','check','current'])
    args = parser.parse_args(argv)
    db = None
    try:
        db = Database(DatabaseSettings.from_environment())
        with db.engine.begin() as connection:
            config = migration_config()
            config.attributes['connection'] = connection
            if args.action == 'upgrade':
                command.upgrade(config,'head')
            elif args.action == 'check':
                command.check(config)
            else:
                # Deliberately avoid printing a connection URL/config object.
                from alembic.runtime.migration import MigrationContext
                print('Database revision: ' + (MigrationContext.configure(connection).get_current_revision() or 'uninitialized'))
        print('Local database migration operation completed.')
        return 0
    except Exception:
        print('Local database migration operation failed. Check configuration, availability and schema; credentials and SQL are not displayed.')
        return 2
    finally:
        if db:
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())
