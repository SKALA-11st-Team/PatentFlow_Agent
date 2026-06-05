"""Run the patent valuation workflow for a batch of patents concurrently.

Each patent runs as an isolated `app.main` subprocess (same as the sequential
batch), but several run at once, bounded by --concurrency. Defaults reproduce a
previous batch's patent set via --from-batch.

Examples:
    # Same 20 patents as a previous batch, 5 at a time
    venv/bin/python scripts/run_batch_async.py --from-batch kr20_20260507_144329 --concurrency 5

    # Explicit list
    venv/bin/python scripts/run_batch_async.py --patents P201510001-KR0,P202405001-KR0 --concurrency 3
"""
import argparse
import asyncio
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run valuation workflow for a batch of patents concurrently.")
    parser.add_argument("--from-batch", default=None, help="Reuse management numbers from artifacts/batches/<name>.")
    parser.add_argument("--patents", default=None, help="Comma-separated management numbers.")
    parser.add_argument("--patents-file", default=None, help="File with one management number per line.")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--batch-name", default=None)
    return parser.parse_args()


def management_numbers_from_batch(batch_name: str) -> list[str]:
    val_dir = Path("artifacts/batches") / batch_name / "valuation_reports"
    numbers: list[str] = []
    seen: set[str] = set()
    for path in sorted(val_dir.glob("*_final_report.md")):
        match = re.match(r"\d+_(P\d+-[A-Za-z0-9]+)_", path.name)
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            numbers.append(match.group(1))
    return numbers


def resolve_management_numbers(args: argparse.Namespace) -> list[str]:
    if args.patents:
        return [item.strip() for item in args.patents.split(",") if item.strip()]
    if args.patents_file:
        return [line.strip() for line in Path(args.patents_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.from_batch:
        return management_numbers_from_batch(args.from_batch)
    raise SystemExit("Provide one of --patents, --patents-file, or --from-batch.")


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


async def run_one(
    management_number: str,
    *,
    index: int,
    total: int,
    semaphore: asyncio.Semaphore,
    timeout: int,
    summary_dir: Path,
    valuation_dir: Path,
    log_dir: Path,
) -> dict[str, object]:
    async with semaphore:
        before = {
            path.name
            for path in Path("artifacts/runs").glob(f"*{management_number}")
            if path.is_dir()
        }
        print(f"[start {index}/{total}] {management_number}", flush=True)
        proc = await asyncio.create_subprocess_exec(
            "venv/bin/python",
            "-m",
            "app.main",
            management_number,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout, stderr = await proc.communicate()
        returncode = proc.returncode

        log_path = log_dir / f"{index:02d}_{management_number}.log"
        log_text = (stdout or b"").decode(errors="replace") + (stderr or b"").decode(errors="replace")
        if timed_out:
            log_text += "\n[run_batch_async] TIMEOUT"
        log_path.write_text(log_text, encoding="utf-8")

        run_dir = newest_run_dir(management_number, before)
        summary = first_match(run_dir / "summary" if run_dir else None, "*_summary.md")
        final = first_match(run_dir / "final" if run_dir else None, "*_final_report.md")

        if not timed_out and returncode == 0 and summary and final:
            summary_copy = summary_dir / f"{index:02d}_{management_number}_{summary.name}"
            final_copy = valuation_dir / f"{index:02d}_{management_number}_{final.name}"
            shutil.copy2(summary, summary_copy)
            shutil.copy2(final, final_copy)
            print(f"[done  {index}/{total}] completed {management_number}", flush=True)
            return {
                "ok": True,
                "management_number": management_number,
                "run_dir": str(run_dir),
                "summary_report": str(summary_copy),
                "valuation_report": str(final_copy),
            }
        print(f"[done  {index}/{total}] FAILED {management_number} (rc={returncode}, timeout={timed_out})", flush=True)
        return {
            "ok": False,
            "management_number": management_number,
            "returncode": returncode,
            "timed_out": timed_out,
            "run_dir": str(run_dir) if run_dir else None,
            "log": str(log_path),
        }


async def main_async() -> int:
    args = parse_args()
    management_numbers = resolve_management_numbers(args)
    if not management_numbers:
        raise SystemExit("No management numbers resolved.")

    batch_name = args.batch_name or f"async_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    batch_root = Path("artifacts/batches") / batch_name
    summary_dir = batch_root / "summary_reports"
    valuation_dir = batch_root / "valuation_reports"
    log_dir = batch_root / "logs"
    for directory in (summary_dir, valuation_dir, log_dir):
        directory.mkdir(parents=True, exist_ok=True)

    total = len(management_numbers)
    print(f"BATCH_ROOT={batch_root}", flush=True)
    print(f"PATENTS={total} CONCURRENCY={args.concurrency}", flush=True)

    semaphore = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *[
            run_one(
                management_number,
                index=index,
                total=total,
                semaphore=semaphore,
                timeout=args.timeout,
                summary_dir=summary_dir,
                valuation_dir=valuation_dir,
                log_dir=log_dir,
            )
            for index, management_number in enumerate(management_numbers, 1)
        ]
    )

    completed = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]

    manifest = {
        "batch_root": str(batch_root),
        "concurrency": args.concurrency,
        "target_count": total,
        "completed_count": len(completed),
        "failed_count": len(failed),
        "completed": completed,
        "failed": failed,
    }
    (batch_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (batch_root / "manifest.txt").write_text(
        "\n".join(
            [
                f"batch_root={batch_root}",
                f"concurrency={args.concurrency}",
                f"target_count={total}",
                f"completed={len(completed)}",
                f"failed={len(failed)}",
                "",
                "completed_management_numbers:",
                *[f"- {item['management_number']}" for item in completed],
                "",
                "failures:",
                *[
                    f"- {item['management_number']}: returncode={item['returncode']}, "
                    f"timed_out={item['timed_out']}, log={item['log']}"
                    for item in failed
                ],
            ]
        ),
        encoding="utf-8",
    )

    print(f"BATCH_ROOT={batch_root}", flush=True)
    print(f"COMPLETED={len(completed)}", flush=True)
    print(f"FAILED={len(failed)}", flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
