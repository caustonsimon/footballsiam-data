"""WordPress REST API client for footballsiam.com.

Auth: Application Passwords over HTTP Basic. Credentials come from the
environment: WP_APP_USERNAME and WP_APP_PASSWORD.

    from wp_client import WPClient
    wp = WPClient()                     # reads env
    idx = wp.index_matches_by_footystats_id()
    wp.create_match({...})

IMPORTANT - server-side requirement
-----------------------------------
This client reads and writes the custom fields (home_score, away_score, date,
match_week, season, footystats_match_id, home_team, away_team) through the REST
`meta` object. For that to work the site must register each of those meta keys
with `show_in_rest => true` and a write-capable `auth_callback` on the `matches`
post type. If a `register_meta(...)` call (or a plugin that does the same) is not
in place, `meta` comes back empty on reads and is silently dropped on writes.
See README.md > "WordPress REST prerequisites".
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterator

import requests
from requests.auth import HTTPBasicAuth

DEFAULT_BASE = "https://footballsiam.com/wp-json/"
CPT_REST_BASE = "wp/v2/match"      # the `matches` CPT registers rest_base "match"
CLUB_REST_BASE = "wp/v2/club"
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
PER_PAGE = 100


class WPError(RuntimeError):
    pass


class WPClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        app_password: str | None = None,
        *,
        dry_run: bool = False,
        session: requests.Session | None = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/") + "/"
        self.username = username or os.environ.get("WP_APP_USERNAME", "")
        self.app_password = app_password or os.environ.get("WP_APP_PASSWORD", "")
        if not self.username or not self.app_password:
            raise WPError(
                "Missing credentials. Set WP_APP_USERNAME and WP_APP_PASSWORD."
            )
        self.dry_run = dry_run
        self.session = session or requests.Session()
        self.auth = HTTPBasicAuth(self.username, self.app_password)
        self._club_title_cache: dict[int, str] = {}

    # -- low level -------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = self.base_url + path.lstrip("/")
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        kwargs.setdefault("auth", self.auth)
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
                raise WPError(f"{method} {path} failed: {exc}") from exc

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            return resp
        raise WPError(f"{method} {path} exhausted retries: {last_exc}")

    @staticmethod
    def _json_or_raise(resp: requests.Response, context: str) -> Any:
        if not resp.ok:
            raise WPError(f"{context} -> HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise WPError(f"{context} returned non-JSON: {resp.text[:300]}") from exc

    # -- reads --------------------------------------------------------

    def iter_all_matches(self) -> Iterator[dict[str, Any]]:
        """Yield every published `matches` post (id, title, meta)."""
        page = 1
        while True:
            resp = self._request(
                "GET",
                CPT_REST_BASE,
                params={
                    "per_page": PER_PAGE,
                    "page": page,
                    "status": "publish",
                    "_fields": "id,title,meta,link",
                    "orderby": "id",
                    "order": "asc",
                },
            )
            if resp.status_code == 400 and "rest_post_invalid_page_number" in resp.text:
                return
            batch = self._json_or_raise(resp, f"GET {CPT_REST_BASE} page {page}")
            if not batch:
                return
            for post in batch:
                yield post
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
            if page >= total_pages:
                return
            page += 1

    def index_matches_by_footystats_id(self) -> dict[int, dict[str, Any]]:
        """Map { footystats_match_id (int) -> match post } for all matches.

        Full scan of the CPT. Robust regardless of whether the server supports
        REST meta_query filtering. Posts without a numeric footystats_match_id
        meta are skipped.
        """
        index: dict[int, dict[str, Any]] = {}
        for post in self.iter_all_matches():
            raw = (post.get("meta") or {}).get("footystats_match_id")
            try:
                fs_id = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            index[fs_id] = post
        return index

    def get_club_title(self, club_id: int) -> str:
        """Rendered title of a `club` post, cached. Empty string if not found."""
        if club_id in self._club_title_cache:
            return self._club_title_cache[club_id]
        resp = self._request(
            "GET", f"{CLUB_REST_BASE}/{club_id}", params={"_fields": "id,title"}
        )
        if resp.status_code == 404:
            self._club_title_cache[club_id] = ""
            return ""
        data = self._json_or_raise(resp, f"GET {CLUB_REST_BASE}/{club_id}")
        title = (data.get("title") or {}).get("rendered", "").strip()
        self._club_title_cache[club_id] = title
        return title

    # -- writes ------------------------------------------------------

    def create_match(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a new `matches` post. Honours dry_run."""
        if self.dry_run:
            return {"_dry_run": True, "would_create": payload}
        resp = self._request("POST", CPT_REST_BASE, json=payload)
        return self._json_or_raise(resp, f"POST {CPT_REST_BASE}")

    def update_match_meta(self, post_id: int, meta: dict[str, Any]) -> dict[str, Any]:
        """PATCH meta on an existing `matches` post. Honours dry_run."""
        if self.dry_run:
            return {"_dry_run": True, "would_update": post_id, "meta": meta}
        resp = self._request("POST", f"{CPT_REST_BASE}/{post_id}", json={"meta": meta})
        return self._json_or_raise(resp, f"POST {CPT_REST_BASE}/{post_id}")

    # -- connectivity check ----------------------------------------

    def whoami(self) -> dict[str, Any]:
        """GET wp/v2/users/me - confirms the Application Password works."""
        resp = self._request("GET", "wp/v2/users/me", params={"_fields": "id,name,slug"})
        return self._json_or_raise(resp, "GET wp/v2/users/me")
