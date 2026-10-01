"""Hashing and verification utilities."""

import hashlib
import os
from typing import Callable, Optional


def hash_file(path: str, algo: str = "md5",
              progress: Optional[Callable[[int, int], None]] = None) -> Optional[str]:
    """Compute a hex digest of a file. Streams in 1 MB chunks."""
    try:
        h = hashlib.new(algo)
        size = os.path.getsize(path)
        done = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, size)
        return h.hexdigest()
    except Exception:
        return None


def verify_file(path: str, expected_hash: str, algo: str = "md5") -> bool:
    got = hash_file(path, algo)
    return got is not None and got.lower() == expected_hash.lower()


def hash_bytes(data: bytes, algo: str = "md5") -> str:
    h = hashlib.new(algo)
    h.update(data)
    return h.hexdigest()
