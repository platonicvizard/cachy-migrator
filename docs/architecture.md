# Architecture

## Phase 1 - Analyze

Read-only inventory from `lsblk`, optional SMART health, filesystem metadata, OS markers, free-space readings, and target scoring.

The analyzer is intentionally tolerant of missing optional tools. Missing `smartctl`, `jdupes`, `rdfind`, `sfdisk`, or `sgdisk` is reported in `analysis.json`; the program does not invent unsafe fallbacks.

## Phase 2 - Plan

Planning produces canonical JSON plus a SHA-256 approval hash. The plan separates:

- user-data classification
- destination capacity inventory
- file-copy operations
- duplicate scan operations
- EFI snapshot operations
- partition-table backup operations
- disabled destructive operations
- disabled installer automation gates

Planning remains read-only. The only inputs that should materially change a plan are disk state, selected `--source` roots, selected `--destination` roots, and the work directory used for report paths.

The primary user interface is an interactive one-command flow. Running `cachy-migrator` without `--analyze`, `--plan`, or `--execute` performs analysis, prompts for source and destination paths, writes the plan, runs a dry-run, and then requires a short plan-hash confirmation before real copy execution. The staged flags remain available for automation and debugging.

## Phase 3 - Migrate User Data

The migration engine classifies and places:

- documents
- pictures
- videos
- music
- projects
- virtual machines
- ISOs
- games and Steam-style libraries
- other user data

It excludes common Windows/Linux system artifacts such as `Windows`, `Windows.old`, `Program Files`, recycle bins, `System Volume Information`, `pagefile.sys`, `hiberfil.sys`, `/usr`, `/var`, `/boot`, `/efi`, and `lost+found`.

Placement is capacity-aware. Items are sorted by size and assigned to the destination with the tightest remaining fit that can hold the item. Each destination reserves at least 1 GiB or 5 percent of free space, whichever is larger.

Execution order:

1. Run duplicate scan reports with `jdupes` or `rdfind` when available.
2. Back up partition tables with `sfdisk --dump` and `sgdisk --backup` when available.
3. Snapshot mounted EFI filesystems with `rsync`.
4. Copy planned user data with `rsync -aHAX`.
5. Verify each copy with `rsync -aHAXnc --delete`.
6. Write `rollback-manifest.json`.

Failed copy verification stops execution. Source cleanup is not implemented and should remain manual.

## Phase 4 - Replace OS

Destructive operations are represented in the plan but disabled. A future backend must require all of the following before any partition deletion, formatting, bootloader modification, or installer handoff:

- exact plan-hash confirmation
- current rollback manifest
- successful checksum verification for every enabled copy operation
- partition-table backups for all touched devices
- EFI snapshots for all mounted EFI partitions
- explicit per-device destructive confirmation
- recovery instructions printed before execution

The current implementation refuses enabled destructive operations during `--execute`.
