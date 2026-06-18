import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# @author 배세은
# @date 2026-05-08
# @relatedFR FR-005, FR-006, FR-008
# @relatedUI UI-005
# @description 무작위 KR 특허 N건에 대해 요약·4축 평가·권고 레포트를 일괄 생성하는 배치 CLI.
# 각 특허를 app.main 서브프로세스로 실행해 산출물을 모은다.


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate random KR patent summary and valuation reports.")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--batch-name", default=None)
    return parser.parse_args()


def load_random_kr_management_numbers() -> list[str]:
    conn = sqlite3.connect("data/patents.sqlite3")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT management_number
        FROM patents
        WHERE country = ?
          AND management_number IS NOT NULL
          AND TRIM(management_number) != ''
        ORDER BY RANDOM()
        """,
        ("KR",),
    ).fetchall()
    conn.close()
    return [row["management_number"] for row in rows]


def newest_run_dir(management_number: str, before: set[str]) -> Path | None:
    candidates = [
        path
        for path in Path("artifacts/runs").glob(f"*{management_number}")
        if path.is_dir() and path.name not in before
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def first_match(directory: Path | None, pattern: str) -> Path | None:
    if not directory:
        return None
    return next(directory.glob(pattern), None)


def main() -> int:
    args = parse_args()
    batch_name = args.batch_name or f"kr20_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    batch_root = Path("artifacts/batches") / batch_name
    summary_dir = batch_root / "summary_reports"
    valuation_dir = batch_root / "valuation_reports"
    log_dir = batch_root / "logs"
    for directory in (summary_dir, valuation_dir, log_dir):
        directory.mkdir(parents=True, exist_ok=True)

    completed: list[dict[str, str]] = []
    failed: list[dict[str, object]] = []
    management_numbers = load_random_kr_management_numbers()

    for attempt, management_number in enumerate(management_numbers, 1):
        if len(completed) >= args.count:
            break

        before = {
            path.name
            for path in Path("artifacts/runs").glob(f"*{management_number}")
            if path.is_dir()
        }
        print(
            f"[{attempt}/{len(management_numbers)} try, {len(completed)}/{args.count} done] "
            f"running {management_number}",
            flush=True,
        )
        proc = subprocess.run(
            [
                "venv/bin/python",
                "-m",
                "app.main",
                management_number,
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )

        log_path = log_dir / f"{attempt:02d}_{management_number}.log"
        log_path.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")

        run_dir = newest_run_dir(management_number, before)
        summary = first_match(run_dir / "summary" if run_dir else None, "*_summary.md")
        final = first_match(run_dir / "final" if run_dir else None, "*_final_report.md")

        if proc.returncode == 0 and summary and final:
            summary_copy = summary_dir / f"{len(completed) + 1:02d}_{management_number}_{summary.name}"
            final_copy = valuation_dir / f"{len(completed) + 1:02d}_{management_number}_{final.name}"
            shutil.copy2(summary, summary_copy)
            shutil.copy2(final, final_copy)
            completed.append(
                {
                    "management_number": management_number,
                    "run_dir": str(run_dir),
                    "summary_report": str(summary_copy),
                    "valuation_report": str(final_copy),
                }
            )
            print(f"[{attempt}/{len(management_numbers)}] completed {management_number}", flush=True)
        else:
            failed.append(
                {
                    "management_number": management_number,
                    "returncode": proc.returncode,
                    "run_dir": str(run_dir) if run_dir else None,
                    "log": str(log_path),
                }
            )
            print(f"[{attempt}/{len(management_numbers)}] failed {management_number}", flush=True)

    manifest = {
        "batch_root": str(batch_root),
        "target_count": args.count,
        "completed_count": len(completed),
        "failed_count": len(failed),
        "completed": completed,
        "failed": failed,
    }
    (batch_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (batch_root / "manifest.txt").write_text(
        "\n".join(
            [
                f"batch_root={batch_root}",
                f"target_count={args.count}",
                f"completed={len(completed)}",
                f"failed={len(failed)}",
                "",
                "completed_management_numbers:",
                *[f"- {item['management_number']}" for item in completed],
                "",
                "failures:",
                *[
                    f"- {item['management_number']}: returncode={item['returncode']}, "
                    f"run_dir={item['run_dir']}, log={item['log']}"
                    for item in failed
                ],
            ]
        ),
        encoding="utf-8",
    )

    print(f"BATCH_ROOT={batch_root}", flush=True)
    print(f"COMPLETED={len(completed)}", flush=True)
    print(f"FAILED={len(failed)}", flush=True)
    return 0 if len(completed) >= args.count else 2


if __name__ == "__main__":
    sys.exit(main())
