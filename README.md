# Xbox 360 Content Manager v2

All-in-one PC tool for Xbox 360 content: STFS + SVOD parsing, XBLA/DLC
unlock (with automatic backup), parallel FTP uploads to Aurora with
verification, XboxUnity Title Update downloads, and direct FATX device
reading.

## Install

    pip install -r requirements.txt

## GUI

    python xbox360_manager.py

New in v2: drag files/folders onto the window to scan them; right-click
any row for a context menu; use the Search box to filter; set the
"Workers" spinner to 2–4 for parallel uploads; toggle "Verify" to check
remote file sizes after every upload.

## CLI

    # Scan
    python xbox360_manager.py scan /path/to/content

    # Unlock (creates .x360cm.bak sidecars by default)
    python xbox360_manager.py unlock /path/to/content

    # Restore from sidecar backups
    python xbox360_manager.py restore /path/to/content

    # Upload (parallel + verified by default at --workers 4)
    python xbox360_manager.py upload /path/to/content \
        --host 192.168.1.100 --workers 4

    # Title updates
    python xbox360_manager.py tu-download 12345678 --out ./tus
    python xbox360_manager.py tu-upload 12345678 --title-id 545408A7 \
        --host 192.168.1.100

    # FATX device
    python xbox360_manager.py fatx-list /dev/sdb --offset 0x80000
    python xbox360_manager.py fatx-extract /dev/sdb --offset 0x80000 \
        --filter "545408A7" --out ./extracted

## What's new in v2

- **FATX/XTAF reading** — open a USB drive or HDD image directly and
  scan it for STFS packages. No console required.
- **Parallel uploads** — `--workers N` spawns N FTP connections; each
  worker handles its own file. Verified by size after every transfer.
- **Backup & restore** — every unlock writes a `.x360cm.bak` sidecar.
  `restore` puts it back. Unlock is now reversible.
- **Drag & drop + context menu** in the GUI.
- **Byte-level progress with ETA** in the GUI upload bar.
- **Search filter** above the table.

## Compatibility

v1 CLI invocations and Python imports still work unchanged. The only
new required argument anywhere is that `unlock` now optionally accepts
`--no-backup`.

## Notes on unlocking

Unlock zeroes the full 0x100-byte license block at offset 0x22C in the
STFS metadata (or the SVOD header inside a GOD folder). The STFS hash
tree lives in block 1, so block 0 changes don't invalidate hashes.

## Notes on FATX

The FATX reader is read-only — safe to point at a live USB drive. If
your device isn't recognized at offset 0, try common offsets like
`0x80000` (internal HDD content partition). Use `fatx-list` to verify
before extracting.
