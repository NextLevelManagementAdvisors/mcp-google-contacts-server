"""
MCP Google Contacts Server: A server that provides Google Contacts functionality
through the Machine Conversation Protocol (MCP).
"""
import sys
import os
import argparse
from typing import Dict, List, Optional
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from mcp_google_contacts_server.tools import register_tools, init_service
from mcp_google_contacts_server.config import config

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="MCP Google Contacts Server"
    )
    parser.add_argument(
        "--transport", 
        choices=["stdio", "http"], 
        default="stdio",
        help="Transport protocol to use (default: stdio)"
    )
    parser.add_argument(
        "--host", 
        default="localhost", 
        help="Host for HTTP transport (default: localhost)"
    )
    parser.add_argument(
        "--port", 
        type=int, 
        default=8000, 
        help="Port for HTTP transport (default: 8000)"
    )
    parser.add_argument(
        "--client-id",
        help="Google OAuth client ID (overrides environment variable)"
    )
    parser.add_argument(
        "--client-secret",
        help="Google OAuth client secret (overrides environment variable)"
    )
    parser.add_argument(
        "--refresh-token",
        help="Google OAuth refresh token (overrides environment variable)"
    )
    parser.add_argument(
        "--credentials-file",
        help="Path to Google OAuth credentials.json file"
    )
    
    return parser.parse_args()

def main():
    """Run the MCP server."""
    print("Starting Google Contacts MCP Server...")
    
    args = parse_args()
    
    # Update config based on arguments
    if args.client_id:
        os.environ["GOOGLE_CLIENT_ID"] = args.client_id
    if args.client_secret:
        os.environ["GOOGLE_CLIENT_SECRET"] = args.client_secret
    if args.refresh_token:
        os.environ["GOOGLE_REFRESH_TOKEN"] = args.refresh_token
    
    # Handle credentials file argument
    if args.credentials_file:
        credentials_path = Path(args.credentials_file)
        if credentials_path.exists():
            # Add the specified credentials file to the beginning of the search paths
            config.credentials_paths.insert(0, credentials_path)
            print(f"Using credentials file: {credentials_path}")
        else:
            print(f"Warning: Specified credentials file {credentials_path} not found")
    
    # Initialize FastMCP server
    mcp = FastMCP("google-contacts")
    
    # Register all tools
    register_tools(mcp)
    
    # Initialize service with credentials
    service = init_service()
    
    if not service:
        print("Warning: No valid Google credentials found. Authentication will be required.")
        print("You can provide credentials using environment variables or command line arguments:")
        print("  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN")
        print("  --client-id, --client-secret, --refresh-token, --credentials-file")
    
    # Run the MCP server with the specified transport
    if args.transport == "stdio":
        print("Running with stdio transport")
        mcp.run(transport='stdio')
    else:
        print(f"Running with HTTP transport on {args.host}:{args.port}")
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        extra_hosts = [h for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h]
        if extra_hosts:
            mcp.settings.transport_security.allowed_hosts.extend(extra_hosts)
        mcp.run(transport='streamable-http')

if __name__ == "__main__":
    main()