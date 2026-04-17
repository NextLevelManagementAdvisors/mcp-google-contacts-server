import os
from pathlib import Path
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

class ContactsConfig(BaseModel):
    """Configuration for Google Contacts integration."""
    google_client_id: Optional[str] = Field(
        default=None,
        description="Google OAuth client ID"
    )
    google_client_secret: Optional[str] = Field(
        default=None,
        description="Google OAuth client secret"
    )
    google_refresh_token: Optional[str] = Field(
        default=None,
        description="Google OAuth refresh token"
    )
    credentials_paths: List[Path] = Field(
        default_factory=list,
        description="Paths to search for credentials.json file"
    )
    token_path: Path = Field(
        default=Path.home() / ".config" / "google-contacts-mcp" / "token.json",
        description="Path to store the token file"
    )
    default_max_results: int = Field(
        default=100,
        description="Default maximum number of results to return"
    )
    scopes: List[str] = Field(
        default=[
            'https://www.googleapis.com/auth/contacts',
            'https://www.googleapis.com/auth/directory.readonly'
        ],
        description="OAuth scopes required for the application"
    )

    # --- Multi-tenant mode (AUTH_MODE=multi) --------------------------------
    auth_mode: str = Field(
        default="single",
        description="'single' = legacy one-refresh-token mode. 'multi' = per-user OAuth broker."
    )
    google_web_client_id: Optional[str] = Field(
        default=None,
        description="Google Web OAuth client ID (multi mode)"
    )
    google_web_client_secret: Optional[str] = Field(
        default=None,
        description="Google Web OAuth client secret (multi mode)"
    )
    oauth_issuer_url: Optional[str] = Field(
        default=None,
        description="Public base URL of this MCP server (e.g. https://contacts.nlma.io)"
    )
    google_oauth_redirect_uri: Optional[str] = Field(
        default=None,
        description="Redirect URI registered with Google for the Web OAuth client"
    )
    db_path: Path = Field(
        default=Path("/opt/contacts-mcp/data.db"),
        description="SQLite database for multi-tenant OAuth state"
    )
    access_token_ttl: int = Field(default=3600, description="Access token lifetime (seconds)")
    refresh_token_ttl: int = Field(default=60 * 60 * 24 * 30, description="Refresh token lifetime (seconds)")
    auth_code_ttl: int = Field(default=600, description="Authorization code lifetime (seconds)")
    oauth_state_ttl: int = Field(default=600, description="OAuth state row lifetime (seconds)")

def load_config() -> ContactsConfig:
    """Load configuration from environment variables and defaults."""
    # Default credentials paths to check
    default_paths = [
        Path.home() / ".config" / "google" / "credentials.json",
        Path.home() / "google_credentials.json",
        Path(__file__).parent / "credentials.json"
    ]
    
    # Create token directory if it doesn't exist
    token_dir = Path.home() / ".config" / "google-contacts-mcp"
    token_dir.mkdir(parents=True, exist_ok=True)
    
    kwargs = dict(
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        google_refresh_token=os.environ.get("GOOGLE_REFRESH_TOKEN"),
        credentials_paths=default_paths,
        token_path=token_dir / "token.json",
        auth_mode=os.environ.get("AUTH_MODE", "single"),
        google_web_client_id=os.environ.get("GOOGLE_WEB_CLIENT_ID"),
        google_web_client_secret=os.environ.get("GOOGLE_WEB_CLIENT_SECRET"),
        oauth_issuer_url=os.environ.get("OAUTH_ISSUER_URL"),
        google_oauth_redirect_uri=os.environ.get("GOOGLE_OAUTH_REDIRECT_URI"),
    )
    if db_path := os.environ.get("DB_PATH"):
        kwargs["db_path"] = Path(db_path)
    return ContactsConfig(**kwargs)

# Global configuration instance
config = load_config()
