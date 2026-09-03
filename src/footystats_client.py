"""Thin wrapper for the FootyStats API (https://api.football-data-api.com/).

Only the pieces the daily sync needs. The API key is read from the
FOOTYSTATS_API_KEY environment variable.

Docs: https://footystats.org/api/documentations/
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

BASE_URL = "https://api.football-data-api.com/"
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


class FootyStatsError(RuntimeError):
    """Raised when the FootyStats API returns an error or an unexpected shape."""


class FootyStatsClient:
    def __init__(self, api_key: str | None = None, *, session: requests.Session | None = None):
        self.api_key = api_key or os.environ.get("FOOTYSTATS_API_KEY", "")
        if not self.api_key:
            raise FootyStatsError(
                "No API key. Pass api_key=... or set FOOTYSTATS_API_KEY."
            )
        self.session = session or requests.Session()

    # -- low level ---------------------------------------------------------

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = BASE_URL + endpoint.lstrip("/")
        merged = {"key": self.api_key, **params}
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=merged, timeout=DEFAULT_TIMEOUT)
            except requests.RequestException as exc:  # network hiccup
                last_exc = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
                raise FootyStatsError(f"GET {endpoint} failed: {exc}") from exc

            if resp.status_code == 429 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            if resp.status_code != 200:
                raise FootyStatsError(
                    f"GET {endpoint} -> HTTP {resp.status_code}: {resp.text[:400]}"
                )

            try:
                payload = resp.json()
            except ValueError as exc:
                raise FootyStatsError(
                    f"GET {endpoint} returned non-JSON: {resp.text[:400]}"
                ) from exc

            if not payload.get("success", False):
                raise FootyStatsError(
                    f"GET {endpoint} success=false: {str(payload)[:400]}"
                )
            return payload

        raise FootyStatsError(f"GET {endpoint} exhausted retries: {last_exc}")

    # -- endpoints -------------------------------------------------------

    def get_league_matches(self, season_id: int) -> list[dict[str, Any]]:
        """Return every match object for a FootyStats season_id.

        Handles pagination (the endpoint returns up to 600 rows per page).
        """
        page = 1
        out: list[dict[str, Any]] = []
        while True:
            payload = self._get(
                "league-matches", {"season_id": season_id, "page": page}
            )
            data = payload.get("data") or []
            out.extend(data)

            pager = payload.get("pager") or {}
            max_page = int(pager.get("max_page", 1) or 1)
            if page >= max_page:
                break
            page += 1
        return out


def get_league_matches(season_id: int, api_key: str | None = None) -> list[dict[str, Any]]:
    """Convenience wrapper: fetch all matches for one season_id."""
    return FootyStatsClient(api_key).get_league_matches(season_id)
