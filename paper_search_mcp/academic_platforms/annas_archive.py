"""Anna's Archive downloader integration."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class AnnasArchiveFetcher:
    """Best-effort PDF downloader via Anna's Archive search and file pages."""

    def __init__(
        self, 
        base_url: str = "https://annas-archive.org", 
        output_dir: str = "./downloads", 
        timeout: int = 30
    ):
        self.base_url = base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

        self.client = httpx.Client(timeout=self.timeout, follow_redirects=True)
        self.client.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def download_pdf(self, identifier: str, save_path: Optional[str] = None) -> Optional[str]:
        """
        Attempts to find and download a PDF using a DOI or search identifier.
        If save_path is omitted, it will automatically generate a secure filename.
        """
        identifier = (identifier or "").strip()
        if not identifier:
            return None

        if identifier.lower().startswith(("http://", "https://")) and identifier.lower().endswith(".pdf"):
            return self._download_url(identifier, identifier, save_path)

        file_page_url = self._find_file_page(identifier)
        if not file_page_url:
            return None

        pdf_url = self._extract_pdf_url(file_page_url)
        if not pdf_url:
            logger.warning(f"Anna's Archive: MD5 page found, but unable to extract a direct download link for {identifier}")
            return None

        return self._download_url(pdf_url, identifier, save_path)

    def _find_file_page(self, identifier: str) -> str:
        search_url = f"{self.base_url}/search?q={quote_plus(identifier)}"
        try:
            response = self.client.get(search_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Anna's Archive search failed for '%s': %s", identifier, exc)
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if re.search(r"/md5/[0-9a-fA-F]{32}", href):
                return urljoin(self.base_url, href)
        return ""

    def _extract_pdf_url(self, file_page_url: str) -> str:
        try:
            response = self.client.get(file_page_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Anna's Archive file page request failed '%s': %s", file_page_url, exc)
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            lowered = href.lower()
            if "torrent" in lowered:
                continue

            if ".pdf" in lowered or "download" in lowered:
                return urljoin(self.base_url, href)
        return ""

    def _download_url(self, url: str, identifier: str, save_path: Optional[str] = None) -> Optional[str]:
        try:
            # We override the default timeout to 60s for the actual file stream download
            with self.client.stream("GET", url, timeout=60) as response:
                response.raise_for_status()
                
                content_type = (response.headers.get("content-type") or "").lower()
                iterator = response.iter_bytes(chunk_size=1024)
                
                try:
                    first_chunk = next(iterator)
                except StopIteration:
                    first_chunk = b""

                if "pdf" not in content_type and not first_chunk.startswith(b"%PDF"):
                    logger.warning("Anna's Archive URL does not look like a PDF: %s", url)
                    return None

                # Determine where to save it
                if save_path:
                    output_path = save_path
                else:
                    safe_hint = re.sub(r"[^a-zA-Z0-9._-]+", "_", identifier)[:80] or "paper"
                    digest = hashlib.sha256((url + identifier).encode("utf-8")).hexdigest()[:8]
                    output_path = os.path.join(self.output_dir, f"annas_archive_{safe_hint}_{digest}.pdf")
                
                # Write to disk
                with open(output_path, "wb") as fh:
                    if first_chunk:
                        fh.write(first_chunk)
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if chunk:
                            fh.write(chunk)
                            
                return output_path

        except httpx.HTTPError as exc:
            logger.error("Anna's Archive download failed '%s': %s", url, exc)
            return None
