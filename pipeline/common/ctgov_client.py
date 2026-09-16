"""ClinicalTrials.gov API v2 client: paging, throttling, retries, raw cache.

Field paths and enum vocabularies used by the pipeline were verified against the
live API; see CLAUDE.md for the confirmed response shape.

Raw pages are cached to disk as gzipped JSONL (one study record per line) so
reruns and tests never refetch.  A companion ``.meta.json`` is written only
after a run completes, so an interrupted run leaves a cache that is recognised
as unusable rather than silently treated as the whole registry.
"""

from __future__ import annotations

import gzip
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Iterator

import requests

API_URL = "https://clinicaltrials.gov/api/v2/studies"

USER_AGENT = (
    "stats.clinbolt.com research pipeline "
    "(+https://stats.clinbolt.com; contact: contactdelano@gmail.com)"
)

# Confirmed present on the live API.
DEFAULT_FIELDS = [
    "protocolSection.identificationModule.nctId",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.statusModule.startDateStruct.date",
    "protocolSection.statusModule.lastUpdatePostDateStruct.date",
    "protocolSection.designModule.enrollmentInfo.count",
    "protocolSection.designModule.enrollmentInfo.type",
    "protocolSection.designModule.studyType",
    "protocolSection.designModule.phases",
]

RETRY_STATUSES = {429, 500, 502, 503, 504}


class CtgovError(RuntimeError):
    """Raised when the API cannot be read after repeated attempts."""


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class CtgovClient:
    def __init__(
        self,
        cache_dir: Path,
        page_size: int = 1000,
        throttle_seconds: float = 1.0,
        max_retries: int = 5,
        timeout: int = 60,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.page_size = page_size
        self.throttle_seconds = throttle_seconds
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request_at = 0.0
        self.total_count: int | None = None

    # -- cache paths ---------------------------------------------------------
    def _cache_paths(self, limit_pages: int | None) -> tuple[Path, Path]:
        # Limited runs get their own cache file so a 2-page sample can never be
        # mistaken for a full pull.
        stem = "studies" if limit_pages is None else "studies_limit{0}".format(limit_pages)
        return (
            self.cache_dir / (stem + ".jsonl.gz"),
            self.cache_dir / (stem + ".meta.json"),
        )

    # -- HTTP ----------------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.throttle_seconds:
            time.sleep(self.throttle_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, params: dict) -> dict:
        last_error = "unknown error"
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                response = self.session.get(API_URL, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = "network error: {0}".format(exc)
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        last_error = "malformed JSON: {0}".format(exc)
                elif response.status_code in RETRY_STATUSES:
                    last_error = "HTTP {0}".format(response.status_code)
                else:
                    # 4xx other than 429 will not improve on retry.
                    raise CtgovError(
                        "HTTP {0} from API: {1}".format(
                            response.status_code, response.text[:300]
                        )
                    )
            backoff = (2 ** attempt) + random.uniform(0, 0.5)
            _log(
                "  retry {0}/{1} after {2}; sleeping {3:.1f}s".format(
                    attempt + 1, self.max_retries, last_error, backoff
                )
            )
            time.sleep(backoff)
        raise CtgovError(
            "giving up after {0} attempts: {1}".format(self.max_retries, last_error)
        )

    # -- public --------------------------------------------------------------
    def iter_studies(
        self,
        fields: list[str] | None = None,
        limit_pages: int | None = None,
        refresh: bool = False,
    ) -> Iterator[dict]:
        """Yield study records, from cache when available, else from the API."""
        fields = fields or DEFAULT_FIELDS
        cache_file, meta_file = self._cache_paths(limit_pages)

        if not refresh and cache_file.exists() and meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            _log(
                "Using cached raw data: {0} ({1} records, fetched {2})".format(
                    cache_file.name, meta.get("records", "?"), meta.get("fetched_at", "?")
                )
            )
            self.total_count = meta.get("total_count")
            with gzip.open(cache_file, "rt", encoding="utf-8") as handle:
                for line in handle:
                    yield json.loads(line)
            return

        for study in self._fetch_to_cache(fields, limit_pages, cache_file, meta_file):
            yield study

    def _fetch_to_cache(
        self,
        fields: list[str],
        limit_pages: int | None,
        cache_file: Path,
        meta_file: Path,
    ) -> Iterator[dict]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename on success, so an interrupted fetch
        # never leaves a half cache behind under the real name.
        temp_file = cache_file.with_suffix(".partial")
        if meta_file.exists():
            meta_file.unlink()

        page_token = None
        page = 0
        records = 0
        started = time.monotonic()

        try:
            with gzip.open(temp_file, "wt", encoding="utf-8") as handle:
                while True:
                    params = {
                        "pageSize": self.page_size,
                        "fields": ",".join(fields),
                    }
                    if page_token:
                        params["pageToken"] = page_token
                    else:
                        params["countTotal"] = "true"

                    payload = self._get(params)
                    page += 1

                    if self.total_count is None:
                        self.total_count = payload.get("totalCount")
                        if self.total_count:
                            pages_expected = -(-self.total_count // self.page_size)
                            _log(
                                "Registry reports {0:,} studies (~{1} pages)".format(
                                    self.total_count, pages_expected
                                )
                            )

                    studies = payload.get("studies", [])
                    for study in studies:
                        handle.write(json.dumps(study, separators=(",", ":")) + "\n")
                        records += 1
                        yield study

                    if self.total_count:
                        pct = 100.0 * records / self.total_count
                        _log("  page {0}: {1:,} records ({2:.1f}%)".format(page, records, pct))
                    else:
                        _log("  page {0}: {1:,} records".format(page, records))

                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        break
                    if limit_pages is not None and page >= limit_pages:
                        _log("Stopping early at --limit-pages {0}".format(limit_pages))
                        break
        except BaseException:
            temp_file.unlink(missing_ok=True)
            raise

        os.replace(temp_file, cache_file)
        meta_file.write_text(
            json.dumps(
                {
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "records": records,
                    "pages": page,
                    "total_count": self.total_count,
                    "limit_pages": limit_pages,
                    "fields": fields,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        _log(
            "Fetched {0:,} records in {1} pages ({2:.0f}s) -> {3}".format(
                records, page, time.monotonic() - started, cache_file.name
            )
        )
