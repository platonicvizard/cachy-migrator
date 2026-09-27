# Cachy Migrator

A safety-first storage migration and OS replacement preparation tool for systems moving to CachyOS.

The project is designed for a CachyOS or Arch-based live environment when real disks are involved. It can be installed and smoke-tested on any OS with Python 3.11+, but full block-device inventory, EFI snapshots, partition-table backups, and `rsync -aHAX` transfers require Linux tools and Linux filesystems.

## Current Capability

- Read-only block-device inventory with `lsblk`.
- SMART probing with `smartctl` when available.
- Windows/Linux/EFI filesystem marker detection.
- User-data classification for documents, pictures, videos, music, projects, VMs, ISOs, games, and other data.
- Capacity-aware placement across multiple destination roots.
- Duplicate scan planning with `jdupes` or `rdfind`.
- Non-destructive execution with `rsync -aHAX` and checksum verification.
- Rollback manifest generation.
- EFI snapshots for mounted EFI partitions.
- Partition-table backups with `sfdisk --dump` and `sgdisk --backup` when available.
- Partition and installer automation represented in the plan but disabled behind explicit gates.

## Safety Model

1. Analysis is read-only.
2. Planning is read-only.
3. Every plan is serialized to canonical JSON and SHA-256 hashed.
4. Execution requires the exact plan hash.
5. Duplicate scanning is report-only.
6. On Linux, file transfers use `rsync -aHAX`; verification uses `rsync -aHAXnc --delete`.
7. Source deletion is never automatic.
8. Partition-table and EFI backups are created before data copy execution.
9. A rollback manifest records duplicate reports, partition backups, EFI snapshots, and every verified copy.
10. Partition deletion, repartitioning, formatting, and installer launch are not executed by the current backend.

## Install From Source

```bash
git clone https://github.com/<owner>/cachy-migrator.git cachy-migrator
cd cachy-migrator
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Replace `https://github.com/<owner>/cachy-migrator.git` with the real repository URL.

On Windows PowerShell:

```powershell
git clone https://github.com/<owner>/cachy-migrator.git cachy-migrator
cd cachy-migrator
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

## System Packages

### CachyOS / Arch Linux

```bash
sudo pacman -Syu python python-pip rsync smartmontools jdupes rdfind gptfdisk util-linux
```

`lsblk`, `sfdisk`, and related utilities come from `util-linux`; `sgdisk` comes from `gptfdisk`.

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip rsync smartmontools jdupes rdfind gdisk util-linux
```

### Fedora

```bash
sudo dnf install python3 python3-pip rsync smartmontools jdupes rdfind gdisk util-linux
```

### openSUSE

```bash
sudo zypper install python3 python3-pip rsync smartmontools jdupes rdfind gptfdisk util-linux
```

### macOS

```bash
brew install python rsync jdupes rdfind
```

macOS does not provide Linux block-device metadata such as `lsblk`, Linux EFI mounts, or Linux partition-table tooling. Use macOS for development, documentation, and file-only experiments. Use a CachyOS/Linux live USB for real migrations.

The bundled Apple `rsync` also lacks Linux `-A`/`-X` metadata support. Cachy Migrator detects that and uses the strongest supported flags for file-only development runs; use Linux for full-fidelity migration.

### Windows

Use Windows for development or run the tool inside WSL2 for file-only experiments:

```powershell
wsl --install
```

Inside WSL2 Ubuntu:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip rsync smartmontools jdupes rdfind gdisk util-linux
```

WSL2 should not be used for destructive disk replacement workflows. Boot a CachyOS/Linux live USB for physical disk migration.

## One-Command Interactive Flow

For the normal migration workflow, boot the CachyOS live USB, clone/install the project, mount the source and destination partitions, then run one command from the project directory:

```bash
sudo ./.venv/bin/cachy-migrator --workdir /mnt/backup/cachy-migration
```

If you run `cachy-migrator` with no mode flags, it starts the interactive workflow automatically. The wizard:

1. Analyzes attached drives.
2. Shows disk health, OS markers, and the recommended CachyOS target.
3. Detects mounted source and destination paths.
4. Lets you accept discovered paths or enter paths manually.
5. Writes `analysis.json`, `migration-plan.json`, and `migration-plan.sha256`.
6. Runs a dry-run first.
7. Requires plan-hash confirmation before the real copy.
8. Runs duplicate reporting, partition-table backups, EFI snapshots, `rsync` copy, checksum verification, and rollback manifest generation.

It still does not delete partitions, format disks, or launch the installer.

## Advanced CLI

When installed in a project virtual environment, invoke the executable by path when using sudo. A plain `sudo cachy-migrator` usually fails because sudo uses a restricted PATH and does not inherit the activated virtualenv.

Before using a work directory under `/mnt`, mount the intended filesystem there and confirm it:

```bash
lsblk -f
findmnt /mnt/backup
```

For example:

```bash
sudo mkdir -p /mnt/backup
sudo mount /dev/<backup-partition> /mnt/backup
```

Do not create `/mnt/backup` as a normal directory and assume it is the backup disk. Cachy Migrator now refuses a `/mnt/...` workdir unless it is backed by a mounted filesystem.

Analyze attached drives:

```bash
sudo ./.venv/bin/cachy-migrator --analyze --workdir /mnt/backup/cachy-migration
```

Create a migration plan with explicit sources and destinations:

```bash
sudo ./.venv/bin/cachy-migrator --plan \
  --workdir /mnt/backup/cachy-migration \
  --source /mnt/windows/Users \
  --source /mnt/linux-home/home \
  --destination /mnt/backup-drive-a \
  --destination /mnt/backup-drive-b
```

Dry-run the approved non-destructive execution:

```bash
HASH=$(cat /mnt/backup/cachy-migration/migration-plan.sha256)
sudo ./.venv/bin/cachy-migrator --execute \
  --workdir /mnt/backup/cachy-migration \
  --confirm-plan-hash "$HASH" \
  --dry-run
```

Run the approved copy, verification, duplicate report, EFI snapshot, partition-table backup, and rollback-manifest workflow:

```bash
HASH=$(cat /mnt/backup/cachy-migration/migration-plan.sha256)
sudo ./.venv/bin/cachy-migrator --execute \
  --workdir /mnt/backup/cachy-migration \
  --confirm-plan-hash "$HASH"
```

The generated files are:

- `analysis.json`
- `migration-plan.json`
- `migration-plan.sha256`
- `duplicate-report.txt` when `jdupes` or `rdfind` is available
- `partition-tables/*.sfdisk`
- `partition-tables/*.sgdisk`
- `efi-snapshots/*`
- `rollback-manifest.json`
- `cachy-migrator.log`

## CachyOS Live-USB Migration Flow

1. Boot the CachyOS live USB.
2. Connect every source and destination drive.
3. Install runtime tools if they are missing.
4. Clone this project and install it in a virtualenv.
5. Mount source partitions read-only when possible.
6. Mount the backup/destination partition read-write.
7. Run `sudo ./.venv/bin/cachy-migrator --workdir /mnt/backup/cachy-migration` from the project directory.
8. Accept discovered source/destination paths or enter them manually.
9. Review the generated plan summary and warnings.
10. Let the wizard run the dry-run.
11. Confirm the plan hash prefix when ready for the real copy.
12. Review `rollback-manifest.json`.
13. Independently verify that migrated data is readable.
14. Only after successful verification, proceed with manual CachyOS installer partitioning.

Minimal CachyOS live-USB command sequence:

```bash
sudo pacman -Syu git python python-pip rsync smartmontools jdupes rdfind gptfdisk util-linux
git clone https://github.com/<owner>/cachy-migrator.git cachy-migrator
cd cachy-migrator
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

lsblk -f
sudo mkdir -p /mnt/backup
sudo mount /dev/<backup-partition> /mnt/backup

sudo ./.venv/bin/cachy-migrator --workdir /mnt/backup/cachy-migration
```

## Mounting Examples

List disks:

```bash
lsblk -f
```

Mount a Linux home partition read-only:

```bash
sudo mkdir -p /mnt/linux-home
sudo mount -o ro /dev/nvme0n1p3 /mnt/linux-home
```

Mount a Windows data partition read-only:

```bash
sudo mkdir -p /mnt/windows
sudo mount -o ro,uid=1000,gid=1000 /dev/sda3 /mnt/windows
```

Mount a backup destination:

```bash
sudo mkdir -p /mnt/backup-drive-a
sudo mount /dev/sdb1 /mnt/backup-drive-a
```

## Common Command Failures

`sudo cachy-migrator: command not found`

Use the virtualenv executable from the project directory:

```bash
sudo ./.venv/bin/cachy-migrator --workdir /mnt/backup/cachy-migration
```

`ERROR: /mnt/... is under /mnt, but no mounted filesystem backs that path`

Mount the real backup partition first. The tool refuses to write migration state into the live USB root filesystem by accident.

```bash
lsblk -f
sudo mkdir -p /mnt/backup
sudo mount /dev/<backup-partition> /mnt/backup
findmnt /mnt/backup
```

`git clone <repo-url>` fails

`<repo-url>` is a placeholder. Use the real GitHub repository URL.

## Development

Run syntax checks:

```bash
python -m compileall src tests
```

Run tests when `pytest` is installed:

```bash
python -m pip install pytest
python -m pytest -q
```

## Status

This is still safety-first migration software. It now contains the non-destructive migration engine, but automated partitioning and installer launch remain disabled until a separately reviewed gated backend is implemented.
