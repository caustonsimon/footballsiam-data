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
PLAYER_REST_BASE = "wp/v2/player"
HEAD_COACH_REST_BASE = "wp/v2/head_coach"  # Phase 3 - see Code Snippet 143
MEDIA_REST_BASE = "wp/v2/media"
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
PER_PAGE = 100

# A descriptive UA - the default `python-requests/x` string is a common bot
# signature and gets challenged more readily.
USER_AGENT = "footballsiam-sync/1.0 (+https://github.com/caustonsimon/footballsiam-data)"

# SiteGround's anti-bot system answers with an HTML page that meta-refreshes to
# /.well-known/sgcaptcha/ instead of the REST JSON. It is served with a 2xx
# status, so it has to be detected by body content.
CHALLENGE_MARKER = b"/.well-known/sgcaptcha/"
CHALLENGE_RETRIES = 3            # initial try + 2 retries
CHALLENGE_BACKOFF_SECONDS = 4    # * attempt -> 4s, 8s


class WPError(RuntimeError):
    pass


class WPHostChallengeError(WPError):
    """The host's anti-bot layer blocked the request (SiteGround sgcaptcha).

    Distinct from WPError so an unattended run can report "the host challenged
    us / the REST API was unreachable from this IP" rather than "the sync
    failed". Not a data or logic problem.
    """


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
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.headers["Accept"] = "application/json"
        self.auth = HTTPBasicAuth(self.username, self.app_password)
        self._club_title_cache: dict[int, str] = {}

    # -- low level -------------------------------------------------------

    @staticmethod
    def _is_host_challenge(resp: requests.Response) -> bool:
        """True when the host served an anti-bot challenge instead of the API."""
        ctype = resp.headers.get("content-type", "").lower()
        if "application/json" in ctype:
            return False
        return CHALLENGE_MARKER in resp.content[:8192]

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

            if self._is_host_challenge(resp):
                if attempt < CHALLENGE_RETRIES:
                    time.sleep(CHALLENGE_BACKOFF_SECONDS * attempt)
                    continue
                raise WPHostChallengeError(
                    f"{method} {path}: host anti-bot challenge (SiteGround "
                    f"sgcaptcha) after {attempt} attempts - the WP REST API was "
                    f"unreachable from this IP. Not a sync failure."
                )

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            return resp
        raise WPError(f"{method} {path} exhausted retries: {last_exc}")

    @staticmethod
    def _json_or_raise(resp: requests.Response, context: str) -> Any:
        if WPClient._is_host_challenge(resp):
            raise WPHostChallengeError(
                f"{context}: host anti-bot challenge (SiteGround sgcaptcha) - "
                f"the WP REST API was unreachable from this IP. Not a sync failure."
            )
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

    def get_match(self, post_id: int, fields: str | None = None) -> dict[str, Any]:
        """Full `matches` post by id (context=edit so all meta is returned)."""
        params: dict[str, Any] = {"context": "edit"}
        if fields:
            params["_fields"] = fields
        resp = self._request("GET", f"{CPT_REST_BASE}/{post_id}", params=params)
        return self._json_or_raise(resp, f"GET {CPT_REST_BASE}/{post_id}")

    def get_player(self, post_id: int, fields: str | None = None) -> dict[str, Any]:
        """Full `player` post by id (context=edit so all acf/meta is returned)."""
        params: dict[str, Any] = {"context": "edit"}
        if fields:
            params["_fields"] = fields
        resp = self._request("GET", f"{PLAYER_REST_BASE}/{post_id}", params=params)
        return self._json_or_raise(resp, f"GET {PLAYER_REST_BASE}/{post_id}")

    def get_club(self, post_id: int, fields: str | None = None) -> dict[str, Any]:
        """Full `club` post by id (context=edit so all acf/meta is returned)."""
        params: dict[str, Any] = {"context": "edit"}
        if fields:
            params["_fields"] = fields
        resp = self._request("GET", f"{CLUB_REST_BASE}/{post_id}", params=params)
        return self._json_or_raise(resp, f"GET {CLUB_REST_BASE}/{post_id}")

    def iter_all_clubs(self, fields: str = "id,title,acf") -> Iterator[dict[str, Any]]:
        """Yield every published `club` post. `context=edit` for full acf."""
        page = 1
        while True:
            resp = self._request(
                "GET",
                CLUB_REST_BASE,
                params={
                    "per_page": PER_PAGE,
                    "page": page,
                    "status": "publish",
                    "context": "edit",
                    "_fields": fields,
                    "orderby": "id",
                    "order": "asc",
                },
            )
            if resp.status_code == 400 and "rest_post_invalid_page_number" in resp.text:
                return
            batch = self._json_or_raise(resp, f"GET {CLUB_REST_BASE} page {page}")
            if not batch:
                return
            for post in batch:
                yield post
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
            if page >= total_pages:
                return
            page += 1

    def get_head_coach(self, post_id: int, fields: str | None = None) -> dict[str, Any]:
        """Full `head_coach` post by id (context=edit for full meta)."""
        params: dict[str, Any] = {"context": "edit"}
        if fields:
            params["_fields"] = fields
        resp = self._request("GET", f"{HEAD_COACH_REST_BASE}/{post_id}", params=params)
        return self._json_or_raise(resp, f"GET {HEAD_COACH_REST_BASE}/{post_id}")

    def iter_all_head_coaches(self, fields: str = "id,title,meta") -> Iterator[dict[str, Any]]:
        """Yield every published `head_coach` post. `context=edit` for full meta."""
        page = 1
        while True:
            resp = self._request(
                "GET",
                HEAD_COACH_REST_BASE,
                params={
                    "per_page": PER_PAGE,
                    "page": page,
                    "status": "publish",
                    "context": "edit",
                    "_fields": fields,
                    "orderby": "id",
                    "order": "asc",
                },
            )
            if resp.status_code == 400 and "rest_post_invalid_page_number" in resp.text:
                return
            if resp.status_code == 404:
                # head_coach CPT not registered/active yet (Snippet 143) -
                # treat as "no head coaches" rather than a hard error.
                return
            batch = self._json_or_raise(resp, f"GET {HEAD_COACH_REST_BASE} page {page}")
            if not batch:
                return
            for post in batch:
                yield post
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
            if page >= total_pages:
                return
            page += 1

    def index_head_coaches_by_manager_id(self) -> dict[int, dict[str, Any]]:
        """Map { footystats_manager_id (int) -> head_coach post } - full
        scan, same robustness reasoning as index_matches_by_footystats_id()
        (works regardless of whether the server supports REST meta_query
        filtering).
        """
        index: dict[int, dict[str, Any]] = {}
        for post in self.iter_all_head_coaches(fields="id,title,meta.footystats_manager_id"):
            raw = (post.get("meta") or {}).get("footystats_manager_id")
            try:
                mgr_id = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            index[mgr_id] = post
        return index

    def create_head_coach(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a new `head_coach` post. Honours dry_run."""
        if self.dry_run:
            return {"_dry_run": True, "would_create": payload}
        resp = self._request("POST", HEAD_COACH_REST_BASE, json=payload)
        return self._json_or_raise(resp, f"POST {HEAD_COACH_REST_BASE}")

    def update_head_coach(
        self, post_id: int, *, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """PATCH a `head_coach` post's meta. Honours dry_run."""
        if not meta:
            return {"_noop": True}
        body = {"meta": meta}
        if self.dry_run:
            return {"_dry_run": True, "would_update": post_id, **body}
        resp = self._request("POST", f"{HEAD_COACH_REST_BASE}/{post_id}", json=body)
        return self._json_or_raise(resp, f"POST {HEAD_COACH_REST_BASE}/{post_id}")

    def iter_all_players(self, fields: str = "id,title,acf") -> Iterator[dict[str, Any]]:
        """Yield every published `player` post. `context=edit` for full acf."""
        page = 1
        while True:
            resp = self._request(
                "GET",
                PLAYER_REST_BASE,
                params={
                    "per_page": PER_PAGE,
                    "page": page,
                    "status": "publish",
                    "context": "edit",
                    "_fields": fields,
                    "orderby": "id",
                    "order": "asc",
                },
            )
            if resp.status_code == 400 and "rest_post_invalid_page_number" in resp.text:
                return
            batch = self._json_or_raise(resp, f"GET {PLAYER_REST_BASE} page {page}")
            if not batch:
                return
            for post in batch:
                yield post
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
            if page >= total_pages:
                return
            page += 1

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

    def update_match(
        self,
        post_id: int,
        *,
        meta: dict[str, Any] | None = None,
        acf: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH a `matches` post with `meta` and/or `acf` in one request.

        `meta` keys must be registered with show_in_rest (register-match-meta.php
        / register-match-stats-meta.php); `acf` keys are ACF fields exposed to
        REST. Anything not registered is silently dropped by WordPress.
        """
        body: dict[str, Any] = {}
        if meta:
            body["meta"] = meta
        if acf:
            body["acf"] = acf
        if not body:
            return {"_noop": True}
        if self.dry_run:
            return {"_dry_run": True, "would_update": post_id, **body}
        resp = self._request("POST", f"{CPT_REST_BASE}/{post_id}", json=body)
        return self._json_or_raise(resp, f"POST {CPT_REST_BASE}/{post_id}")

    def create_player(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a new `player` post. Honours dry_run."""
        if self.dry_run:
            return {"_dry_run": True, "would_create": payload}
        resp = self._request("POST", PLAYER_REST_BASE, json=payload)
        return self._json_or_raise(resp, f"POST {PLAYER_REST_BASE}")

    def update_player(
        self,
        post_id: int,
        *,
        meta: dict[str, Any] | None = None,
        acf: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH a `player` post with `meta` and/or `acf`. Honours dry_run."""
        body: dict[str, Any] = {}
        if meta:
            body["meta"] = meta
        if acf:
            body["acf"] = acf
        if not body:
            return {"_noop": True}
        if self.dry_run:
            return {"_dry_run": True, "would_update": post_id, **body}
        resp = self._request("POST", f"{PLAYER_REST_BASE}/{post_id}", json=body)
        return self._json_or_raise(resp, f"POST {PLAYER_REST_BASE}/{post_id}")

    def update_club(
        self,
        post_id: int,
        *,
        meta: dict[str, Any] | None = None,
        acf: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH a `club` post with `meta` and/or `acf`. Honours dry_run."""
        body: dict[str, Any] = {}
        if meta:
            body["meta"] = meta
        if acf:
            body["acf"] = acf
        if not body:
            return {"_noop": True}
        if self.dry_run:
            return {"_dry_run": True, "would_update": post_id, **body}
        resp = self._request("POST", f"{CLUB_REST_BASE}/{post_id}", json=body)
        return self._json_or_raise(resp, f"POST {CLUB_REST_BASE}/{post_id}")

    def upload_media(
        self,
        file_path: str,
        *,
        filename: str | None = None,
        title: str | None = None,
        alt_text: str | None = None,
    ) -> dict[str, Any]:
        """Upload local file bytes straight to wp/v2/media and return the attachment.

        Unlike the WordPress connector's "upload image from URL" ability, this
        takes a file already sitting on disk (e.g. a chart PNG rendered locally)
        and needs no public URL of its own first. Returns the attachment dict,
        including `source_url` - the public URL other tools (e.g. Canva) can
        then fetch it from. Honours dry_run.
        """
        import mimetypes

        fname = filename or os.path.basename(file_path)
        mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        if self.dry_run:
            return {"_dry_run": True, "would_upload": file_path, "filename": fname}
        with open(file_path, "rb") as fh:
            data = fh.read()
        headers = {
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Content-Type": mime,
        }
        resp = self._request("POST", MEDIA_REST_BASE, data=data, headers=headers)
        result = self._json_or_raise(resp, f"POST {MEDIA_REST_BASE}")

        patch: dict[str, Any] = {}
        if title:
            patch["title"] = title
        if alt_text:
            patch["alt_text"] = alt_text
        if patch:
            patch_resp = self._request(
                "POST", f"{MEDIA_REST_BASE}/{result['id']}", json=patch
            )
            result = self._json_or_raise(
                patch_resp, f"POST {MEDIA_REST_BASE}/{result['id']}"
            )
        return result

    # -- predictor auto-grading (added 2026-09-17) ------------------
    # Custom fs/v1 REST routes on the site, mirroring the existing
    # fs/v1/update-match / fs/v1/update-live pattern (a shared-secret
    # header rather than current_user_can, because this host has already
    # been seen to strip the Application Password's Authorization header
    # on at least one custom route -- see the "Importer" snippet's
    # "Temporarily open to bypass token stripping" comment). WP_GRADE_TOKEN
    # must match the token hardcoded in the "FS Predictor Auto-Grading
    # Engine v1" Code Snippet's permission_callback.

    def get_matchweek_status(self, league: str, season: str, mw: int) -> dict[str, Any]:
        """GET fs/v1/matchweek-status - {total, played, remaining, complete}."""
        resp = self._request(
            "GET", "fs/v1/matchweek-status",
            params={"league": league, "season": season, "mw": mw},
        )
        return self._json_or_raise(resp, f"GET fs/v1/matchweek-status league={league} mw={mw}")

    def grade_predictor_matchweek(self, mw: int) -> dict[str, Any]:
        """POST fs/v1/grade-predictions - triggers automatic Predictor Game
        grading for one matchweek. Idempotent on the WP side (only ever
        touches pending predictions), so calling it for an already-graded
        matchweek is a safe no-op. Honours dry_run (does not call out at all
        in a dry run - grading has side effects: it creates fs_score posts
        and sends result emails, so it should never fire from a dry run).
        """
        if self.dry_run:
            return {"_dry_run": True, "would_grade_mw": mw}
        token = os.environ.get("WP_GRADE_TOKEN", "")
        if not token:
            raise WPError(
                "WP_GRADE_TOKEN not set - cannot call fs/v1/grade-predictions. "
                "See README > 'Predictor auto-grading'."
            )
        resp = self._request(
            "POST", "fs/v1/grade-predictions",
            json={"mw": mw},
            headers={"x-fs-grade-token": token},
        )
        return self._json_or_raise(resp, f"POST fs/v1/grade-predictions mw={mw}")

    # -- connectivity check ----------------------------------------

    def whoami(self) -> dict[str, Any]:
        """GET wp/v2/users/me - confirms the Application Password works."""
        resp = self._request("GET", "wp/v2/users/me", params={"_fields": "id,name,slug"})
        return self._json_or_raise(resp, "GET wp/v2/users/me")
