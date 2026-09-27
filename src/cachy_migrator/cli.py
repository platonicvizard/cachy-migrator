from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import sys
from pathlib import Path

from .core import (
    analyze,
    backup_partition_tables,
    execute_copy_operations,
    filter_safe_destinations,
    human_bytes,
    mounted_destinations,
    mounted_user_sources,
    plan_from_analysis,
    plan_hash,
    run_duplicate_scans,
    save_json,
    snapshot_efi,
    write_rollback_manifest,
)


def setup_logging(log_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stderr)],
    )


def print_analysis(data: dict) -> None:
    print("Detected drives:\n")
    for idx, d in enumerate(data.get("drives", []), 1):
        print(f"{idx}. {d['path']} ({human_bytes(d.get('size'))})")
        print(f"   Model: {d.get('model') or 'unknown'}")
        print(f"   Transport: {d.get('tran') or 'unknown'}")
        print(f"   SMART: {d.get('smart_status')}")
        print(f"   Score: {d.get('score')}/100")
        for reason in d.get("score_reasons", []):
            print(f"     - {reason}")
        for marker in d.get("os_markers", []):
            print(f"     - {marker}")
        print()


def print_plan_summary(plan: dict) -> None:
    target = plan.get("target")
    if target:
        print("Recommended CachyOS target:")
        print(f"  {target['path']} ({human_bytes(target.get('size'))}) score={target.get('score')}/100")
    print(f"Classified user-data items: {len(plan.get('user_data', []))}")
    print(f"Planned copy operations: {len(plan.get('copy_operations', []))}")
    planned_bytes = sum(op.get("bytes", 0) for op in plan.get("copy_operations", []))
    if planned_bytes:
        print(f"Planned transfer size: {human_bytes(planned_bytes)}")
    if plan.get("duplicate_scan_operations"):
        tool = plan["duplicate_scan_operations"][0].get("tool")
        print(f"Duplicate scan: {tool} report planned")
    print(f"Partition-table backups: {len(plan.get('partition_table_backup_operations', []))}")
    print(f"EFI snapshots: {len(plan.get('efi_snapshot_operations', []))}")
    for warning in plan.get("warnings", []):
        print(f"WARNING: {warning}")


def is_root() -> bool:
    if not hasattr(os, "geteuid"):
        return False
    return os.geteuid() == 0


def prompt_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    label = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{label}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def prompt_paths(title: str, discovered: list[str], allow_empty: bool = False) -> list[str]:
    print(title)
    if discovered:
        for idx, path in enumerate(discovered, 1):
            print(f"  {idx}. {path}")
        if prompt_yes_no("Use these paths", default=True):
            return discovered
    elif allow_empty:
        print("  No mounted paths were discovered.")
    else:
        print("  No mounted paths were discovered. Enter at least one path.")

    paths: list[str] = []
    while True:
        value = prompt_text("Enter a path, or leave blank when done")
        if not value:
            if paths or allow_empty:
                return paths
            print("At least one path is required.")
            continue
        paths.append(value)


def _sudo_hint() -> str:
    python = shlex.quote(sys.executable)
    return f"sudo {python} -m cachy_migrator.cli"


def _prepare_workdir(raw_workdir: str) -> Path:
    workdir = Path(raw_workdir).expanduser().resolve()

    # Treat /mnt as an explicit mount namespace for migration media. Refuse to
    # create a workdir there unless some ancestor below / is actually mounted.
    if str(workdir).startswith("/mnt/"):
        probe = workdir
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent

        mounted = probe
        while mounted != mounted.parent and not os.path.ismount(mounted):
            mounted = mounted.parent

        if mounted == Path("/"):
            raise RuntimeError(
                f"{workdir} is under /mnt, but no mounted filesystem backs that path. "
                "Mount the intended backup/data partition first, then rerun. "
                "Refusing to write migration state into the live root filesystem."
            )

    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot create or write work directory {workdir}. "
            f"If it is intentionally mounted and requires root, rerun with: "
            f"{_sudo_hint()} <same arguments>"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Cannot create work directory {workdir}. Verify that the destination filesystem is mounted."
        ) from exc

    if not os.access(workdir, os.W_OK):
        raise RuntimeError(
            f"Work directory {workdir} is not writable. "
            f"Use appropriate permissions or rerun with: {_sudo_hint()} <same arguments>"
        )

    return workdir


def write_plan_files(workdir: Path, analysis: dict, plan: dict) -> str:
    digest = plan_hash(plan)
    save_json(workdir / "analysis.json", analysis)
    save_json(workdir / "migration-plan.json", plan)
    (workdir / "migration-plan.sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def execute_plan(plan: dict, workdir: Path, dry_run: bool = False) -> int:
    destructive = [x for x in plan.get("destructive_operations", []) if x.get("enabled")]
    if destructive:
        logging.error("Execution refused: destructive operations are not supported by this execution path")
        return 4

    operations = plan.get("copy_operations", [])
    try:
        duplicate_results = run_duplicate_scans(plan)
        partition_results = backup_partition_tables(plan)
        efi_results = snapshot_efi(plan, dry_run=dry_run)
        copy_results = execute_copy_operations(plan, dry_run=dry_run)
    except Exception as exc:
        logging.error("Execution failed: %s", exc)
        return 5

    manifest_path = Path(plan.get("rollback_manifest") or workdir / "rollback-manifest.json")
    write_rollback_manifest(manifest_path, plan, duplicate_results, partition_results, efi_results, copy_results)

    print("Plan confirmed and non-destructive execution completed.")
    print(f"Copy operations: {len(operations)}")
    print(f"Rollback manifest: {manifest_path}")
    print("Destructive partition/installer automation remains disabled unless a future gated backend enables it.")
    return 0


def interactive_migration(workdir: Path) -> int:
    print("Cachy Migrator interactive migration")
    print("This workflow analyzes disks, plans migration, backs up metadata, copies user data, verifies checksums, and writes a rollback manifest.")
    print("It does not delete partitions, format disks, or launch the installer.")
    print()

    print("Analyzing attached drives...")
    analysis = analyze()
    print_analysis(analysis)

    discovered_sources = mounted_user_sources(analysis)
    sources = prompt_paths("Source roots to classify and migrate:", discovered_sources)
    discovered_destinations, destination_warnings = filter_safe_destinations(sources, mounted_destinations(analysis))
    for warning in destination_warnings:
        print(f"WARNING: {warning}")
    destinations = prompt_paths("Destination roots for migrated data:", discovered_destinations)

    plan = plan_from_analysis(
        analysis,
        source_roots=sources,
        destination_roots=destinations,
        workdir=workdir,
    )
    digest = write_plan_files(workdir, analysis, plan)

    print()
    print_plan_summary(plan)
    print(f"\nPlan hash: {digest}")
    print(f"Plan written to: {workdir / 'migration-plan.json'}")

    if not plan.get("copy_operations"):
        print("No copy operations were planned. Review the warnings and mounted paths, then run again.")
        return 2

    if not prompt_yes_no("Run a dry-run of the non-destructive migration now", default=True):
        print("Stopping before execution. The plan files were written for review.")
        return 0
    dry_run_result = execute_plan(plan, workdir, dry_run=True)
    if dry_run_result != 0:
        return dry_run_result

    if not prompt_yes_no("Dry-run completed. Run the real copy and checksum verification now", default=False):
        print("Stopping after dry-run. Re-run this command when ready, or use --execute with the saved plan hash.")
        return 0

    confirmation = prompt_text(f"Type the first 12 characters of the plan hash to execute ({digest[:12]})")
    if confirmation != digest[:12]:
        print("Confirmation did not match. Execution cancelled.")
        return 3

    return execute_plan(plan, workdir, dry_run=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safety-first CachyOS migration planner",
        epilog="Run without --analyze, --plan, or --execute to start the interactive one-command migration flow.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--analyze", action="store_true", help="read-only drive analysis")
    mode.add_argument("--plan", action="store_true", help="generate migration plan")
    mode.add_argument("--execute", action="store_true", help="execute approved non-destructive copy operations")
    mode.add_argument("--interactive", action="store_true", help="run the interactive one-command migration flow")
    parser.add_argument("--workdir", default=".", help="directory for analysis, plan, manifest, and logs")
    parser.add_argument("--source", action="append", default=[], help="source root to classify and migrate; repeat for multiple roots")
    parser.add_argument("--destination", action="append", default=[], help="destination root for migrated data; repeat for multiple drives")
    parser.add_argument("--confirm-plan-hash", help="required exact SHA-256 hash for --execute")
    parser.add_argument("--dry-run", action="store_true", help="show rsync work without writing migrated data")
    args = parser.parse_args(argv)

    try:
        workdir = _prepare_workdir(args.workdir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    setup_logging(workdir / "cachy-migrator.log")

    if not is_root():
        logging.warning("Not running as root; some disk metadata may be unavailable")

    if args.interactive or not (args.analyze or args.plan or args.execute):
        return interactive_migration(workdir)

    if args.analyze:
        data = analyze()
        save_json(workdir / "analysis.json", data)
        print_analysis(data)
        return 0

    if args.plan:
        data = analyze()
        plan = plan_from_analysis(
            data,
            source_roots=args.source or None,
            destination_roots=args.destination or None,
            workdir=workdir,
        )
        digest = write_plan_files(workdir, data, plan)
        print_analysis(data)
        print_plan_summary(plan)
        print(f"\nPlan hash: {digest}")
        print(f"Plan written to: {workdir / 'migration-plan.json'}")
        return 0

    plan_path = workdir / "migration-plan.json"
    if not plan_path.exists():
        logging.error("migration-plan.json does not exist; run --plan first")
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    digest = plan_hash(plan)
    if not args.confirm_plan_hash or args.confirm_plan_hash != digest:
        logging.error("Execution refused: --confirm-plan-hash must exactly match %s", digest)
        return 3

    return execute_plan(plan, workdir, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
