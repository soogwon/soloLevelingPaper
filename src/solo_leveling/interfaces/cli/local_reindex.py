"""로컬 논문 판본의 기본 색인을 명시적으로 다시 만든다."""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version-id', required=True, help='논문 판본 ID')
    parser.add_argument('--base-embedding-set-id', required=True, help='현재 기본 색인 ID')
    args = parser.parse_args(argv)

    from solo_leveling.infrastructure.database.schema import init_db
    from solo_leveling.infrastructure.storage.vector_store import close_client, get_client
    from solo_leveling.interfaces.mcp.runtime import load_environment, runtime_paths
    from solo_leveling.workers.local_reindex import reindex_local_fragments

    paths = runtime_paths(load_environment())
    if not paths.db_path.is_file() or not paths.chroma_dir.is_dir():
        parser.error('기존 로컬 SQLite와 Chroma 저장소가 필요합니다.')
    db = str(paths.db_path)
    init_db(db)
    client = get_client(str(paths.chroma_dir))
    try:
        new_id = reindex_local_fragments(db, client, args.version_id, args.base_embedding_set_id)
    except ValueError as error:
        parser.exit(1, f'재색인 실패 ({type(error).__name__}). 기존 기본 색인을 확인해 주세요.\n')
    finally:
        close_client(client)
    print(new_id)


if __name__ == '__main__':
    main()
