"""SVOD (Secure Virtual Optical Drive) / GOD folder handling for Xbox 360.

A GOD game is a folder containing:
  - A header/manifest file (starts with an STFS magic: CON, LIVE, or PIRS)
  - One or more data fragments named Data0000, Data0001, ...

This module parses the SVOD header (which uses the same base layout as STFS
but with an SVOD descriptor) and provides unlock/upload helpers.
"""

import os
from typing import Optional

from stfs import (
    BLOCK_SIZE,
    VALID_MAGICS,
    StfsHeader,
    parse_stfs_header,
    LICENSE_BLOCK_OFFSET,
    LICENSE_BLOCK_SIZE,
)


def _read_magic(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(4)
    except Exception:
        return b""


def find_god_header(folder: str) -> Optional[str]:
    """Return the path to the SVOD header file inside a GOD folder.

    The header file is the only file in the folder that starts with an
    STFS magic (CON/LIVE/PIRS). Data fragments (Data0000, ...) do not.
    """
    if not os.path.isdir(folder):
        return None
    for entry in sorted(os.listdir(folder)):
        full = os.path.join(folder, entry)
        if os.path.isfile(full) and _read_magic(full) in VALID_MAGICS:
            return full
    return None


def is_god_folder(folder: str) -> bool:
    """Return True if the folder looks like a GOD (SVOD) game."""
    if not os.path.isdir(folder):
        return False
    has_data = False
    has_header = False
    for entry in os.listdir(folder):
        full = os.path.join(folder, entry)
        if not os.path.isfile(full):
            continue
        lower = entry.lower()
        if lower.startswith("data") and lower[4:].isdigit():
            has_data = True
        if _read_magic(full) in VALID_MAGICS:
            has_header = True
    return has_data and has_header


def read_god_header(folder: str) -> Optional[StfsHeader]:
    """Parse the SVOD header from a GOD folder."""
    header_path = find_god_header(folder)
    if not header_path:
        return None
    try:
        with open(header_path, "rb") as f:
            return parse_stfs_header(f.read(BLOCK_SIZE))
    except Exception:
        return None


def get_data_fragments(folder: str) -> list:
    """Return a sorted list of data fragment paths inside a GOD folder."""
    if not os.path.isdir(folder):
        return []
    fragments = []
    for entry in os.listdir(folder):
        full = os.path.join(folder, entry)
        if os.path.isfile(full):
            lower = entry.lower()
            if lower.startswith("data") and lower[4:].isdigit():
                fragments.append(full)
    fragments.sort(key=lambda p: int(os.path.basename(p)[4:]))
    return fragments


def is_god_unlocked(folder: str) -> bool:
    """Return True if the SVOD header's license block is already zeroed."""
    header_path = find_god_header(folder)
    if not header_path:
        return False
    try:
        with open(header_path, "rb") as f:
            f.seek(LICENSE_BLOCK_OFFSET)
            block = f.read(LICENSE_BLOCK_SIZE)
        if len(block) < LICENSE_BLOCK_SIZE:
            return False
        return all(b == 0 for b in block)
    except Exception:
        return False


def unlock_god(folder: str) -> bool:
    """Unlock a GOD-format game by zeroing the license block in its header."""
    header_path = find_god_header(folder)
    if not header_path:
        return False
    try:
        with open(header_path, "rb") as f:
            data = bytearray(f.read())
    except Exception:
        return False

    if len(data) < BLOCK_SIZE or data[0:4] not in VALID_MAGICS:
        return False

    data[LICENSE_BLOCK_OFFSET:LICENSE_BLOCK_OFFSET + LICENSE_BLOCK_SIZE] = b"\x00" * LICENSE_BLOCK_SIZE

    try:
        with open(header_path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False
