"""
MCP Google Contacts Server: A server that provides Google Contacts functionality
through the Machine Conversation Protocol (MCP).
"""
import argparse
import os
from pathlib import Path

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP

from mcp_google_contacts_server.auth_provider import GoogleOAuthProvider
from mcp_google_contacts_server.config import config
from mcp_google_contacts_server.db import Db
from mcp_google_contacts_server.google_oauth_routes import register_routes
from mcp_google_contacts_server.tools import init_service, register_tools


def parse_args():
    parser = argparse.ArgumentParser(description="MCP Google Contacts Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--client-id", help="Google OAuth client ID (single-mode override)")
    parser.add_argument("--client-secret", help="Google OAuth client secret (single-mode override)")
    parser.add_argument("--refresh-token", help="Google OAuth refresh token (single-mode override)")
    parser.add_argument("--credentials-file", help="Path to Google OAuth credentials.json file")
    return parser.parse_args()


def _build_mcp_single() -> FastMCP:
    """Legacy mode: one baked-in refresh token shared by all callers."""
    return FastMCP("google-contacts")


def _build_mcp_multi() -> FastMCP:
    """Per-user Google OAuth broker mode."""
    if not config.oauth_issuer_url:
        raise RuntimeError("OAUTH_ISSUER_URL required when AUTH_MODE=multi")
    if not config.google_web_client_id or not config.google_web_client_secret:
        raise RuntimeError("GOOGLE_WEB_CLIENT_ID / GOOGLE_WEB_CLIENT_SECRET required when AUTH_MODE=multi")
    if not config.google_oauth_redirect_uri:
        raise RuntimeError("GOOGLE_OAUTH_REDIRECT_URI required when AUTH_MODE=multi")

    db = Db(config.db_path)
    provider = GoogleOAuthProvider(db)
    mcp = FastMCP(
        "google-contacts",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=config.oauth_issuer_url,
            resource_server_url=f"{config.oauth_issuer_url.rstrip('/')}/mcp",
            required_scopes=[],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                default_scopes=config.scopes,
                valid_scopes=config.scopes,
            ),
        ),
    )
    register_routes(mcp, db, provider)
    return mcp


def main():
    print("Starting Google Contacts MCP Server...")
    args = parse_args()

    if args.client_id:
        os.environ["GOOGLE_CLIENT_ID"] = args.client_id
    if args.client_secret:
        os.environ["GOOGLE_CLIENT_SECRET"] = args.client_secret
    if args.refresh_token:
        os.environ["GOOGLE_REFRESH_TOKEN"] = args.refresh_token

    if args.credentials_file:
        credentials_path = Path(args.credentials_file)
        if credentials_path.exists():
            config.credentials_paths.insert(0, credentials_path)
            print(f"Using credentials file: {credentials_path}")
        else:
            print(f"Warning: Specified credentials file {credentials_path} not found")

    if config.auth_mode == "multi":
        print("AUTH_MODE=multi: per-user Google OAuth broker enabled")
        mcp = _build_mcp_multi()
    else:
        print("AUTH_MODE=single: legacy single-tenant mode")
        mcp = _build_mcp_single()

    register_tools(mcp)

    if config.auth_mode == "single":
        service = init_service()
        if not service:
            print("Warning: No valid Google credentials found. Authentication will be required.")
            print("You can provide credentials using environment variables or command line arguments:")
            print("  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN")
            print("  --client-id, --client-secret, --refresh-token, --credentials-file")

    if args.transport == "stdio":
        print("Running with stdio transport")
        mcp.run(transport="stdio")
    else:
        print(f"Running with HTTP transport on {args.host}:{args.port}")
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        extra_hosts = [h for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h]
        if extra_hosts:
            mcp.settings.transport_security.allowed_hosts.extend(extra_hosts)
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
