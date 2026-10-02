"""Google sign-in for a desktop app (OAuth "installed app" flow).

Files, all in the per-user app-data folder (never the project folder):
    client_secret.json  the OAuth client you download from Google Cloud (SETUP.md)
    token.json          your saved sign-in, so you only sign in once
    account.json        the signed-in email, to show in the app

Permissions requested:
    documents    read and edit Google Docs you open with the app
    drive.file   only files this app creates itself (temporary diagram uploads);
                 it can't see anything else in your Drive
    openid/email your email address, to show who is signed in
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..config import config_dir

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Google may return the scopes in a different form ("email" vs the full URL);
# don't treat that as an error.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class AuthError(Exception):
    """A sign-in problem to show the user as a plain message."""


def client_file() -> Path:
    return config_dir() / "client_secret.json"


def token_file() -> Path:
    return config_dir() / "token.json"


def account_file() -> Path:
    return config_dir() / "account.json"


def has_client_file() -> bool:
    return client_file().exists()


def install_client_file(src: str | Path) -> None:
    """Check the downloaded OAuth client JSON and copy it into the app folder."""
    try:
        data = json.loads(Path(src).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthError(f"That file isn't a readable JSON file: {exc}")
    if "installed" not in data:
        kind = next(iter(data), "unknown")
        raise AuthError(
            "That's not a Desktop app client file. In Google Cloud, create an OAuth client "
            f"with Application type = 'Desktop app' (this one is '{kind}'). See SETUP.md."
        )
    client_file().parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, client_file())


def load_credentials():
    """Saved sign-in, refreshed if needed; None if you need to sign in."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_file().exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(token_file()), SCOPES)
    except (ValueError, OSError):
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Expired or revoked (in Testing mode this happens every 7 days)
            sign_out()
            return None
        _save(creds)
        return creds
    return None


def sign_in():
    """Open the browser for Google sign-in and wait (up to 5 minutes)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not has_client_file():
        raise AuthError("The app hasn't been set up with a Google client file yet (see SETUP.md).")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file()), SCOPES)
    try:
        creds = flow.run_local_server(
            port=0,
            timeout_seconds=300,
            prompt="consent",
            authorization_prompt_message="",
            success_message="Signed in. You can close this tab and go back to the app.",
        )
    except Exception as exc:
        raise AuthError(f"Sign-in didn't complete: {exc}")
    if creds is None:
        raise AuthError("Sign-in timed out. Please try again.")
    # Google's consent screen lets you untick individual permissions.
    granted = set(getattr(creds, "granted_scopes", None) or creds.scopes or [])
    missing = [s for s in SCOPES[:2] if granted and s not in granted]
    if missing:
        raise AuthError(
            "Some permissions weren't ticked on Google's screen, so the app can't write to your docs.\n\n"
            "Please sign in again and tick all the boxes (Google Docs, and the Drive files "
            "this app creates)."
        )
    _save(creds)
    email = _email_from(creds)
    account_file().write_text(json.dumps({"email": email}), encoding="utf-8")
    return creds


def sign_out() -> None:
    for f in (token_file(), account_file()):
        try:
            f.unlink()
        except FileNotFoundError:
            pass


def signed_in_email() -> str | None:
    try:
        return json.loads(account_file().read_text(encoding="utf-8")).get("email")
    except (OSError, ValueError):
        return None


def services(creds):
    """(docs, drive) API clients."""
    from googleapiclient.discovery import build

    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return docs, drive


def _save(creds) -> None:
    token_file().parent.mkdir(parents=True, exist_ok=True)
    token_file().write_text(creds.to_json(), encoding="utf-8")


def _email_from(creds) -> str | None:
    token = getattr(creds, "id_token", None)
    if not token:
        return None
    try:
        from google.auth import jwt

        return jwt.decode(token, verify=False).get("email")
    except Exception:
        return None
