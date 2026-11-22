import argparse
import json
from pathlib import Path
from .core import load_project, export_batch, save_project


def main():
    parser = argparse.ArgumentParser(description="Pixel Studio — PNG 묶음 변환")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--export", dest="output", type=Path, help="GUI 없이 일괄 PNG 출력")
    parser.add_argument("--ids", nargs="+", help="선택한 에셋 ID만 출력")
    parser.add_argument("--force", action="store_true", help="변경되지 않은 결과도 재출력")
    args = parser.parse_args()
    if args.output:
        if not args.project:
            parser.error("--export에는 --project가 필요합니다.")
        project = load_project(args.project)
        result = export_batch(project, args.output, args.ids, incremental=not args.force)
        save_project(args.project, project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["failed"] else 0
    from .app import run
    return run(args.project)


if __name__ == "__main__":
    raise SystemExit(main())
