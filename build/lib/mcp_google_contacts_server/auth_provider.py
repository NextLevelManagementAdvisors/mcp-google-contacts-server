"""OAuth 2.1 authorization-server provider that brokers Google identity.

Flow summary:
  MCP client --> /authorize --> provider.authorize() --> Google consent URL
  Google --> /oauth/google/callback (separate Starlette route in google_oauth_routes.py)
      --> exchange Google code, upsert user, mint our auth code, redirect MCP client
  MCP client --> /token --> provider.exchange_authorization_code() --> OAuthToken
  MCP client --> /mcp (with Authorization: Bearer ...) --> provider.load_access_token()
"""
import secrets
import time
import urllib.parse
from typing import Optional, List

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from mcp_google_contacts_server.config import config
from mcp_google_contacts_server.db import Db


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class GoogleAccessToken(AccessToken):
    """AccessToken with the Google user's email threaded through so tools can look up creds."""

    google_email: str


class GoogleRefreshToken(RefreshToken):
    google_email: str


class GoogleAuthorizationCode(AuthorizationCode):
    google_email: str


def _new_token() -> str:
    return secrets.token_urlsafe(32)


class GoogleOAuthProvider(OAuthAuthorizationServerProvider[
    GoogleAuthorizationCode, GoogleRefreshToken, GoogleAccessToken
]):
    """Bridges our MCP clients' OAuth flow to Google OAuth."""

    def __init__(self, db: Db):
        self.db = db

    # ---- dynamic client registration ---------------------------------------

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        row = self.db.get_client(client_id)
        if not row:
            return None
        return OAuthClientInformationFull.model_validate(row)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise RegistrationError(error="invalid_client_metadata", error_description="missing client_id")
        self.db.put_client(
            client_info.client_id,
            client_info.model_dump(mode="json", exclude_none=True),
        )

    # ---- /authorize entry: redirect user through Google -------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not config.google_web_client_id:
            raise RuntimeError("GOOGLE_WEB_CLIENT_ID not configured; multi-tenant mode needs a Web OAuth client")
        if not config.google_oauth_redirect_uri:
            raise RuntimeError("GOOGLE_OAUTH_REDIRECT_URI not configured")

        state = _new_token()
        self.db.put_state(
            state,
            {
                "mcp_client_id": client.client_id or "",
                "mcp_redirect_uri": str(params.redirect_uri),
                "mcp_redirect_uri_explicit": params.redirect_uri_provided_explicitly,
                "mcp_code_challenge": params.code_challenge,
                "mcp_scopes": params.scopes or [],
                "mcp_resource": params.resource,
                "mcp_state": params.state,
            },
            ttl_seconds=config.oauth_state_ttl,
        )

        google_scopes = list(config.scopes) + ["openid", "email", "profile"]
        query = urllib.parse.urlencode(
            {
                "client_id": config.google_web_client_id,
                "redirect_uri": config.google_oauth_redirect_uri,
                "response_type": "code",
                "scope": " ".join(google_scopes),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "include_granted_scopes": "true",
            }
        )
        return f"{GOOGLE_AUTH_URL}?{query}"

    # ---- authorization codes (MCP side) -----------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[GoogleAuthorizationCode]:
        row = self.db.get_auth_code(authorization_code)
        if not row or row["mcp_client_id"] != (client.client_id or ""):
            return None
        return GoogleAuthorizationCode(
            code=row["code"],
            scopes=row["mcp_scopes"],
            expires_at=float(row["expires_at"]),
            client_id=row["mcp_client_id"],
            code_challenge=row["mcp_code_challenge"],
            redirect_uri=AnyUrl(row["mcp_redirect_uri"]),
            redirect_uri_provided_explicitly=row["mcp_redirect_uri_explicit"],
            resource=row["mcp_resource"],
            google_email=row["google_email"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: GoogleAuthorizationCode
    ) -> OAuthToken:
        self.db.delete_auth_code(authorization_code.code)

        now = int(time.time())
        access = _new_token()
        refresh = _new_token()
        self.db.put_access_token(
            access,
            google_email=authorization_code.google_email,
            mcp_client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + config.access_token_ttl,
            resource=authorization_code.resource,
        )
        self.db.put_refresh_token(
            refresh,
            google_email=authorization_code.google_email,
            mcp_client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + config.refresh_token_ttl,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=config.access_token_ttl,
            refresh_token=refresh,
            scope=" ".join(authorization_code.scopes),
        )

    # ---- refresh ----------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[GoogleRefreshToken]:
        row = self.db.get_refresh_token(refresh_token)
        if not row or row["mcp_client_id"] != (client.client_id or ""):
            return None
        return GoogleRefreshToken(
            token=row["token"],
            client_id=row["mcp_client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            google_email=row["google_email"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: GoogleRefreshToken,
        scopes: List[str],
    ) -> OAuthToken:
        # Rotate: issue a new access + refresh, invalidate the old refresh.
        self.db.delete_refresh_token(refresh_token.token)

        # Scope narrowing allowed; expanding is not.
        new_scopes = scopes or refresh_token.scopes
        if set(new_scopes) - set(refresh_token.scopes):
            raise TokenError(error="invalid_scope", error_description="scopes cannot expand")

        now = int(time.time())
        access = _new_token()
        refresh = _new_token()
        self.db.put_access_token(
            access,
            google_email=refresh_token.google_email,
            mcp_client_id=refresh_token.client_id,
            scopes=new_scopes,
            expires_at=now + config.access_token_ttl,
        )
        self.db.put_refresh_token(
            refresh,
            google_email=refresh_token.google_email,
            mcp_client_id=refresh_token.client_id,
            scopes=new_scopes,
            expires_at=now + config.refresh_token_ttl,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=config.access_token_ttl,
            refresh_token=refresh,
            scope=" ".join(new_scopes),
        )

    # ---- access-token validation (called per MCP request) -----------------

    async def load_access_token(self, token: str) -> Optional[GoogleAccessToken]:
        row = self.db.get_access_token(token)
        if not row:
            return None
        return GoogleAccessToken(
            token=row["token"],
            client_id=row["mcp_client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            resource=row["resource"],
            google_email=row["google_email"],
        )

    # ---- revoke -----------------------------------------------------------

    async def revoke_token(self, token) -> None:
        # token is either GoogleAccessToken or GoogleRefreshToken; both have .token
        if isinstance(token, AccessToken):
            self.db.delete_access_token(token.token)
        else:
            self.db.delete_refresh_token(token.token)
