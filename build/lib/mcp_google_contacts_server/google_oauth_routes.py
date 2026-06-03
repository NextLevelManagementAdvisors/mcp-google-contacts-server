"""Extra Starlette routes that the FastMCP app needs for the Google auth loop.

Only `/oauth/google/callback` is exposed — the MCP client never calls this
directly; Google redirects the user's browser here at the end of consent.
"""
import secrets
import urllib.parse
from typing import Any, Dict

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from mcp_google_contacts_server.auth_provider import GoogleOAuthProvider
from mcp_google_contacts_server.config import config
from mcp_google_contacts_server.db import Db


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def register_routes(mcp: FastMCP, db: Db, provider: GoogleOAuthProvider) -> None:
    @mcp.custom_route("/oauth/google/callback", methods=["GET"])
    async def google_callback(request: Request) -> Response:
        err = request.query_params.get("error")
        state = request.query_params.get("state")
        google_code = request.query_params.get("code")

        if not state:
            return PlainTextResponse("missing state", status_code=400)

        state_row = db.pop_state(state)
        if not state_row:
            return PlainTextResponse("state not found or expired", status_code=400)

        mcp_redirect = state_row["mcp_redirect_uri"]
        mcp_state = state_row.get("mcp_state")

        if err:
            return _redirect_with(mcp_redirect, error=err, state=mcp_state)

        if not google_code:
            return _redirect_with(mcp_redirect, error="invalid_request", state=mcp_state)

        # 1. Exchange Google authorization code for Google tokens.
        try:
            token_resp = await _post_json(
                GOOGLE_TOKEN_URL,
                data={
                    "code": google_code,
                    "client_id": config.google_web_client_id or "",
                    "client_secret": config.google_web_client_secret or "",
                    "redirect_uri": config.google_oauth_redirect_uri or "",
                    "grant_type": "authorization_code",
                },
            )
        except httpx.HTTPError as exc:
            return _redirect_with(
                mcp_redirect,
                error="server_error",
                error_description=f"google token exchange failed: {exc}",
                state=mcp_state,
            )

        google_refresh_token = token_resp.get("refresh_token")
        google_access_token = token_resp.get("access_token")
        if not google_refresh_token or not google_access_token:
            return _redirect_with(
                mcp_redirect,
                error="server_error",
                error_description="google response missing refresh_token (user must re-consent)",
                state=mcp_state,
            )

        # 2. Get the user's email.
        try:
            userinfo = await _get_json(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {google_access_token}"},
            )
        except httpx.HTTPError as exc:
            return _redirect_with(
                mcp_redirect,
                error="server_error",
                error_description=f"userinfo failed: {exc}",
                state=mcp_state,
            )

        email = userinfo.get("email")
        email_verified = userinfo.get("email_verified")
        if not email or not email_verified:
            return _redirect_with(
                mcp_redirect,
                error="access_denied",
                error_description="google account has no verified email",
                state=mcp_state,
            )

        # 3. Persist the Google refresh_token keyed by email.
        db.upsert_user(email, google_refresh_token)

        # 4. Mint an authorization code for the MCP client.
        our_code = secrets.token_urlsafe(32)
        db.put_auth_code(
            our_code,
            {
                "google_email": email,
                "mcp_client_id": state_row["mcp_client_id"],
                "mcp_redirect_uri": state_row["mcp_redirect_uri"],
                "mcp_redirect_uri_explicit": state_row["mcp_redirect_uri_explicit"],
                "mcp_code_challenge": state_row["mcp_code_challenge"],
                "mcp_scopes": state_row["mcp_scopes"],
                "mcp_resource": state_row.get("mcp_resource"),
            },
            ttl_seconds=config.auth_code_ttl,
        )

        # 5. Send the browser back to the MCP client.
        return _redirect_with(mcp_redirect, code=our_code, state=mcp_state)


def _redirect_with(base_url: str, **params: Any) -> RedirectResponse:
    parsed = urllib.parse.urlparse(base_url)
    existing = urllib.parse.parse_qsl(parsed.query)
    existing.extend((k, v) for k, v in params.items() if v is not None)
    new_query = urllib.parse.urlencode(existing)
    final = urllib.parse.urlunparse(parsed._replace(query=new_query))
    return RedirectResponse(url=final, status_code=302)


async def _post_json(url: str, *, data: Dict[str, str]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, data=data)
        r.raise_for_status()
        return r.json()


async def _get_json(url: str, *, headers: Dict[str, str]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()
