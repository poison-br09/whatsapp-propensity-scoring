import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / 'supabase' / 'migrations'

_CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS _schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def run_migrations(database_url: str) -> None:
    import psycopg2

    migration_files = sorted(MIGRATIONS_DIR.glob('*.sql'))
    if not migration_files:
        logger.info('No migration files found in %s', MIGRATIONS_DIR)
        return

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TRACKING_TABLE)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute('SELECT filename FROM _schema_migrations')
            applied = {row[0] for row in cur.fetchall()}

        pending = [f for f in migration_files if f.name not in applied]
        if not pending:
            logger.info('All %s migrations already applied', len(migration_files))
            return

        logger.info('%s pending migration(s) to apply', len(pending))
        for migration_file in pending:
            logger.info('Applying migration %s', migration_file.name)
            sql = migration_file.read_text(encoding='utf-8')
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    'INSERT INTO _schema_migrations (filename) VALUES (%s)',
                    (migration_file.name,),
                )
            conn.commit()
            logger.info('Applied migration %s', migration_file.name)

    except Exception:
        conn.rollback()
        logger.exception('Migration failed — rolled back')
        raise
    finally:
        conn.close()
