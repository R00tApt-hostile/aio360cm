"""XTAF / FATX filesystem reader for Xbox 360 storage devices.

Supports reading USB drives and (with a manual offset) internal HDD
partitions. Read-only for safety — no accidental writes to your drive.

Reference: the XTAF layout used by the Xbox 360 is a variant of FATX.
    Sector size: 512 bytes
    Cluster size: 0x4000 (16 KiB) on USB drives, 0x1000 on some HDDs
    Directory entry: 0x40 bytes
"""

import os
import struct
from dataclasses import dataclass
from typing import List, Optional


SECTOR_SIZE = 512
DEFAULT_CLUSTER_SIZE = 0x4000

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_DIRECTORY = 0x10
ATTR_DELETED = 0x80

FATX_MAGICS = (b"XTAF", b"FATX", b"XTAF")


@dataclass
class FatxEntry:
    name: str
    attributes: int
    first_cluster: int
    size: int
    parent_path: str

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & ATTR_DIRECTORY)

    @property
    def is_deleted(self) -> bool:
        return bool(self.attributes & ATTR_DELETED)

    def __repr__(self):
        kind = "DIR " if self.is_directory else "FILE"
        return f"<{kind} {self.name} ({self.size} bytes)>"


class FatxError(Exception):
    pass


class FatxVolume:
    """Read-only FATX/XTAF filesystem reader.

    `offset` is where the partition/filesystem begins on the device.
    For USB drives, that is usually 0. For internal HDDs, you'll need to
    specify the partition offset (typically a multiple of 512).
    """

    def __init__(self, path: str, offset: int = 0,
                 cluster_size: Optional[int] = None):
        self.path = path
        self.offset = offset
        self._fh = None
        self.cluster_size = cluster_size or DEFAULT_CLUSTER_SIZE
        self.fat_offset = 0
        self.root_cluster = 1
        self.total_clusters = 0
        self._fat_cache = None

    # ------------------------------------------------------------------
    # Open / close
    # ------------------------------------------------------------------

    def open(self):
        try:
            self._fh = open(self.path, "rb")
        except OSError as e:
            raise FatxError(f"Cannot open {self.path}: {e}")

        magic = self._read_at(0, 4)
        if magic not in FATX_MAGICS:
            # Try alternative offsets used by some partitions
            for alt in (0x1000, 0x2000, 0x10000, 0x80000):
                self._fh.seek(alt)
                if self._fh.read(4) in FATX_MAGICS:
                    self.offset = alt
                    magic = self._read_at(0, 4)
                    break
            else:
                raise FatxError(
                    f"Not a FATX volume (magic={magic!r}). "
                    "Specify the correct --offset if this is an HDD partition.")

        # Superblock
        spc = struct.unpack(">I", self._read_at(0x08, 4))[0]
        if spc and 1 <= spc <= 0x10000:
            self.cluster_size = spc * SECTOR_SIZE
        self.root_cluster = struct.unpack(">I", self._read_at(0x0C, 4))[0] or 1

        # FAT table is at sector 1 (i.e. right after the superblock)
        self.fat_offset = SECTOR_SIZE
        # Rough cluster count for bounds checks
        self.total_clusters = max(1, (self._device_size() - self.fat_offset) // self.cluster_size)

    def close(self):
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------------
    # Low-level IO
    # ------------------------------------------------------------------

    def _device_size(self) -> int:
        if not self._fh:
            return 0
        try:
            return os.fstat(self._fh.fileno()).st_size
        except Exception:
            return 0

    def _read_at(self, rel_offset: int, size: int) -> bytes:
        if not self._fh:
            return b""
        try:
            self._fh.seek(self.offset + rel_offset)
            return self._fh.read(size)
        except Exception:
            return b""

    def _cluster_offset(self, cluster: int) -> int:
        # Cluster data area begins after the FAT. The first data cluster
        # in XTAF is cluster 1 (cluster 0 is reserved).
        # FAT table size in bytes: total_clusters * 4, rounded up to cluster.
        fat_bytes = self.total_clusters * 4
        fat_clusters = (fat_bytes + self.cluster_size - 1) // self.cluster_size
        data_start = (1 + fat_clusters) * self.cluster_size
        return data_start + (cluster - 1) * self.cluster_size

    # ------------------------------------------------------------------
    # Directory parsing
    # ------------------------------------------------------------------

    def _parse_entry(self, raw: bytes, parent_path: str) -> Optional[FatxEntry]:
        if len(raw) < 0x40:
            return None
        name_len = raw[0]
        if name_len == 0 or name_len == 0xFF:
            return None  # end of directory or free slot
        if name_len > 42:
            return None
        attributes = raw[1]
        try:
            name = raw[2:2 + name_len].decode("ascii", errors="replace")
        except Exception:
            return None
        first_cluster = struct.unpack(">I", raw[0x2C:0x30])[0]
        size = struct.unpack(">I", raw[0x30:0x34])[0]
        return FatxEntry(name, attributes, first_cluster, size, parent_path)

    def list_dir(self, cluster: Optional[int] = None) -> List[FatxEntry]:
        """List entries in a directory cluster (default: root)."""
        if cluster is None:
            cluster = self.root_cluster
        entries: List[FatxEntry] = []
        visited = set()
        current = cluster
        parent_path = "/" if cluster == self.root_cluster else "?"

        while current and current not in visited:
            visited.add(current)
            if current < 1 or current > self.total_clusters:
                break
            base = self._cluster_offset(current)
            for slot in range(self.cluster_size // 0x40):
                raw = self._read_at(base + slot * 0x40, 0x40)
                entry = self._parse_entry(raw, parent_path)
                if entry is None:
                    if len(raw) >= 1 and raw[0] == 0xFF:
                        return entries
                    continue
                if entry.is_deleted:
                    continue
                entries.append(entry)
            # Follow FAT chain
            current = self._next_cluster(current)
        return entries

    def walk(self, cluster: Optional[int] = None, path: str = "/"):
        """Yield (path, entry) for every file in the tree."""
        entries = self.list_dir(cluster)
        for e in entries:
            full = (path.rstrip("/") + "/" + e.name) if path != "/" else "/" + e.name
            if e.is_directory:
                yield full, e
                yield from self.walk(e.first_cluster, full)
            else:
                yield full, e

    def read_file(self, entry: FatxEntry) -> bytes:
        """Read the full contents of a file entry."""
        if entry.is_directory:
            return b""
        out = bytearray()
        current = entry.first_cluster
        visited = set()
        remaining = entry.size
        while current and current not in visited and remaining > 0:
            visited.add(current)
            base = self._cluster_offset(current)
            chunk = self._read_at(base, min(self.cluster_size, remaining))
            if not chunk:
                break
            out.extend(chunk)
            remaining -= len(chunk)
            if remaining <= 0:
                break
            current = self._next_cluster(current)
        return bytes(out)

    def read_file_to(self, entry: FatxEntry, out_path: str,
                     progress=None) -> bool:
        """Stream a file entry to a local path."""
        if entry.is_directory:
            return False
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        current = entry.first_cluster
        visited = set()
        remaining = entry.size
        done = 0
        try:
            with open(out_path, "wb") as f:
                while current and current not in visited and remaining > 0:
                    visited.add(current)
                    base = self._cluster_offset(current)
                    chunk = self._read_at(base, min(self.cluster_size, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, entry.size)
                    current = self._next_cluster(current)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # FAT
    # ------------------------------------------------------------------

    def _load_fat(self):
        if self._fat_cache is not None:
            return
        fat_bytes = self.total_clusters * 4
        raw = self._read_at(self.fat_offset, fat_bytes)
        self._fat_cache = []
        for i in range(0, len(raw), 4):
            self._fat_cache.append(struct.unpack(">I", raw[i:i + 4])[0])

    def _next_cluster(self, cluster: int) -> int:
        self._load_fat()
        if not self._fat_cache or cluster >= len(self._fat_cache):
            return 0
        nxt = self._fat_cache[cluster]
        if nxt == 0 or nxt == 0xFFFFFFFF or nxt >= self.total_clusters:
            return 0
        return nxt


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def is_fatx_volume(path: str, offset: int = 0) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(4) in FATX_MAGICS
    except Exception:
        return False


def find_content_files(volume: FatxVolume, prefix: str = "/Content/"):
    """Yield (path, entry) for STFS-like files under a prefix."""
    from stfs import is_stfs_file  # local import to avoid circular dep
    for path, entry in volume.walk():
        if entry.is_directory:
            continue
        if not path.startswith(prefix):
            continue
        yield path, entry
