"""Explicit additive migrations; never migrate a retained evaluation database."""
from ai_tender_intelligence.assets import root as asset_root
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from ai_tender_intelligence.persistence.database import Database, DatabaseSettings


def migration_config():
    config = Config()
    config.set_main_option('script_location', str(asset_root() / 'discovery_migrations'))
    return config


def upgrade(db):
    with db.engine.begin() as connection:
        current = connection.scalar(text('SELECT current_database()'))
        if current in {'p5_luna_eval', 'p5_luna_v3_batch'}:
            raise ValueError('Retained evaluation database is protected.')
        if connection.scalar(text('SELECT version_num FROM alembic_version')) not in {'0003_prompts', '0004_searches'}:
            raise ValueError('Expected existing profile schema is required.')
        config = migration_config()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')


def main():
    db = None
    try:
        db = Database(DatabaseSettings.from_environment())
        upgrade(db)
        print('Discovery metadata migration completed; profile revision unchanged.')
        return 0
    except Exception:
        print('Discovery migration failed; check explicit local administrative configuration.')
        return 2
    finally:
        if db:
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())
