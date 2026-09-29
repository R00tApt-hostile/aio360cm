# Xbox 360 Content Manager

All-in-one PC tool for managing Xbox 360 content: parses STFS
(CON/LIVE/PIRS) packages and SVOD (GOD) folders, unlocks XBLA/DLC,
downloads Title Updates from XboxUnity, and uploads everything to
Aurora via FTP.

## Install

    pip install -r requirements.txt

## GUI

    python xbox360_manager.py

1. Add files or folders with the toolbar buttons.
2. Select rows and click **Unlock Selected** to patch XBLA/DLC.
3. Enter your console's FTP details (default Aurora: `xboxftp`/`xboxftp`).
4. Click **Upload Selected to Xbox** — files are placed under
   `Content/0000000000000000/{TitleID}/…` automatically.
5. Click **Download TUs** to fetch matching Title Updates from XboxUnity.

## CLI

    # Scan for content
    python xbox360_manager.py scan /path/to/content

    # Unlock XBLA/DLC
    python xbox360_manager.py unlock /path/to/content

    # Upload to console
    python xbox360_manager.py upload /path/to/content --host 192.168.1.100

    # Download a Title Update by MediaID
    python xbox360_manager.py tu-download 12345678 --out ./tus

    # Download and upload a Title Update in one step
    python xbox360_manager.py tu-upload 12345678 --title-id 545408A7 \
        --host 192.168.1.100

## Supported content

| Type                | Detection | Unlock | Upload | TU Download |
|---------------------|-----------|--------|--------|-------------|
| XBLA (0x0004)       | ✅        | ✅     | ✅     | ✅          |
| DLC / Add-on (0x02) | ✅        | ✅     | ✅     | ✅          |
| GOD (SVOD folder)   | ✅        | ✅     | ✅     | ✅          |
| Title Update (0x0D) | ✅        | n/a    | ✅     | ✅          |
| Save Game (0x01)    | ✅        | n/a    | ✅     | n/a         |

## Notes on unlocking

The unlock zeroes the entire 0x100-byte licensing data block at offset
`0x22C` in the STFS metadata. Because the STFS hash tree lives in block 1
(not block 0), modifying block 0 does not invalidate any stored hashes
and no re-hashing is required.

For GOD folders, the same license block is zeroed inside the SVOD header
file (the only file in the folder with an STFS magic).

## Notes on Title Updates

Title Updates are fetched from the XboxUnity API using the game's
MediaID. The client automatically selects the highest-version TU
available and saves it with a standard filename. Title Updates must be
placed in `Content/0000000000000000/{TitleID}/000B0000/` on the console.
