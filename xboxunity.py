"""XboxUnity API client for searching and downloading Title Updates (TUs).

The XboxUnity API (https://xboxunity.net) is the community-run database
used by Aurora, FSD, and tools like X360 TU Manager. This client provides
a minimal interface for querying available TUs by MediaID or TitleID.
"""

import json
import os
from typing import List, Optional

import requests

XBOXUNITY_API = "https://xboxunity.net/Resources/Lib/TitleUpdate.php"

# Some common user agents that XboxUnity accepts
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Xbox360Manager/2.0"


class TitleUpdateInfo:
    """Represents a single Title Update returned by XboxUnity."""

    def __init__(self, data: dict):
        self.title_id = data.get("TitleID", "")
        self.media_id = data.get("MediaID", "")
        self.version = data.get("Version", 0)
        self.name = data.get("Name", "")
        self.size = data.get("Size", 0)
        self.upload_date = data.get("UploadDate", "")
        self.download_url = data.get("Download", "")
        self.raw = data

    def __repr__(self):
        return (f"<TU {self.title_id}/{self.media_id} "
                f"v{self.version} ({self.size} bytes)>")


class XboxUnityClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": DEFAULT_UA})

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_title_updates(self, media_id: str) -> List[TitleUpdateInfo]:
        """Return all TUs matching the given MediaID."""
        params = {"media_id": media_id.upper()}
        if self.api_key:
            params["api_key"] = self.api_key
        try:
            r = self.session.get(XBOXUNITY_API, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[xboxunity] query error: {e}")
            return []

        # The API returns either a list or a dict with a "results" key
        if isinstance(data, dict):
            data = data.get("results", [])
        if not isinstance(data, list):
            return []
        return [TitleUpdateInfo(item) for item in data]

    def get_latest_title_update(self, media_id: str) -> Optional[TitleUpdateInfo]:
        """Return the highest-version TU for the given MediaID."""
        tus = self.get_title_updates(media_id)
        if not tus:
            return None
        tus.sort(key=lambda t: t.version, reverse=True)
        return tus[0]

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_title_update(self, tu: TitleUpdateInfo, output_dir: str,
                              progress: Optional[callable] = None) -> Optional[str]:
        """Download a TU to output_dir. Returns the local path or None."""
        if not tu.download_url:
            print("[xboxunity] no download URL for TU")
            return None

        os.makedirs(output_dir, exist_ok=True)
        filename = f"tu{tu.title_id}_{tu.version:08X}.bin"
        local_path = os.path.join(output_dir, filename)

        try:
            r = self.session.get(tu.download_url, stream=True, timeout=120)
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress and total:
                            progress(downloaded, total)
            return local_path
        except Exception as e:
            print(f"[xboxunity] download error: {e}")
            return None
