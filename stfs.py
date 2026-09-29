"""STFS (Secure Transacted File System) parser and unlocker for Xbox 360.

Correctly parses CON / LIVE / PIRS packages (XBLA, DLC, Title Updates, Saves)
using the official Free60 header layout, and provides robust unlocking by
zeroing the full 0x100-byte license block at offset 0x22C.
"""

import struct
from dataclasses import dataclass
from typing import Optional

MAGIC_CON = b"CON "
MAGIC_LIVE = b"LIVE"
MAGIC_PIRS = b"PIRS"
VALID_MAGICS = (MAGIC_CON, MAGIC_LIVE, MAGIC_PIRS)

BLOCK_SIZE = 0x1000
STFS_HEADER_SIZE = 0x1000

# ---------------------------------------------------------------------------
# Content type values (from the Free60 wiki and xVerter documentation)
# ---------------------------------------------------------------------------
CONTENT_TYPE_SAVE = 0x00000001
CONTENT_TYPE_MARKETPLACE = 0x00000002
CONTENT_TYPE_XBLA = 0x00000004
CONTENT_TYPE_GOD = 0x00000008
CONTENT_TYPE_AVATAR = 0x00000009
CONTENT_TYPE_PROFILE = 0x0000000A
CONTENT_TYPE_TITLE_UPDATE = 0x0000000D

CONTENT_TYPE_NAMES = {
    0x00000001: "Save Game",
    0x00000002: "Marketplace Content (DLC)",
    0x00000003: "Publisher",
    0x00000004: "Xbox Live Arcade",
    0x00000005: "Xbox Original",
    0x00000006: "Xbox 360 Title",
    0x00000007: "Game Demo",
    0x00000008: "Game on Demand",
    0x00000009: "Avatar Item",
    0x0000000A: "Profile",
    0x0000000B: "Picture",
    0x0000000C: "Video",
    0x0000000D: "Title Update",
    0x0000000E: "Game Title",
    0x0000000F: "Installed Game",
    0x00000010: "Game Add-On",
    0x00000011: "Game Add-On",
    0x00000012: "Game Add-On",
}

# The licensing data block in the STFS metadata section.
# Zeroing this entire 0x100-byte region removes all license checks.
LICENSE_BLOCK_OFFSET = 0x22C
LICENSE_BLOCK_SIZE = 0x100

# Descriptor type marker (STFS = 0, SVOD = 1) at 0x3A9
DESCRIPTOR_TYPE_OFFSET = 0x3A9

# Metadata offsets (authoritative, from Free60 wiki)
METADATA_OFFSET = 0x344
OFF_CONTENT_TYPE = 0x344
OFF_METADATA_VERSION = 0x348
OFF_CONTENT_SIZE = 0x34C
OFF_MEDIA_ID = 0x354
OFF_VERSION = 0x358
OFF_BASE_VERSION = 0x35C
OFF_TITLE_ID = 0x360
OFF_PLATFORM = 0x364
OFF_EXECUTABLE_TYPE = 0x365
OFF_DISC_NUMBER = 0x366
OFF_DISC_IN_SET = 0x367
OFF_SAVE_GAME_ID = 0x368
OFF_CONSOLE_ID = 0x36C
OFF_PROFILE_ID = 0x371
OFF_VOLUME_DESCRIPTOR = 0x379
OFF_DATA_FILE_COUNT = 0x39D
OFF_DATA_FILE_COMBINED_SIZE = 0x3A1
OFF_DESCRIPTOR_TYPE = 0x3A9
OFF_DEVICE_ID = 0x3FD

# Display string offsets (UTF-16BE)
OFF_DISPLAY_NAME = 0x411
OFF_DISPLAY_DESCRIPTION = 0x491
OFF_PUBLISHER_NAME = 0x511
OFF_TITLE_NAME = 0x551

# Legacy offsets (fallback)
OFF_LEGACY_DISPLAY_NAME = 0x344
OFF_LEGACY_DISPLAY_DESC = 0x3C4
OFF_LEGACY_PUBLISHER = 0x444
OFF_LEGACY_TITLE = 0x484


@dataclass
class StfsHeader:
    magic: bytes = b""
    content_type: int = 0
    metadata_version: int = 0
    content_size: int = 0
    media_id: int = 0
    version: int = 0
    base_version: int = 0
    title_id: int = 0
    platform: int = 0
    executable_type: int = 0
    disc_number: int = 0
    disc_in_set: int = 0
    save_game_id: int = 0
    console_id: bytes = b""
    profile_id: bytes = b""
    volume_type: int = 0
    block_count: int = 0
    descriptor_type: int = 0  # 0 = STFS, 1 = SVOD
    display_name: str = ""
    display_description: str = ""
    publisher_name: str = ""
    title_name: str = ""

    @property
    def magic_str(self) -> str:
        return self.magic.decode("ascii", errors="replace")

    @property
    def content_type_name(self) -> str:
        return CONTENT_TYPE_NAMES.get(
            self.content_type & 0x0FFFFFFF,
            f"Unknown (0x{self.content_type:08X})",
        )

    @property
    def title_id_hex(self) -> str:
        return f"{self.title_id:08X}"

    @property
    def media_id_hex(self) -> str:
        return f"{self.media_id:08X}"

    @property
    def is_xbla(self) -> bool:
        return (self.content_type & 0x0FFFFFFF) == CONTENT_TYPE_XBLA

    @property
    def is_dlc(self) -> bool:
        return (self.content_type & 0x0FFFFFFF) in (
            0x02, 0x10, 0x11, 0x12,
        )

    @property
    def is_god(self) -> bool:
        return (self.content_type & 0x0FFFFFFF) == CONTENT_TYPE_GOD

    @property
    def is_title_update(self) -> bool:
        return (self.content_type & 0x0FFFFFFF) == CONTENT_TYPE_TITLE_UPDATE

    @property
    def is_save(self) -> bool:
        return (self.content_type & 0x0FFFFFFF) == CONTENT_TYPE_SAVE


def _read_utf16be(data: bytes, offset: int, length: int) -> str:
    if offset + length > len(data):
        return ""
    raw = data[offset:offset + length]
    try:
        text = raw.decode("utf-16-be", errors="ignore")
        idx = text.find("\x00")
        if idx >= 0:
            text = text[:idx]
        return text.strip()
    except Exception:
        return ""


def parse_stfs_header(data: bytes) -> Optional[StfsHeader]:
    """Parse an STFS header from the first 0x1000 bytes of a file."""
    if len(data) < BLOCK_SIZE:
        return None
    if data[0:4] not in VALID_MAGICS:
        return None
    try:
        h = StfsHeader()
        h.magic = data[0:4]

        # --- Metadata section (authoritative) ---
        h.content_type = struct.unpack(">I", data[OFF_CONTENT_TYPE:OFF_CONTENT_TYPE + 4])[0]
        h.metadata_version = struct.unpack(">I", data[OFF_METADATA_VERSION:OFF_METADATA_VERSION + 4])[0]
        h.content_size = struct.unpack(">Q", data[OFF_CONTENT_SIZE:OFF_CONTENT_SIZE + 8])[0]
        h.media_id = struct.unpack(">I", data[OFF_MEDIA_ID:OFF_MEDIA_ID + 4])[0]
        h.version = struct.unpack(">I", data[OFF_VERSION:OFF_VERSION + 4])[0]
        h.base_version = struct.unpack(">I", data[OFF_BASE_VERSION:OFF_BASE_VERSION + 4])[0]
        h.title_id = struct.unpack(">I", data[OFF_TITLE_ID:OFF_TITLE_ID + 4])[0]
        h.platform = data[OFF_PLATFORM]
        h.executable_type = data[OFF_EXECUTABLE_TYPE]
        h.disc_number = data[OFF_DISC_NUMBER]
        h.disc_in_set = data[OFF_DISC_IN_SET]
        h.save_game_id = struct.unpack(">I", data[OFF_SAVE_GAME_ID:OFF_SAVE_GAME_ID + 4])[0]
        h.console_id = data[OFF_CONSOLE_ID:OFF_CONSOLE_ID + 5]
        h.profile_id = data[OFF_PROFILE_ID:OFF_PROFILE_ID + 8]

        # Descriptor type: 0 = STFS, 1 = SVOD
        h.descriptor_type = struct.unpack(">I", data[OFF_DESCRIPTOR_TYPE:OFF_DESCRIPTOR_TYPE + 4])[0]

        # Volume descriptor block count
        if OFF_VOLUME_DESCRIPTOR + 0x24 <= len(data):
            vd = data[OFF_VOLUME_DESCRIPTOR:OFF_VOLUME_DESCRIPTOR + 0x24]
            h.volume_type = struct.unpack(">H", vd[0x00:0x02])[0]
            h.block_count = struct.unpack(">H", vd[0x22:0x24])[0]

        # Display strings
        h.display_name = _read_utf16be(data, OFF_DISPLAY_NAME, 0x80)
        h.display_description = _read_utf16be(data, OFF_DISPLAY_DESCRIPTION, 0x80)
        h.publisher_name = _read_utf16be(data, OFF_PUBLISHER_NAME, 0x40)
        h.title_name = _read_utf16be(data, OFF_TITLE_NAME, 0x40)

        # Fallback to legacy offsets if primary strings are empty
        if not h.display_name:
            h.display_name = _read_utf16be(data, OFF_LEGACY_DISPLAY_NAME, 0x80)
        if not h.display_description:
            h.display_description = _read_utf16be(data, OFF_LEGACY_DISPLAY_DESC, 0x80)
        if not h.publisher_name:
            h.publisher_name = _read_utf16be(data, OFF_LEGACY_PUBLISHER, 0x40)
        if not h.title_name:
            h.title_name = _read_utf16be(data, OFF_LEGACY_TITLE, 0x40)

        return h
    except Exception:
        return None


def is_stfs_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) in VALID_MAGICS
    except Exception:
        return False


def read_stfs_header(path: str) -> Optional[StfsHeader]:
    try:
        with open(path, "rb") as f:
            return parse_stfs_header(f.read(BLOCK_SIZE))
    except Exception:
        return None


def is_unlocked(path: str) -> bool:
    """Return True if the entire license block is already zeroed."""
    try:
        with open(path, "rb") as f:
            f.seek(LICENSE_BLOCK_OFFSET)
            block = f.read(LICENSE_BLOCK_SIZE)
        if len(block) < LICENSE_BLOCK_SIZE:
            return False
        return all(b == 0 for b in block)
    except Exception:
        return False


def unlock_stfs(path: str, output_path: Optional[str] = None) -> bool:
    """Unlock an STFS package by zeroing the entire 0x100-byte license block.

    Because the STFS hash tree lives in block 1, modifying block 0 does not
    invalidate any stored hashes.
    """
    try:
        with open(path, "rb") as f:
            data = bytearray(f.read())
    except Exception:
        return False

    if len(data) < BLOCK_SIZE or data[0:4] not in VALID_MAGICS:
        return False

    # Already unlocked? Just ensure output exists.
    if all(b == 0 for b in data[LICENSE_BLOCK_OFFSET:LICENSE_BLOCK_OFFSET + LICENSE_BLOCK_SIZE]):
        if output_path and output_path != path:
            try:
                with open(output_path, "wb") as f:
                    f.write(data)
            except Exception:
                return False
        return True

    data[LICENSE_BLOCK_OFFSET:LICENSE_BLOCK_OFFSET + LICENSE_BLOCK_SIZE] = b"\x00" * LICENSE_BLOCK_SIZE

    out = output_path or path
    try:
        with open(out, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False
