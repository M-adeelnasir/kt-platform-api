"""GitHub connector: a departing employee's reasoning + ownership + unfinished work (plan §9).

Single-person model: the employee authorizes their own account, so their authenticated login is
the authoritative identity. We capture the KNOWLEDGE in GitHub, not the raw code:

- repos they actually contributed to (discovered via commit search — no scanning every repo)
- their commit messages (grouped per repo)
- their PRs (title + body + review/conversation comments) — where the "why" lives
- their issues (title + body + comments)
- each contributed repo's README

Everything is recent + bounded to stay under GitHub's rate limits. Org-wide GitHub App access
(offboarding-proof) is a later phase; this uses the user's OAuth token.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

import httpx

from config import get_settings
from connectors.base import NormalizedDoc

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
AUTH_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
SCOPES = "repo read:user"

# Bounded "recent activity" caps (keep well under the 5000 req/hr + 30 search req/min limits).
COMMITS_MAX = 60  # commits searched (grouped per repo)
PR_MAX = 20  # their most recent PRs, fully expanded with comments
ISSUE_MAX = 20  # their most recent issues, with comments
COMMENT_MAX = 20  # comments fetched per PR/issue
REPO_README_MAX = 15  # READMEs fetched across contributed repos


class GitHubConnector:
    type = "github"

    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.github_client_id
        self._client_secret = s.github_client_secret
        self._redirect_uri = s.github_redirect_uri

    # --- OAuth (unchanged: single-account, no PKCE, long-lived token) ---
    def auth_url(self, state: str) -> tuple[str, str]:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": SCOPES,
            "state": state,
            "allow_signup": "false",
        }
        return f"{AUTH_URL}?{urlencode(params)}", ""

    def exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, object]:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"github token exchange failed: {data}")
        return {
            "access_token": data["access_token"],
            "scope": data.get("scope"),
            "token_type": data.get("token_type"),
        }

    # --- Sync ---
    def refresh(self, tokens: dict[str, object]) -> dict[str, object] | None:
        return None  # GitHub OAuth App tokens are long-lived; no refresh.

    def sync(self, tokens: dict[str, object]) -> Iterator[NormalizedDoc]:
        token = str(tokens["access_token"])
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(base_url=GITHUB_API, headers=headers, timeout=30) as client:
            login = self._whoami(client) or str(tokens.get("github_username") or "")
            if not login:
                logger.warning("github sync: could not resolve the authenticated login")
                return

            contributed_repos: set[str] = set()
            yield from self._commits_by_repo(client, login, contributed_repos)
            yield from self._pull_requests(client, login)
            yield from self._issues(client, login)
            yield from self._readmes(client, login, contributed_repos)

    def _whoami(self, client: httpx.Client) -> str | None:
        r = client.get("/user")
        if r.status_code != 200:
            return None
        login = r.json().get("login")
        return str(login) if login else None

    def _commits_by_repo(
        self, client: httpx.Client, login: str, contributed: set[str]
    ) -> Iterator[NormalizedDoc]:
        items = self._search(client, f"author:{login}", "commits", per_page=COMMITS_MAX)
        # Group commit messages by repo (most recent first).
        by_repo: dict[str, list[str]] = {}
        for it in items:
            repo = ((it.get("repository") or {}).get("full_name")) or ""
            msg = ((it.get("commit") or {}).get("message") or "").splitlines()
            if repo and msg:
                by_repo.setdefault(repo, []).append(f"- {msg[0]}")
        for repo, lines in by_repo.items():
            contributed.add(repo)
            yield NormalizedDoc(
                external_id=f"gh:commits:{repo}",
                title=f"{repo} — recent commits by {login}",
                text=(
                    f"The following commits in the repository {repo} were authored by {login}. "
                    f"{login} worked on / contributed this code.\n\n"
                    f"Recent commit messages by {login}:\n" + "\n".join(lines)
                ),
                author=login,
                url=f"https://github.com/{repo}",
                kind="github_commits",
            )

    def _pull_requests(self, client: httpx.Client, login: str) -> Iterator[NormalizedDoc]:
        items = self._search(client, f"type:pr author:{login}", "issues", per_page=PR_MAX)
        for it in items[:PR_MAX]:
            repo = _repo_from_url(it.get("repository_url"))
            number = it.get("number")
            if not repo or number is None:
                continue
            body = it.get("body") or ""
            comments = self._comments(client, repo, int(number), is_pr=True)
            created = (it.get("created_at") or "")[:10]
            opened = f" on {created}" if created else ""
            header = (
                f"This GitHub pull request (#{number}) in the repository {repo} was authored by "
                f"{login}{opened}. {login} is the person who worked on this change. Any other "
                f"names below are reviewers or mentions, not the author."
            )
            text = f"{header}\n\nTitle: {it.get('title', '')}\n\n{body}"
            if comments:
                text += "\n\nDiscussion (comments by various people):\n" + "\n".join(comments)
            yield NormalizedDoc(
                external_id=f"gh:pr:{repo}:{number}",
                title=f"{repo} PR #{number}: {it.get('title', '')}",
                text=text,
                author=login,
                url=it.get("html_url"),
                kind="github_pr",
            )

    def _issues(self, client: httpx.Client, login: str) -> Iterator[NormalizedDoc]:
        items = self._search(client, f"type:issue author:{login}", "issues", per_page=ISSUE_MAX)
        for it in items[:ISSUE_MAX]:
            repo = _repo_from_url(it.get("repository_url"))
            number = it.get("number")
            if not repo or number is None:
                continue
            body = it.get("body") or ""
            comments = self._comments(client, repo, int(number), is_pr=False)
            created = (it.get("created_at") or "")[:10]
            opened = f" on {created}" if created else ""
            header = (
                f"This GitHub issue (#{number}) in the repository {repo} was opened by "
                f"{login}{opened}."
            )
            text = f"{header}\n\nTitle: {it.get('title', '')}\n\n{body}"
            if comments:
                text += "\n\nDiscussion (comments by various people):\n" + "\n".join(comments)
            yield NormalizedDoc(
                external_id=f"gh:issue:{repo}:{number}",
                title=f"{repo} issue #{number}: {it.get('title', '')}",
                text=text,
                author=login,
                url=it.get("html_url"),
                kind="github_issue",
            )

    def _readmes(
        self, client: httpx.Client, login: str, repos: set[str]
    ) -> Iterator[NormalizedDoc]:
        for repo in list(repos)[:REPO_README_MAX]:
            try:
                r = client.get(f"/repos/{repo}/readme")
                if r.status_code != 200:
                    continue
                content = base64.b64decode(r.json().get("content", "")).decode("utf-8", "replace")
            except Exception as exc:
                logger.warning("github readme %s failed: %s", repo, exc)
                continue
            if content.strip():
                yield NormalizedDoc(
                    external_id=f"gh:readme:{repo}",
                    title=f"{repo} — README",
                    text=(
                        f"This is the README of the repository {repo}, "
                        f"a project {login} contributed to.\n\n{content}"
                    ),
                    author=login,
                    url=f"https://github.com/{repo}",
                    kind="github_readme",
                )

    def _comments(self, client: httpx.Client, repo: str, number: int, *, is_pr: bool) -> list[str]:
        out: list[str] = []
        # Conversation comments (both PRs and issues live under /issues/{n}/comments).
        out.extend(self._comment_bodies(client, f"/repos/{repo}/issues/{number}/comments"))
        if is_pr:
            # Plus inline code-review comments.
            out.extend(self._comment_bodies(client, f"/repos/{repo}/pulls/{number}/comments"))
        return out[:COMMENT_MAX]

    def _comment_bodies(self, client: httpx.Client, path: str) -> list[str]:
        try:
            data = _json_list(client.get(path, params={"per_page": COMMENT_MAX}))
        except Exception as exc:
            logger.warning("github comments %s failed: %s", path, exc)
            return []
        bodies = []
        for c in data:
            user = ((c.get("user") or {}).get("login")) or "someone"
            body = (c.get("body") or "").strip()
            if body:
                bodies.append(f"{user}: {body}")
        return bodies

    def _search(
        self, client: httpx.Client, query: str, kind: str, per_page: int
    ) -> list[dict[str, Any]]:
        """GitHub search (kind 'commits'|'issues'), most-recent first; bounded to one page."""
        sort = "committer-date" if kind == "commits" else "updated"
        try:
            r = client.get(
                f"/search/{kind}",
                params={"q": query, "per_page": min(per_page, 100), "sort": sort, "order": "desc"},
            )
        except Exception as exc:
            logger.warning("github search %s failed: %s", kind, exc)
            return []
        if r.status_code != 200:
            logger.warning("github search %s returned %s: %s", kind, r.status_code, r.text[:160])
            return []
        items = r.json().get("items", [])
        return items if isinstance(items, list) else []


def _repo_from_url(repository_url: object) -> str | None:
    # repository_url looks like https://api.github.com/repos/{owner}/{name}
    if not isinstance(repository_url, str) or "/repos/" not in repository_url:
        return None
    return repository_url.split("/repos/", 1)[1]


def _json_list(resp: httpx.Response) -> list[dict[str, Any]]:
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data if isinstance(data, list) else []
