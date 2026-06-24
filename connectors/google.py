"""Google connector: Drive + Docs + Gmail (plan §9, Phase 1.5).

One OAuth consent grants read-only access to Drive (including Google Docs) and Gmail. Sync
normalizes each artifact into a `NormalizedDoc`. Counts are bounded for the MVP (and we log
what we skipped, per the no-silent-caps rule).
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Iterator
from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from config import get_settings
from connectors.base import NormalizedDoc
from connectors.extract import extract_docx, extract_pdf

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

DRIVE_MAX = 25
GMAIL_MAX = 25
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class GoogleConnector:
    type = "google"

    def __init__(self) -> None:
        s = get_settings()
        self._client_config = {
            "web": {
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [s.google_redirect_uri],
            }
        }
        self._redirect_uri = s.google_redirect_uri

    def _flow(self, state: str | None = None) -> Flow:
        return Flow.from_client_config(
            self._client_config, scopes=SCOPES, redirect_uri=self._redirect_uri, state=state
        )

    def auth_url(self, state: str) -> tuple[str, str]:
        """Return (consent_url, code_verifier). The verifier (PKCE) must be persisted and
        passed back to exchange_code — the callback runs in a different Flow instance.
        """
        flow = self._flow(state=state)
        url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # force a refresh token every time
        )
        return str(url), str(flow.code_verifier)

    def exchange_code(self, code: str, code_verifier: str | None = None) -> dict[str, object]:
        # Google often returns scopes in a different order / adds `openid`, which makes oauthlib
        # raise "Scope has changed". Relax that check so the token exchange succeeds.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        # localhost redirect is http; allow it for the local dev OAuth exchange.
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        flow = self._flow()
        if code_verifier:
            flow.code_verifier = code_verifier  # restore PKCE verifier from the auth step
        flow.fetch_token(code=code)
        creds = flow.credentials
        return _creds_to_dict(creds)

    def refresh(self, tokens: dict[str, object]) -> dict[str, object] | None:
        # Google's client library auto-refreshes the access token at call time and the refresh
        # token is stable, so nothing to persist here.
        return None

    def sync(self, tokens: dict[str, object]) -> Iterator[NormalizedDoc]:
        creds = _dict_to_creds(tokens)
        yield from self._sync_drive(creds)
        yield from self._sync_gmail(creds)

    # --- Drive (incl. Google Docs) ---
    def _sync_drive(self, creds: Credentials) -> Iterator[NormalizedDoc]:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        resp = (
            service.files()
            .list(
                q="trashed = false",
                orderBy="modifiedTime desc",
                pageSize=DRIVE_MAX,
                fields="files(id, name, mimeType, webViewLink, owners(displayName))",
            )
            .execute()
        )
        files = resp.get("files", [])
        skipped = 0
        for f in files:
            mime = f.get("mimeType", "")
            text: str | None = None
            try:
                if mime == GOOGLE_DOC_MIME:
                    data = service.files().export(fileId=f["id"], mimeType="text/plain").execute()
                    text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
                    kind = "docs"
                elif mime == PDF_MIME:
                    raw = service.files().get_media(fileId=f["id"]).execute()
                    text = extract_pdf(raw if isinstance(raw, bytes) else bytes(raw))
                    kind = "drive_pdf"
                elif mime == DOCX_MIME:
                    raw = service.files().get_media(fileId=f["id"]).execute()
                    text = extract_docx(raw if isinstance(raw, bytes) else bytes(raw))
                    kind = "drive_docx"
                elif mime.startswith("text/"):
                    data = service.files().get_media(fileId=f["id"]).execute()
                    text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
                    kind = "drive"
                else:
                    skipped += 1
                    continue
            except Exception as exc:  # one bad file shouldn't kill the sync
                logger.warning("drive file %s failed: %s", f.get("id"), exc)
                skipped += 1
                continue

            if not text or not text.strip():
                continue
            owners = f.get("owners") or []
            author = owners[0].get("displayName") if owners else None
            yield NormalizedDoc(
                external_id=f"drive:{f['id']}",
                title=f.get("name", "Untitled"),
                text=text,
                author=author,
                url=f.get("webViewLink"),
                kind=kind,
            )
        if skipped:
            logger.info("drive sync skipped %d non-text files (cap %d)", skipped, DRIVE_MAX)

    # --- Gmail ---
    def _sync_gmail(self, creds: Credentials) -> Iterator[NormalizedDoc]:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        listing = (
            service.users()
            .messages()
            .list(userId="me", maxResults=GMAIL_MAX, q="-in:spam -in:trash")
            .execute()
        )
        for ref in listing.get("messages", []):
            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
            except Exception as exc:
                logger.warning("gmail message %s failed: %s", ref.get("id"), exc)
                continue
            headers = {
                h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])
            }
            subject = headers.get("subject", "(no subject)")
            sender = headers.get("from")
            body = _extract_gmail_text(msg.get("payload", {})) or msg.get("snippet", "")
            if not body.strip():
                continue
            yield NormalizedDoc(
                external_id=f"gmail:{ref['id']}",
                title=f"Email: {subject}",
                text=f"From: {sender}\nSubject: {subject}\n\n{body}",
                author=sender,
                url=None,
                kind="gmail",
            )


def _creds_to_dict(creds: Credentials) -> dict[str, object]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }


def _dict_to_creds(tokens: dict[str, object]) -> Credentials:
    return Credentials(  # type: ignore[no-untyped-call]  # google lib signature is untyped
        token=tokens.get("token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri=tokens.get("token_uri"),
        client_id=tokens.get("client_id"),
        client_secret=tokens.get("client_secret"),
        scopes=tokens.get("scopes"),
    )


def _extract_gmail_text(payload: dict[str, Any]) -> str:
    """Walk the MIME tree and return the first text/plain body (base64url-decoded)."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return _b64url(body["data"])
    for part in payload.get("parts", []) or []:
        text = _extract_gmail_text(part)
        if text:
            return text
    # Fallback: any html part, crudely stripped.
    if mime == "text/html" and body.get("data"):
        import re

        return re.sub(r"<[^>]+>", " ", _b64url(body["data"]))
    return ""


def _b64url(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
