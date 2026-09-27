from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Partition:
    name: str
    path: str
    size: int | None = None
    fstype: str | None = None
    label: str | None = None
    mountpoint: str | None = None
    parttype: str | None = None
    uuid: str | None = None
    free_bytes: int = 0


@dataclass
class Drive:
    name: str
    path: str
    size: int | None = None
    model: str | None = None
    rota: bool | None = None
    tran: str | None = None
    mountpoint: str | None = None
    smart_status: str = "unknown"
    smart_score: int = 0
    free_bytes: int = 0
    partitions: list[Partition] = field(default_factory=list)
    os_markers: list[str] = field(default_factory=list)
    score: int = 0
    score_reasons: list[str] = field(default_factory=list)


@dataclass
class UserDataItem:
    source: str
    relative_path: str
    category: str
    bytes: int
    files: int
    excluded: bool = False
    reason: str | None = None


@dataclass
class Destination:
    path: str
    free_bytes: int
    usable_bytes: int


CATEGORY_NAMES: dict[str, set[str]] = {
    "documents": {"desktop", "documents", "downloads", "notes", "scans"},
    "pictures": {"pictures", "photos", "images", "screenshots", "camera roll"},
    "videos": {"videos", "movies"},
    "music": {"music", "audio"},
    "projects": {"code", "dev", "developer", "development", "projects", "src", "source", "workspace", "workspaces"},
    "vms": {"virtualbox vms", "vmware", "vms", "virtual machines", "libvirt", "qemu"},
    "isos": {"iso", "isos", "images-iso"},
    "games": {"steam", "steamapps", "games", "lutris", "heroic", "bottles"},
}

SYSTEM_NAMES = {
    "$recycle.bin",
    "$windows.~bt",
    "$windows.~ws",
    "boot",
    "efi",
    "lost+found",
    "msocache",
    "pagefile.sys",
    "perflogs",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "swapfile.sys",
    "system volume information",
    "usr",
    "var",
    "windows",
    "windows.old",
}

HOME_CONTAINER_NAMES = {"home", "users"}
DEFAULT_RESERVE_BYTES = 1024**3


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def lsblk_inventory() -> list[Drive]:
    cmd = [
        "lsblk", "-J", "-b",
        "-o", "NAME,PATH,SIZE,FSTYPE,LABEL,MOUNTPOINT,MODEL,TYPE,ROTA,TRAN,PARTTYPE,UUID"
    ]
    result = run(cmd, check=True)
    data = json.loads(result.stdout)
    drives: list[Drive] = []

    for item in data.get("blockdevices", []):
        if item.get("type") != "disk":
            continue
        drive = Drive(
            name=item.get("name"),
            path=item.get("path") or f"/dev/{item.get('name')}",
            size=_int_or_none(item.get("size")),
            model=(item.get("model") or "").strip() or None,
            rota=_bool_or_none(item.get("rota")),
            tran=item.get("tran"),
            mountpoint=item.get("mountpoint"),
        )
        for child in item.get("children") or []:
            if child.get("type") != "part":
                continue
            drive.partitions.append(Partition(
                name=child.get("name"),
                path=child.get("path") or f"/dev/{child.get('name')}",
                size=_int_or_none(child.get("size")),
                fstype=child.get("fstype"),
                label=child.get("label"),
                mountpoint=child.get("mountpoint"),
                parttype=child.get("parttype"),
                uuid=child.get("uuid"),
                free_bytes=mount_free_bytes(child.get("mountpoint")),
            ))
        drives.append(drive)
    return drives


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _bool_or_none(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    try:
        return bool(int(v))
    except (TypeError, ValueError):
        return None


def smart_probe(drive: Drive) -> None:
    if not command_exists("smartctl"):
        drive.smart_status = "smartctl-unavailable"
        return
    proc = run(["smartctl", "-H", "-j", drive.path])
    if not proc.stdout.strip():
        drive.smart_status = "unavailable"
        return
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        drive.smart_status = "unparseable"
        return
    passed = ((data.get("smart_status") or {}).get("passed"))
    if passed is True:
        drive.smart_status = "passed"
        drive.smart_score = 25
    elif passed is False:
        drive.smart_status = "failed"
        drive.smart_score = -100
    else:
        drive.smart_status = "unknown"


def mount_free_bytes(mountpoint: str | None) -> int:
    if not mountpoint:
        return 0
    try:
        return shutil.disk_usage(mountpoint).free
    except OSError:
        return 0


def detect_os_markers(drive: Drive) -> None:
    markers: list[str] = []
    for p in drive.partitions:
        fs = (p.fstype or "").lower()
        label = (p.label or "").lower()
        if fs == "ntfs":
            markers.append(f"possible-windows:{p.path}")
        if "windows" in label:
            markers.append(f"windows-label:{p.path}")
        if fs in {"ext4", "btrfs", "xfs", "f2fs"}:
            markers.append(f"linux-filesystem:{p.path}")
        if fs in {"vfat", "fat32"} and ("efi" in label or p.parttype):
            markers.append(f"efi:{p.path}")
    drive.os_markers = sorted(set(markers))


def score_drive(drive: Drive) -> None:
    score = 0
    reasons: list[str] = []

    if drive.smart_status == "failed":
        drive.score = 0
        drive.score_reasons = ["SMART health check failed; do not use as target"]
        return

    score += drive.smart_score
    if drive.smart_score:
        reasons.append(f"SMART health +{drive.smart_score}")

    tran = (drive.tran or "").lower()
    model = (drive.model or "").lower()
    if tran == "nvme" or "nvme" in model:
        score += 30
        reasons.append("NVMe +30")
    elif drive.rota is False:
        score += 22
        reasons.append("SSD/non-rotational +22")
    elif drive.rota is True:
        score += 8
        reasons.append("rotational drive +8")

    size = drive.size or 0
    if size >= 2 * 1024**4:
        score += 20
        reasons.append("capacity >=2TB +20")
    elif size >= 1024**4:
        score += 15
        reasons.append("capacity >=1TB +15")
    elif size >= 512 * 1024**3:
        score += 10
        reasons.append("capacity >=512GB +10")

    clutter = sum(1 for m in drive.os_markers if m.startswith(("possible-windows", "linux-filesystem")))
    penalty = min(clutter * 3, 12)
    if penalty:
        score -= penalty
        reasons.append(f"existing OS/filesystem clutter -{penalty}")

    drive.score = max(0, min(100, score))
    drive.score_reasons = reasons


def analyze() -> dict[str, Any]:
    drives = lsblk_inventory()
    warnings: list[str] = []
    for drive in drives:
        smart_probe(drive)
        detect_os_markers(drive)
        drive.free_bytes = mount_free_bytes(drive.mountpoint)
        for partition in drive.partitions:
            partition.free_bytes = mount_free_bytes(partition.mountpoint)
        score_drive(drive)
        if drive.smart_status == "failed":
            warnings.append(f"{drive.path}: SMART reports failure")
    drives.sort(key=lambda d: d.score, reverse=True)
    return {
        "version": 1,
        "drives": [asdict(d) for d in drives],
        "warnings": warnings,
        "optional_tools": {
            name: command_exists(name)
            for name in ["smartctl", "os-prober", "rsync", "jdupes", "rdfind", "blkid", "sfdisk", "sgdisk"]
        },
    }


def human_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    value = float(n)
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{n}B"


def classify_user_data(source_roots: list[str | Path]) -> list[UserDataItem]:
    items: list[UserDataItem] = []
    for root_value in source_roots:
        root = Path(root_value).expanduser().resolve()
        if not root.exists():
            items.append(UserDataItem(
                source=str(root),
                relative_path=".",
                category="missing",
                bytes=0,
                files=0,
                excluded=True,
                reason="source path does not exist",
            ))
            continue
        for candidate in _classification_candidates(root):
            rel = _safe_relative(candidate, root)
            excluded, reason = _is_excluded_candidate(candidate)
            category = _category_for_path(candidate)
            total_bytes, files = directory_stats(candidate) if not excluded else (0, 0)
            items.append(UserDataItem(
                source=str(candidate),
                relative_path=rel,
                category=category,
                bytes=total_bytes,
                files=files,
                excluded=excluded,
                reason=reason,
            ))
    items.sort(key=lambda item: (item.excluded, item.category, item.relative_path.lower()))
    return items


def _classification_candidates(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    children = _safe_iterdir(root)
    home_container = root.name.lower() in HOME_CONTAINER_NAMES
    if home_container:
        candidates: list[Path] = []
        for user_dir in children:
            if user_dir.is_dir() and not _is_hidden_system(user_dir):
                candidates.extend(_safe_iterdir(user_dir) or [user_dir])
        return candidates
    return children or [root]


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []


def _safe_relative(path: Path, root: Path) -> str:
    try:
        value = path.relative_to(root)
    except ValueError:
        return path.name
    return "." if str(value) == "." else str(value)


def _is_hidden_system(path: Path) -> bool:
    return path.name.startswith(".") or path.name.lower() in SYSTEM_NAMES


def _is_excluded_candidate(path: Path) -> tuple[bool, str | None]:
    lower_name = path.name.lower()
    if lower_name in SYSTEM_NAMES:
        return True, "system or OS artifact"
    if lower_name.startswith(("$", ".")) and lower_name not in {".ssh", ".gnupg"}:
        return True, "hidden/system artifact"
    if path.is_file() and lower_name in {"hiberfil.sys", "pagefile.sys", "swapfile.sys"}:
        return True, "Windows paging/hibernation file"
    return False, None


def _category_for_path(path: Path) -> str:
    lower_name = path.name.lower()
    for category, names in CATEGORY_NAMES.items():
        if lower_name in names:
            return category
    suffix = path.suffix.lower()
    if suffix in {".doc", ".docx", ".odt", ".pdf", ".txt", ".xls", ".xlsx", ".csv"}:
        return "documents"
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".heic", ".raw", ".webp"}:
        return "pictures"
    if suffix in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
        return "videos"
    if suffix in {".mp3", ".flac", ".wav", ".m4a", ".ogg"}:
        return "music"
    if suffix == ".iso":
        return "isos"
    return "other"


def directory_stats(path: Path) -> tuple[int, int]:
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    total = 0
    files = 0
    for dirpath, dirnames, filenames in os.walk(path, onerror=lambda _: None):
        dirnames[:] = [name for name in dirnames if name.lower() not in SYSTEM_NAMES]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            try:
                stat = file_path.stat()
            except OSError:
                continue
            if not file_path.is_symlink():
                total += stat.st_size
            files += 1
    return total, files


def destination_inventory(destination_roots: list[str | Path]) -> list[Destination]:
    destinations: list[Destination] = []
    for value in destination_roots:
        path = Path(value).expanduser().resolve()
        free_bytes = mount_free_bytes(str(path.parent if not path.exists() else path))
        reserve = max(DEFAULT_RESERVE_BYTES, int(free_bytes * 0.05))
        destinations.append(Destination(
            path=str(path),
            free_bytes=free_bytes,
            usable_bytes=max(0, free_bytes - reserve),
        ))
    destinations.sort(key=lambda d: d.usable_bytes, reverse=True)
    return destinations


def filter_safe_destinations(
    source_roots: list[str | Path],
    destination_roots: list[str | Path],
) -> tuple[list[str], list[str]]:
    safe: list[str] = []
    warnings: list[str] = []
    resolved_sources = [Path(source).expanduser().resolve() for source in source_roots]
    for destination_value in destination_roots:
        destination = Path(destination_value).expanduser().resolve()
        overlap = next((source for source in resolved_sources if _same_or_nested(destination, source)), None)
        if overlap is not None:
            warnings.append(f"Destination {destination} is inside selected source {overlap}; skipped to avoid recursive copy")
            continue
        safe.append(str(destination))
    return safe, warnings


def _same_or_nested(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def plan_copy_operations(items: list[UserDataItem], destinations: list[Destination]) -> tuple[list[dict[str, Any]], list[str]]:
    operations: list[dict[str, Any]] = []
    warnings: list[str] = []
    remaining = {d.path: d.usable_bytes for d in destinations}
    movable = [item for item in items if not item.excluded and item.bytes > 0]
    for item in sorted(movable, key=lambda x: x.bytes, reverse=True):
        destination = _choose_destination(item.bytes, destinations, remaining)
        if destination is None:
            warnings.append(f"No destination has enough usable free space for {item.source} ({human_bytes(item.bytes)})")
            continue
        source_label = _safe_label(Path(item.source).anchor or "source")
        rel_parts = [part for part in Path(item.relative_path).parts if part not in {"", "."}]
        dest_path = Path(destination.path) / "cachy-migrator-data" / source_label / item.category
        for part in rel_parts:
            dest_path /= _safe_label(part)
        operations.append({
            "source": item.source,
            "destination": str(dest_path),
            "category": item.category,
            "bytes": item.bytes,
            "files": item.files,
            "tool": "rsync",
            "args": ["-aHAX"],
            "verify": "rsync-checksum",
            "enabled": True,
        })
        remaining[destination.path] -= item.bytes
    return operations, warnings


def _choose_destination(size: int, destinations: list[Destination], remaining: dict[str, int]) -> Destination | None:
    candidates = [d for d in destinations if remaining.get(d.path, 0) >= size]
    if not candidates:
        return None
    return min(candidates, key=lambda d: remaining[d.path] - size)


def _safe_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    return cleaned.strip("._") or "item"


def mounted_user_sources(analysis: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    for drive in analysis.get("drives", []):
        for partition in drive.get("partitions", []):
            mountpoint = partition.get("mountpoint")
            fstype = (partition.get("fstype") or "").lower()
            if mountpoint and fstype not in {"vfat", "fat32"}:
                sources.append(mountpoint)
    return sorted(set(sources))


def mounted_destinations(analysis: dict[str, Any]) -> list[str]:
    destinations: list[str] = []
    for drive in analysis.get("drives", []):
        for partition in drive.get("partitions", []):
            mountpoint = partition.get("mountpoint")
            if mountpoint and partition.get("free_bytes", 0) > DEFAULT_RESERVE_BYTES:
                destinations.append(mountpoint)
    return sorted(set(destinations))


def duplicate_scan_operations(source_roots: list[str | Path], workdir: Path) -> list[dict[str, Any]]:
    tool = "jdupes" if command_exists("jdupes") else "rdfind" if command_exists("rdfind") else None
    if tool is None:
        return []
    roots = [str(Path(root).expanduser().resolve()) for root in source_roots if Path(root).expanduser().exists()]
    if not roots:
        return []
    report = workdir / "duplicate-report.txt"
    command = [tool, "-r", *roots] if tool == "jdupes" else [tool, "-dryrun", "true", *roots]
    return [{
        "tool": tool,
        "command": command,
        "report": str(report),
        "enabled": True,
    }]


def efi_snapshot_operations(analysis: dict[str, Any], workdir: Path) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for drive in analysis.get("drives", []):
        for partition in drive.get("partitions", []):
            fstype = (partition.get("fstype") or "").lower()
            label = (partition.get("label") or "").lower()
            is_efi = fstype in {"vfat", "fat32"} and ("efi" in label or partition.get("parttype"))
            if not is_efi:
                continue
            name = _safe_label(partition.get("name") or partition.get("path") or "efi")
            operations.append({
                "device": partition.get("path"),
                "mountpoint": partition.get("mountpoint"),
                "destination": str(workdir / "efi-snapshots" / name),
                "enabled": bool(partition.get("mountpoint")),
                "reason": None if partition.get("mountpoint") else "EFI partition is not mounted",
            })
    return operations


def partition_table_backup_operations(analysis: dict[str, Any], workdir: Path) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for drive in analysis.get("drives", []):
        name = _safe_label(drive.get("name") or drive.get("path") or "disk")
        operations.append({
            "device": drive.get("path"),
            "sfdisk_dump": str(workdir / "partition-tables" / f"{name}.sfdisk"),
            "sgdisk_backup": str(workdir / "partition-tables" / f"{name}.sgdisk"),
            "enabled": True,
        })
    return operations


def plan_from_analysis(
    analysis: dict[str, Any],
    source_roots: list[str | Path] | None = None,
    destination_roots: list[str | Path] | None = None,
    workdir: str | Path = ".",
) -> dict[str, Any]:
    drives = analysis.get("drives", [])
    healthy = [d for d in drives if d.get("smart_status") != "failed"]
    target = healthy[0] if healthy else None
    alternatives = healthy[1:]
    workdir_path = Path(workdir).resolve()
    source_values = list(source_roots or mounted_user_sources(analysis))
    destination_values, destination_warnings = filter_safe_destinations(
        source_values,
        list(destination_roots or mounted_destinations(analysis)),
    )
    classified = classify_user_data(source_values) if source_values else []
    destinations = destination_inventory(destination_values) if destination_values else []
    copy_operations, placement_warnings = plan_copy_operations(classified, destinations)

    plan: dict[str, Any] = {
        "version": 2,
        "target": target,
        "alternatives": alternatives,
        "steps": [],
        "user_data": [asdict(item) for item in classified],
        "destinations": [asdict(destination) for destination in destinations],
        "copy_operations": copy_operations,
        "duplicate_scan_operations": duplicate_scan_operations(source_values, workdir_path),
        "efi_snapshot_operations": efi_snapshot_operations(analysis, workdir_path),
        "partition_table_backup_operations": partition_table_backup_operations(analysis, workdir_path),
        "destructive_operations": [],
        "installer_automation": {
            "enabled": False,
            "reason": "Installer automation remains disabled until manifests, EFI snapshots, partition-table backups, and checksum verification are complete.",
            "required_gates": [
                "exact plan hash confirmation",
                "rollback manifest present",
                "partition-table backups present",
                "EFI snapshots reviewed",
                "all copy operations checksum-verified",
                "per-device destructive confirmation",
            ],
        },
        "rollback_manifest": str(workdir_path / "rollback-manifest.json"),
        "warnings": list(analysis.get("warnings", [])) + destination_warnings + placement_warnings,
    }

    if target:
        plan["steps"].extend([
            "Review duplicate candidates before migration",
            "Consolidate user data to verified destination storage",
            f"Back up EFI before modifying {target['path']}",
            "Verify all copied data using rsync checksum comparison",
            f"Prepare {target['path']} for CachyOS installation",
            "Launch CachyOS installer after operator approval",
            "Restore user data after installation",
        ])
        plan["destructive_operations"].append({
            "device": target["path"],
            "action": "repartition-for-cachyos",
            "enabled": False,
            "reason": "Destructive backend is represented but disabled until all non-destructive migration gates pass",
        })
    else:
        plan["warnings"].append("No healthy target drive could be recommended")
    if source_values and not copy_operations:
        plan["warnings"].append("No user-data copy operations were planned from the selected sources")
    if classified and not destinations:
        plan["warnings"].append("No destination roots were selected or discovered")

    return plan


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def plan_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(plan).encode("utf-8")).hexdigest()


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(canonical_json(data) + "\n", encoding="utf-8")


def rsync_copy(source: str, destination: str, dry_run: bool = False) -> None:
    if not command_exists("rsync"):
        raise RuntimeError("rsync is required for execution")
    Path(destination).mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", *_rsync_flags(), *_rsync_progress_flags()]
    if dry_run:
        cmd.append("--dry-run")
    cmd += [_rsync_source(source), _rsync_destination(destination)]
    subprocess.run(cmd, check=True)


def rsync_verify(source: str, destination: str) -> None:
    flags = _rsync_flags(checksum=True)
    cmd = ["rsync", *flags, "--delete", _rsync_source(source), _rsync_destination(destination)]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "rsync verification failed")
    if proc.stdout.strip():
        raise RuntimeError("checksum verification found differences")


def _rsync_source(source: str) -> str:
    path = Path(source)
    value = str(path)
    if path.is_dir() and not value.endswith(os.sep):
        return value + os.sep
    return value


def _rsync_destination(destination: str) -> str:
    value = str(destination)
    if not value.endswith(os.sep):
        return value + os.sep
    return value


def _rsync_flags(checksum: bool = False) -> list[str]:
    if _rsync_supports_short_flag("A") and _rsync_supports_short_flag("X"):
        return ["-aHAXc" if checksum else "-aHAX"]
    flags = ["-aHc" if checksum else "-aH"]
    if platform.system() == "Darwin" and _rsync_supports_short_flag("E"):
        flags[0] += "E"
    elif _rsync_supports_long_option("--extended-attributes"):
        flags.append("--extended-attributes")
    return flags


def _rsync_progress_flags() -> list[str]:
    if _rsync_supports_long_option("--info="):
        return ["--info=progress2"]
    if _rsync_supports_long_option("--progress"):
        return ["--progress"]
    return []


def _rsync_supports_short_flag(flag: str) -> bool:
    help_text = _rsync_help()
    return f"[{flag}" in help_text or flag in _rsync_option_letters(help_text)


def _rsync_supports_long_option(option: str) -> bool:
    return option in _rsync_help()


def _rsync_option_letters(help_text: str) -> set[str]:
    letters: set[str] = set()
    for line in help_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[-") and "]" in stripped:
            letters.update(ch for ch in stripped if ch.isalpha())
    return letters


def _rsync_help() -> str:
    if not command_exists("rsync"):
        return ""
    proc = subprocess.run(["rsync", "--help"], text=True, capture_output=True)
    return proc.stdout + proc.stderr


def run_duplicate_scans(plan: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for operation in plan.get("duplicate_scan_operations", []):
        if not operation.get("enabled"):
            continue
        report = Path(operation["report"])
        report.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(operation["command"], text=True, capture_output=True)
        report.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        results.append({
            "tool": operation.get("tool"),
            "report": str(report),
            "returncode": proc.returncode,
        })
    return results


def backup_partition_tables(plan: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for operation in plan.get("partition_table_backup_operations", []):
        if not operation.get("enabled"):
            continue
        device = operation.get("device")
        sfdisk_dump = Path(operation["sfdisk_dump"])
        sgdisk_backup = Path(operation["sgdisk_backup"])
        sfdisk_dump.parent.mkdir(parents=True, exist_ok=True)
        result: dict[str, Any] = {"device": device, "sfdisk_dump": str(sfdisk_dump), "sgdisk_backup": str(sgdisk_backup)}
        if command_exists("sfdisk") and device:
            proc = subprocess.run(["sfdisk", "--dump", device], text=True, capture_output=True)
            sfdisk_dump.write_text(proc.stdout + proc.stderr, encoding="utf-8")
            result["sfdisk_returncode"] = proc.returncode
        else:
            result["sfdisk_skipped"] = "sfdisk unavailable or missing device"
        if command_exists("sgdisk") and device:
            proc = subprocess.run(["sgdisk", "--backup", str(sgdisk_backup), device], text=True, capture_output=True)
            result["sgdisk_returncode"] = proc.returncode
            if proc.stderr:
                result["sgdisk_stderr"] = proc.stderr.strip()
        else:
            result["sgdisk_skipped"] = "sgdisk unavailable or missing device"
        results.append(result)
    return results


def snapshot_efi(plan: dict[str, Any], dry_run: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for operation in plan.get("efi_snapshot_operations", []):
        if not operation.get("enabled"):
            results.append({
                "device": operation.get("device"),
                "status": "skipped",
                "reason": operation.get("reason") or "operation disabled",
            })
            continue
        source = operation.get("mountpoint")
        destination = operation.get("destination")
        if not source or not destination:
            continue
        rsync_copy(source, destination, dry_run=dry_run)
        if not dry_run:
            rsync_verify(source, destination)
        results.append({"device": operation.get("device"), "source": source, "destination": destination, "status": "verified" if not dry_run else "dry-run"})
    return results


def execute_copy_operations(plan: dict[str, Any], dry_run: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for operation in plan.get("copy_operations", []):
        if not operation.get("enabled"):
            continue
        source = operation["source"]
        destination = operation["destination"]
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record: dict[str, Any] = {
            "source": source,
            "destination": destination,
            "category": operation.get("category"),
            "bytes": operation.get("bytes"),
            "files": operation.get("files"),
            "started_at": started,
            "dry_run": dry_run,
        }
        try:
            rsync_copy(source, destination, dry_run=dry_run)
            if not dry_run:
                rsync_verify(source, destination)
            record["status"] = "verified" if not dry_run else "dry-run"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            results.append(record)
            raise
        finally:
            record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        results.append(record)
    return results


def write_rollback_manifest(
    path: Path,
    plan: dict[str, Any],
    duplicate_results: list[dict[str, Any]],
    partition_results: list[dict[str, Any]],
    efi_results: list[dict[str, Any]],
    copy_results: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = {
        "version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "plan_hash": plan_hash(plan),
        "duplicate_results": duplicate_results,
        "partition_table_backups": partition_results,
        "efi_snapshots": efi_results,
        "copy_results": copy_results,
        "restore_notes": [
            "Copy results map each original source to its verified destination.",
            "EFI snapshots are byte-for-byte rsync copies of mounted EFI filesystem contents.",
            "Partition-table backups are for emergency recovery and must be restored only by an experienced operator.",
        ],
    }
    save_json(path, manifest)
    return manifest
