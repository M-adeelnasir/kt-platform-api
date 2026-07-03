"""Microsoft 365 connector: Outlook mail + OneDrive/SharePoint files + Teams messages (plan §9).

Single-person delegated OAuth (Azure AD, confidential client with a secret — no PKCE): the
employee authorizes their own Microsoft account, and we capture the knowledge attributed to them
via Microsoft Graph:

- Outlook mail (recent messages, subject + body)
- OneDrive / SharePoint files (Word/PDF/text — extracted to plain text via `extract.py`)
- Teams messages (their 1:1/group chats + channels of teams they're in)

Microsoft specifics handled here:
- Access tokens expire (~1h); `offline_access` yields a refresh token. Microsoft issues a fresh
  refresh token on each refresh, so `refresh()` returns the new set for the caller to persist.
- Graph bodies are HTML → reduced to plain text.
- Teams (esp. channel messages) can require admin-consented scopes; that section is best-effort
  and skips (logs) on 403 so mail + files still sync.
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
from connectors.extract import extract_docx, extract_pdf

logger = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"

# Delegated Graph scopes. offline_access → refresh token; the rest are read-only.
SCOPES = (
    "offline_access openid profile User.Read Mail.Read Files.Read.All Sites.Read.All "
    "Chat.Read ChannelMessage.Read.All Channel.ReadBasic.All Team.ReadBasic.All"
)

MAIL_MAX = 25
FILES_MAX = 20
CHAT_MAX = 10
TEAM_MAX = 5
CHANNEL_MAX = 5
MSG_MAX = 20


class MicrosoftConnector:
    type = "microsoft"

    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.microsoft_client_id
        self._client_secret = s.microsoft_client_secret
        self._redirect_uri = s.microsoft_redirect_uri
        self._tenant = s.microsoft_tenant_id or "common"

    def _authority(self, path: str) -> str:
        return f"{AUTHORITY}/{self._tenant}/oauth2/v2.0/{path}"

    def auth_url(self, state: str) -> tuple[str, str]:
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "prompt": "consent",  # ensure a refresh token is granted
        }
        return f"{self._authority('authorize')}?{urlencode(params)}", ""  # no PKCE (confidential)

    def exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, object]:
        resp = httpx.post(
            self._authority("token"),
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "scope": SCOPES,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return _token_dict(resp.json())

    def refresh(self, tokens: dict[str, object]) -> dict[str, object] | None:
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            return None
        resp = httpx.post(
            self._authority("token"),
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": SCOPES,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return _token_dict(resp.json())

    def sync(self, tokens: dict[str, object]) -> Iterator[NormalizedDoc]:
        token = str(tokens["access_token"])
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
            yield from self._sync_mail(client)
            yield from self._sync_files(client)
            yield from self._sync_teams(client)

    # --- Outlook mail ---
    def _sync_mail(self, client: httpx.Client) -> Iterator[NormalizedDoc]:
        r = client.get(
            f"{GRAPH}/me/messages",
            params={
                "$top": MAIL_MAX,
                "$select": "id,subject,from,receivedDateTime,body,bodyPreview,webLink",
                "$orderby": "receivedDateTime desc",
            },
        )
        if r.status_code != 200:
            logger.warning("m365 mail %s: %s", r.status_code, r.text[:160])
            return
        for msg in r.json().get("value", []):
            subject = msg.get("subject") or "(no subject)"
            sender = (
                ((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "unknown"
            )
            body = _html_text((msg.get("body") or {}).get("content", "")) or msg.get(
                "bodyPreview", ""
            )
            if not body.strip():
                continue
            yield NormalizedDoc(
                external_id=f"msmail:{msg['id']}",
                title=f"Email: {subject}",
                text=f"From: {sender}\nSubject: {subject}\n\n{body}",
                author=sender,
                url=msg.get("webLink"),
                kind="ms_mail",
            )

    # --- OneDrive / SharePoint files ---
    def _sync_files(self, client: httpx.Client) -> Iterator[NormalizedDoc]:
        r = client.get(f"{GRAPH}/me/drive/recent", params={"$top": FILES_MAX})
        if r.status_code != 200:
            logger.warning("m365 files %s: %s", r.status_code, r.text[:160])
            return
        skipped = 0
        for item in r.json().get("value", []):
            name = item.get("name") or "Untitled"
            lower = name.lower()
            download_url = item.get("@microsoft.graph.downloadUrl")
            try:
                if download_url:
                    raw = httpx.get(str(download_url), timeout=60, follow_redirects=True).content
                else:
                    ref = item.get("parentReference") or {}
                    drive_id, item_id = ref.get("driveId"), item.get("id")
                    if not drive_id or not item_id:
                        skipped += 1
                        continue
                    raw = client.get(f"{GRAPH}/drives/{drive_id}/items/{item_id}/content").content

                if lower.endswith(".pdf"):
                    text = extract_pdf(raw)
                elif lower.endswith(".docx"):
                    text = extract_docx(raw)
                elif lower.endswith((".txt", ".md", ".csv", ".json")):
                    text = raw.decode("utf-8", errors="replace")
                else:
                    skipped += 1
                    continue
            except Exception as exc:  # one bad file shouldn't kill the sync
                logger.warning("m365 file %s failed: %s", name, exc)
                skipped += 1
                continue

            if not text or not text.strip():
                continue
            author = ((item.get("lastModifiedBy") or {}).get("user") or {}).get("displayName")
            yield NormalizedDoc(
                external_id=f"msfile:{item.get('id')}",
                title=name,
                text=text,
                author=author,
                url=item.get("webUrl"),
                kind="ms_file",
            )
        if skipped:
            logger.info(
                "m365 files skipped %d unsupported/empty items (cap %d)", skipped, FILES_MAX
            )

    # --- Teams (best-effort; channel messages may need admin consent) ---
    def _sync_teams(self, client: httpx.Client) -> Iterator[NormalizedDoc]:
        yield from self._sync_chats(client)
        yield from self._sync_channels(client)

    def _sync_chats(self, client: httpx.Client) -> Iterator[NormalizedDoc]:
        r = client.get(f"{GRAPH}/me/chats", params={"$top": CHAT_MAX})
        if r.status_code != 200:
            logger.info("m365 teams chats %s (skipping): %s", r.status_code, r.text[:120])
            return
        for chat in r.json().get("value", []):
            chat_id = chat.get("id")
            if not chat_id:
                continue
            topic = chat.get("topic") or "Teams chat"
            lines = self._messages(client, f"{GRAPH}/chats/{chat_id}/messages")
            if not lines:
                continue
            yield NormalizedDoc(
                external_id=f"mschat:{chat_id}",
                title=f"Teams chat: {topic}",
                text=f"Teams chat ({topic}) involving the departing person:\n\n" + "\n".join(lines),
                url=chat.get("webUrl"),
                kind="ms_teams_chat",
            )

    def _sync_channels(self, client: httpx.Client) -> Iterator[NormalizedDoc]:
        r = client.get(f"{GRAPH}/me/joinedTeams", params={"$top": TEAM_MAX})
        if r.status_code != 200:
            logger.info("m365 joinedTeams %s (skipping): %s", r.status_code, r.text[:120])
            return
        for team in r.json().get("value", [])[:TEAM_MAX]:
            team_id, team_name = team.get("id"), team.get("displayName", "Team")
            if not team_id:
                continue
            cr = client.get(f"{GRAPH}/teams/{team_id}/channels", params={"$top": CHANNEL_MAX})
            if cr.status_code != 200:
                continue
            for ch in cr.json().get("value", [])[:CHANNEL_MAX]:
                ch_id, ch_name = ch.get("id"), ch.get("displayName", "General")
                if not ch_id:
                    continue
                lines = self._messages(
                    client, f"{GRAPH}/teams/{team_id}/channels/{ch_id}/messages"
                )
                if not lines:
                    continue
                yield NormalizedDoc(
                    external_id=f"mschannel:{team_id}:{ch_id}",
                    title=f"Teams: {team_name} / {ch_name}",
                    text=(
                        f"Teams channel {team_name} / {ch_name} messages:\n\n" + "\n".join(lines)
                    ),
                    url=ch.get("webUrl"),
                    kind="ms_teams_channel",
                )

    def _messages(self, client: httpx.Client, url: str) -> list[str]:
        r = client.get(url, params={"$top": MSG_MAX})
        if r.status_code != 200:
            return []
        out: list[str] = []
        for m in r.json().get("value", []):
            who = ((m.get("from") or {}).get("user") or {}).get("displayName") or "someone"
            body = _html_text((m.get("body") or {}).get("content", ""))
            if body:
                out.append(f"{who}: {body}")
        return out


def _token_dict(data: dict[str, Any]) -> dict[str, object]:
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "scope": data.get("scope"),
    }


def _html_text(html: str) -> str:
    """Reduce Graph HTML message/mail bodies to plain text."""
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
