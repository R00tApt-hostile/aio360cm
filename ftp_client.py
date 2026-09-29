"""FTP client for uploading content to an Xbox 360 running Aurora / FSD."""

import ftplib
import os
from typing import Callable, Optional

from stfs import (
    CONTENT_TYPE_XBLA,
    CONTENT_TYPE_TITLE_UPDATE,
    CONTENT_TYPE_SAVE,
    CONTENT_TYPE_AVATAR,
    CONTENT_TYPE_GOD,
    CONTENT_TYPE_MARKETPLACE,
)


class XboxFTPClient:
    def __init__(self):
        self.ftp: Optional[ftplib.FTP] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, host: str, port: int = 21, user: str = "xboxftp",
                password: str = "xboxftp", timeout: int = 30) -> bool:
        try:
            self.ftp = ftplib.FTP()
            self.ftp.connect(host, port, timeout=timeout)
            self.ftp.login(user, password)
            self.ftp.set_pasv(True)
            return True
        except Exception as e:
            print(f"[ftp] connect error: {e}")
            self.ftp = None
            return False

    def disconnect(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.ftp = None

    def is_connected(self) -> bool:
        if not self.ftp:
            return False
        try:
            self.ftp.voidcmd("NOOP")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def ensure_dir(self, remote_path: str):
        """Create a remote directory tree, ignoring errors if it exists."""
        if not self.ftp:
            return
        parts = remote_path.replace("\\", "/").strip("/").split("/")
        current = ""
        for part in parts:
            if not part:
                continue
            current = f"{current}/{part}" if current else part
            try:
                self.ftp.mkd(current)
            except ftplib.error_perm:
                pass  # Already exists

    def list_dir(self, remote_path: str) -> list:
        """List entries in a remote directory. Returns [] on error."""
        if not self.ftp:
            return []
        try:
            return self.ftp.nlst(remote_path)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Upload helpers
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_path: str,
                    progress: Optional[Callable[[int, int], None]] = None) -> bool:
        """Upload a single file, creating remote dirs as needed."""
        if not self.ftp:
            return False
        try:
            remote_dir = os.path.dirname(remote_path).replace("\\", "/")
            if remote_dir:
                self.ensure_dir(remote_dir)
            size = os.path.getsize(local_path)
            sent = [0]

            def cb(chunk):
                sent[0] += len(chunk)
                if progress:
                    progress(sent[0], size)

            with open(local_path, "rb") as f:
                self.ftp.storbinary(
                    f"STOR {remote_path}", f,
                    blocksize=65536, callback=cb,
                )
            return True
        except Exception as e:
            print(f"[ftp] upload error ({local_path}): {e}")
            return False

    def upload_folder(self, local_folder: str, remote_folder: str,
                      file_progress: Optional[Callable[[str, int, int], None]] = None
                      ) -> bool:
        """Recursively upload a local folder to a remote folder."""
        if not self.ftp:
            return False
        try:
            files = []
            for root, _dirs, names in os.walk(local_folder):
                for n in names:
                    local = os.path.join(root, n)
                    rel = os.path.relpath(local, local_folder)
                    remote = remote_folder.rstrip("/") + "/" + rel.replace("\\", "/")
                    files.append((local, remote))
            total = len(files)
            for idx, (local, remote) in enumerate(files):
                if file_progress:
                    file_progress(remote, idx + 1, total)
                if not self.upload_file(local, remote):
                    return False
            return True
        except Exception as e:
            print(f"[ftp] folder upload error: {e}")
            return False


def build_remote_path(header, kind: str) -> str:
    """Build the standard on-console destination path for a package.

    All paths are relative to the console's root FTP directory.
    """
    base = "Content/0000000000000000"
    title = header.title_id_hex
    ct = header.content_type & 0x0FFFFFFF

    if kind == "god" or ct == CONTENT_TYPE_GOD:
        # GOD games live under 00007000
        return f"{base}/{title}/00007000"

    if ct == CONTENT_TYPE_XBLA:
        return f"{base}/{title}/00000004"

    if ct in (CONTENT_TYPE_MARKETPLACE, 0x10, 0x11, 0x12):
        # DLC / add-ons live under their content-type subfolder
        return f"{base}/{title}/{ct:08X}/{header.media_id_hex}"

    if ct == CONTENT_TYPE_TITLE_UPDATE:
        # Title updates go in 000B0000
        return f"{base}/{title}/000B0000"

    if ct == CONTENT_TYPE_SAVE:
        return f"{base}/{title}/00000001"

    if ct == CONTENT_TYPE_AVATAR:
        return f"{base}/{title}/00009000"

    # Fallback: use the content-type value as the folder name
    return f"{base}/{title}/{ct:08X}"
