"""Atlassian connector: Jira issues + Confluence pages (plan §9).

Single-person 3LO OAuth: the employee authorizes their own Atlassian account, so their accountId
is the authoritative identity. We capture the knowledge in Jira/Confluence, attributed to them:

- Jira issues they reported or were assigned (summary + description + comments)
- Confluence pages they created or edited (title + body)

Atlassian specifics handled here:
- `offline_access` gives a refresh token; access tokens expire in ~1h and refresh tokens ROTATE,
  so `refresh()` returns the new token set for the caller to persist.
- API calls go through the cloud id (resolved from accessible-resources).
- Jira description/comments are ADF (JSON) and Confluence bodies are storage XHTML — both are
  reduced to plain text here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

import httpx

from config import get_settings
from connectors.base import NormalizedDoc

logger = logging.getLogger(__name__)

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
API_BASE = "https://api.atlassian.com/ex"

SCOPES = (
    "read:jira-work read:jira-user "
    "read:confluence-content.all read:confluence-space.summary "
    "read:me offline_access"
)

JIRA_MAX = 25
CONFLUENCE_MAX = 20
COMMENT_MAX = 20


class AtlassianConnector:
    type = "atlassian"

    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.atlassian_client_id
        self._client_secret = s.atlassian_client_secret
        self._redirect_uri = s.atlassian_redirect_uri

    def auth_url(self, state: str) -> tuple[str, str]:
        params = {
            "audience": "api.atlassian.com",
            "client_id": self._client_id,
            "scope": SCOPES,
            "redirect_uri": self._redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        return f"{AUTH_URL}?{urlencode(params)}", ""  # no PKCE

    def exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, object]:
        resp = httpx.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return self._token_dict(resp.json())

    def refresh(self, tokens: dict[str, object]) -> dict[str, object] | None:
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            return None
        resp = httpx.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return self._token_dict(resp.json())

    @staticmethod
    def _token_dict(data: dict[str, Any]) -> dict[str, object]:
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "scope": data.get("scope"),
        }

    def sync(self, tokens: dict[str, object]) -> Iterator[NormalizedDoc]:
        token = str(tokens["access_token"])
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        with httpx.Client(headers=headers, timeout=30) as client:
            cloud = self._cloud_id(client)
            if not cloud:
                logger.warning("atlassian: no accessible cloud site")
                return
            account_id = self._account_id(client)
            if not account_id:
                logger.warning("atlassian: could not resolve accountId")
                return
            yield from self._jira(client, cloud, account_id)
            yield from self._confluence(client, cloud, account_id)

    def _cloud_id(self, client: httpx.Client) -> str | None:
        r = client.get(RESOURCES_URL)
        if r.status_code != 200:
            return None
        sites = r.json()
        return str(sites[0]["id"]) if sites else None

    def _account_id(self, client: httpx.Client) -> str | None:
        r = client.get("https://api.atlassian.com/me")
        if r.status_code != 200:
            return None
        account_id = r.json().get("account_id")
        return str(account_id) if account_id else None

    # --- Jira ---
    def _jira(self, client: httpx.Client, cloud: str, account_id: str) -> Iterator[NormalizedDoc]:
        base = f"{API_BASE}/jira/{cloud}/rest/api/3"
        jql = f"reporter = {account_id} OR assignee = {account_id} ORDER BY updated DESC"
        r = client.get(
            f"{base}/search",
            params={"jql": jql, "maxResults": JIRA_MAX, "fields": "summary,description"},
        )
        if r.status_code != 200:
            logger.warning("atlassian jira search %s: %s", r.status_code, r.text[:160])
            return
        for issue in r.json().get("issues", []):
            key = issue.get("key")
            fields = issue.get("fields", {})
            summary = fields.get("summary", "")
            desc = _adf_text(fields.get("description"))
            comments = self._jira_comments(client, base, key)
            text = (
                f"Jira issue {key} was reported/assigned to the departing person. "
                f"Title: {summary}\n\n{desc}"
            )
            if comments:
                text += "\n\nComments:\n" + "\n".join(comments)
            yield NormalizedDoc(
                external_id=f"jira:{key}",
                title=f"Jira {key}: {summary}",
                text=text,
                url=f"{API_BASE}/jira/{cloud}/browse/{key}",
                kind="jira_issue",
            )

    def _jira_comments(self, client: httpx.Client, base: str, key: str | None) -> list[str]:
        if not key:
            return []
        r = client.get(f"{base}/issue/{key}/comment", params={"maxResults": COMMENT_MAX})
        if r.status_code != 200:
            return []
        out = []
        for c in r.json().get("comments", []):
            who = (c.get("author") or {}).get("displayName", "someone")
            body = _adf_text(c.get("body"))
            if body:
                out.append(f"{who}: {body}")
        return out

    # --- Confluence ---
    def _confluence(
        self, client: httpx.Client, cloud: str, account_id: str
    ) -> Iterator[NormalizedDoc]:
        base = f"{API_BASE}/confluence/{cloud}/wiki/rest/api"
        cql = f"type=page and (creator = '{account_id}' or contributor = '{account_id}')"
        r = client.get(
            f"{base}/content/search",
            params={"cql": cql, "limit": CONFLUENCE_MAX, "expand": "body.storage"},
        )
        if r.status_code != 200:
            logger.warning("atlassian confluence search %s: %s", r.status_code, r.text[:160])
            return
        for page in r.json().get("results", []):
            pid = page.get("id")
            title = page.get("title", "")
            body = _html_text((page.get("body", {}).get("storage", {}) or {}).get("value", ""))
            if not body.strip():
                continue
            yield NormalizedDoc(
                external_id=f"confluence:{pid}",
                title=f"Confluence: {title}",
                text=(
                    f"Confluence page authored/edited by the departing person.\n\n{title}\n\n{body}"
                ),
                url=f"{API_BASE}/confluence/{cloud}/wiki/pages/{pid}",
                kind="confluence_page",
            )


def _adf_text(node: object) -> str:
    """Recursively extract plain text from an Atlassian Document Format (ADF) node."""
    if not isinstance(node, dict):
        return ""
    out: list[str] = []
    if node.get("type") == "text" and isinstance(node.get("text"), str):
        out.append(node["text"])
    for child in node.get("content", []) or []:
        out.append(_adf_text(child))
    # Join block-level content with spaces; good enough for embedding.
    return " ".join(p for p in out if p).strip()


def _html_text(html: str) -> str:
    """Crudely strip Confluence storage-format XHTML to text."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()
