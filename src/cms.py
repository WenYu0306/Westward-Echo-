"""CMS abstraction layer — reference connectors for pulling novels and pushing translations.

Keep it simple: these are reference implementations for development and demo,
not production integrations. Extend by adding new Connector subclasses.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import httpx


# ═══════════════════════════════════════════════════════════════
# Abstract interface
# ═══════════════════════════════════════════════════════════════

class CMSConnector(ABC):
    """Abstract interface for connecting to content management / copyright systems."""

    @abstractmethod
    def pull_novel(self, source_id: str) -> str:
        """Pull a novel's full text from the CMS. Returns the raw txt content.

        Args:
            source_id: An identifier meaningful to the connector (filename, CMS id, URL path).
        """

    @abstractmethod
    def push_translation(self, job_id: str, platform: str, /) -> dict:
        """Push a completed translation to a publishing platform.

        Args:
            job_id: The Westward Echo job identifier.
            platform: Target platform label (e.g. ``"web"``, ``"kindle"``).

        Returns:
            ``{"url": str, "status": str, "platform_message": str}``
        """

    def list_sources(self) -> list[str]:
        """List available source identifiers. Optional — default returns empty list."""
        return []


# ═══════════════════════════════════════════════════════════════
# File-system connector (dev / demo)
# ═══════════════════════════════════════════════════════════════

class FileSystemConnector(CMSConnector):
    """Read novels from a local directory. Useful for development and demo."""

    def __init__(self, base_dir: str = "./novels"):
        self.base_dir = Path(base_dir).resolve()

    def pull_novel(self, source_id: str) -> str:
        path = self.base_dir / f"{source_id}.txt"
        if not path.exists():
            raise FileNotFoundError(f"Novel source not found: {path}")
        return path.read_text(encoding="utf-8")

    def push_translation(self, job_id: str, platform: str, /) -> dict:
        output_dir = self.base_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy the Markdown output if it exists
        from .config import OUTPUT_DIR as _out

        md_path = _out / f"{job_id}_full_novel_en-US.md"
        if md_path.exists():
            dest = output_dir / f"{job_id}.md"
            shutil.copy2(md_path, dest)

        return {
            "url": str(output_dir / f"{job_id}.md"),
            "status": "published",
            "platform_message": f"Saved to {output_dir}",
        }

    def list_sources(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        return sorted(
            [p.stem for p in self.base_dir.glob("*.txt") if p.is_file()]
        )


# ═══════════════════════════════════════════════════════════════
# Webhook connector (HTTP-based)
# ═══════════════════════════════════════════════════════════════

class WebhookConnector(CMSConnector):
    """Push / pull translations via HTTP to a webhook URL."""

    def __init__(self, webhook_url: str, api_key: str = ""):
        self.webhook_url = webhook_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self._client = httpx.Client(headers=headers, timeout=30)
        return self._client

    def pull_novel(self, source_id: str) -> str:
        url = f"{self.webhook_url}/novels/{source_id}"
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp.text

    def push_translation(self, job_id: str, platform: str, /) -> dict:
        url = f"{self.webhook_url}/translations/{job_id}"

        # Attach the Markdown body
        from .config import OUTPUT_DIR as _out

        md_path = _out / f"{job_id}_full_novel_en-US.md"
        body = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

        try:
            resp = self.client.post(url, json={
                "job_id": job_id,
                "platform": platform,
                "content": body,
            })
            resp.raise_for_status()
            return {
                "url": url,
                "status": "published",
                "platform_message": resp.text[:200],
            }
        except httpx.HTTPError as exc:
            return {
                "url": url,
                "status": "failed",
                "platform_message": str(exc),
            }


# ═══════════════════════════════════════════════════════════════
# Factory — picks the right connector from config
# ═══════════════════════════════════════════════════════════════

def get_connector() -> CMSConnector:
    """Return the CMS connector configured via environment variables."""
    from .config import (
        CMS_SOURCE_TYPE,
        CMS_FILE_BASE_DIR,
        CMS_WEBHOOK_URL,
        CMS_WEBHOOK_API_KEY,
    )

    if CMS_SOURCE_TYPE == "file":
        return FileSystemConnector(base_dir=CMS_FILE_BASE_DIR)

    if CMS_SOURCE_TYPE == "webhook":
        return WebhookConnector(
            webhook_url=CMS_WEBHOOK_URL,
            api_key=CMS_WEBHOOK_API_KEY,
        )

    raise ValueError(
        f"Unknown CMS_SOURCE_TYPE '{CMS_SOURCE_TYPE}'. "
        "Expected 'file' or 'webhook'."
    )
