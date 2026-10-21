"""기존 저장소용 모델 준비 명령을 유지한다."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from solo_leveling.interfaces.cli.prepare_embedding_model import main, prepare, subprocess


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
