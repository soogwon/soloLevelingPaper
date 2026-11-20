"""운영자가 신규 유료 접수만 중지하는 명령."""

import argparse
from getpass import getpass


def pause_intake(conn, expected_generation):
    return conn.execute(
        """UPDATE server_dispatch_controls
           SET intake_enabled=false, generation=generation+1, updated_at=CURRENT_TIMESTAMP
           WHERE control_id='provider_dispatch' AND generation=%s AND intake_enabled
           RETURNING generation""",
        (expected_generation,),
    ).fetchone()


def main():
    import psycopg

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--expected-generation', required=True, type=int)
    args = parser.parse_args()
    dsn = getpass('Operator database URL (hidden): ')
    from psycopg import sql

    try:
        with psycopg.connect(dsn, sslmode='verify-full', connect_timeout=10) as conn:
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(args.schema)))
            changed = pause_intake(conn, args.expected_generation)
        print(f'intake_paused generation={changed[0]}' if changed else 'intake_not_changed')
        return 0 if changed else 1
    except Exception:
        print('intake_pause_failed')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
