"""기존 저장소용 진단 명령과 기본 로그 위치를 유지한다."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from solo_leveling.interfaces.cli.mcp_diagnostics import main as packaged_main, preload_embedding_library


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    return packaged_main(['--log-dir', str(ROOT / 'runtime' / 'diagnostics'), *args])


if __name__ == '__main__':
    raise SystemExit(main())
