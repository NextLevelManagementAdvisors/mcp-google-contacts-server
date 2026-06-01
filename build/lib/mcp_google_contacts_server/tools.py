"""MCP tools implementation for Google Contacts."""
import asyncio
import threading
import time
import traceback
from collections import OrderedDict
from typing import Dict, List, Optional, Any, Union

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP

from mcp_google_contacts_server.google_contacts_service import GoogleContactsService, GoogleContactsError
from mcp_google_contacts_server.formatters import format_contact, format_contacts_list, format_directory_people
from mcp_google_contacts_server.config import config

# Global service instance (single-tenant mode only)
contacts_service = None

# Per-user service cache (multi-tenant mode)
_SERVICE_CACHE_MAX = 64
_SERVICE_CACHE_TTL = 55 * 60  # rebuild just before Google's 1h access-token expiry
_service_cache_lock = threading.Lock()
_service_cache: "OrderedDict[str, tuple[GoogleContactsService, float]]" = OrderedDict()

def init_service() -> Optional[GoogleContactsService]:
    """Initialize and return a Google Contacts service instance.
    
    Returns:
        GoogleContactsService instance or None if initialization fails
    """
    global contacts_service
    
    if contacts_service:
        return contacts_service
    
    try:
        # First try environment variables
        try:
            contacts_service = GoogleContactsService.from_env()
            print("Successfully loaded credentials from environment variables.")
            return contacts_service
        except GoogleContactsError:
            pass
            
        # Then try default file locations
        for path in config.credentials_paths:
            if path.exists():
                try:
                    print(f"Found credentials file at {path}")
                    contacts_service = GoogleContactsService.from_file(path)
                    print("Successfully loaded credentials from file.")
                    return contacts_service
                except GoogleContactsError as e:
                    print(f"Error with credentials at {path}: {e}")
                    continue
                
        print("No valid credentials found. Please provide credentials to use Google Contacts.")
        return None
        
    except Exception as e:
        print(f"Error initializing Google Contacts service: {str(e)}")
        traceback.print_exc()
        return None


def _service_for_email(google_email: str) -> Optional[GoogleContactsService]:
    """Build (or reuse) a GoogleContactsService for a specific user in multi-tenant mode.

    Looks up the user's refresh_token in the DB, constructs a per-user service,
    and caches it briefly so consecutive tool calls in a session don't repeatedly
    round-trip to Google's token endpoint.
    """
    now = time.monotonic()
    with _service_cache_lock:
        entry = _service_cache.get(google_email)
        if entry and entry[1] > now:
            _service_cache.move_to_end(google_email)
            return entry[0]

    # Import here to avoid a circular import at module load time
    from mcp_google_contacts_server.db import Db

    db = Db(config.db_path)
    user = db.get_user(google_email)
    if not user:
        return None

    try:
        svc = GoogleContactsService.from_tokens(
            client_id=config.google_web_client_id,
            client_secret=config.google_web_client_secret,
            refresh_token=user["google_refresh_token"],
        )
    except Exception as e:
        print(f"Failed to build service for {google_email}: {e}")
        traceback.print_exc()
        return None

    db.touch_user(google_email)

    with _service_cache_lock:
        _service_cache[google_email] = (svc, now + _SERVICE_CACHE_TTL)
        _service_cache.move_to_end(google_email)
        while len(_service_cache) > _SERVICE_CACHE_MAX:
            _service_cache.popitem(last=False)
    return svc


def _resolve_service() -> Optional[GoogleContactsService]:
    """Return the service appropriate for the current request.

    In single mode: shared global (init_service).
    In multi mode: the caller's per-user service via the bearer-token context.
    """
    if config.auth_mode == "multi":
        tok = get_access_token()
        email = getattr(tok, "google_email", None) if tok else None
        if not email:
            return None
        return _service_for_email(email)
    return init_service()


def register_tools(mcp: FastMCP) -> None:
    """Register all Google Contacts tools with the MCP server.
    
    Args:
        mcp: FastMCP server instance
    """
    
    @mcp.tool()
    async def list_contacts(name_filter: Optional[str] = None, max_results: int = 100) -> str:
        """List all contacts or filter by name.
        
        Args:
            name_filter: Optional filter to find contacts by name
            max_results: Maximum number of results to return (default: 100)
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            contacts = service.list_contacts(name_filter, max_results)
            return format_contacts_list(contacts)
        except Exception as e:
            return f"Error: Failed to list contacts - {str(e)}"

    @mcp.tool()
    async def get_contact(identifier: str) -> str:
        """Get a contact by resource name or email.
        
        Args:
            identifier: Resource name (people/*) or email address of the contact
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            contact = service.get_contact(identifier)
            return format_contact(contact)
        except Exception as e:
            return f"Error: Failed to get contact - {str(e)}"

    @mcp.tool()
    async def create_contact(given_name: str, family_name: Optional[str] = None, 
                           email: Optional[str] = None, phone: Optional[str] = None) -> str:
        """Create a new contact.
        
        Args:
            given_name: First name of the contact
            family_name: Last name of the contact
            email: Email address of the contact
            phone: Phone number of the contact
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            contact = service.create_contact(
                given_name, 
                family_name, 
                email, 
                phone
            )
            return f"Contact created successfully!\n\n{format_contact(contact)}"
        except Exception as e:
            return f"Error: Failed to create contact - {str(e)}"

    @mcp.tool()
    async def update_contact(resource_name: str, given_name: Optional[str] = None, 
                           family_name: Optional[str] = None, email: Optional[str] = None,
                           phone: Optional[str] = None) -> str:
        """Update an existing contact.
        
        Args:
            resource_name: Contact resource name (people/*)
            given_name: Updated first name
            family_name: Updated last name
            email: Updated email address
            phone: Updated phone number
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            contact = service.update_contact(
                resource_name,
                given_name,
                family_name,
                email,
                phone
            )
            return f"Contact updated successfully!\n\n{format_contact(contact)}"
        except Exception as e:
            return f"Error: Failed to update contact - {str(e)}"

    @mcp.tool()
    async def delete_contact(resource_name: str) -> str:
        """Delete a contact by resource name.
        
        Args:
            resource_name: Contact resource name (people/*) to delete
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            result = service.delete_contact(resource_name)
            if result.get('success'):
                return f"Contact {resource_name} deleted successfully."
            else:
                return f"Failed to delete contact: {result.get('message', 'Unknown error')}"
        except Exception as e:
            return f"Error: Failed to delete contact - {str(e)}"

    @mcp.tool()
    async def search_contacts(query: str, max_results: int = 10) -> str:
        """Search contacts by name, email, or phone number.
        
        Args:
            query: Search term to find in contacts
            max_results: Maximum number of results to return (default: 10)
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            # Get all contacts and filter locally with more flexible search
            all_contacts = service.list_contacts(max_results=max(100, max_results*2))
            
            query = query.lower()
            matches = []
            
            for contact in all_contacts:
                if (query in contact.get('displayName', '').lower() or
                    query in contact.get('givenName', '').lower() or
                    query in contact.get('familyName', '').lower() or
                    query in str(contact.get('email', '')).lower() or
                    query in str(contact.get('phone', '')).lower()):
                    matches.append(contact)
                    
                if len(matches) >= max_results:
                    break
                    
            if not matches:
                return f"No contacts found matching '{query}'."
                
            return f"Search results for '{query}':\n\n{format_contacts_list(matches)}"
        except Exception as e:
            return f"Error: Failed to search contacts - {str(e)}"

    @mcp.tool()
    async def list_workspace_users(query: Optional[str] = None, max_results: int = 50) -> str:
        """List Google Workspace users in your organization's directory.
        
        This tool allows you to search and list users in your Google Workspace directory,
        including their email addresses and other information.
        
        Args:
            query: Optional search term to find specific users (name, email, etc.)
            max_results: Maximum number of results to return (default: 50)
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            workspace_users = service.list_directory_people(query=query, max_results=max_results)
            return format_directory_people(workspace_users, query)
        except Exception as e:
            return f"Error: Failed to list Google Workspace users - {str(e)}"

    @mcp.tool()
    async def search_directory(query: str, max_results: int = 20) -> str:
        """Search for people specifically in the Google Workspace directory.
        
        This performs a more targeted search of your organization's directory.
        
        Args:
            query: Search term to find specific directory members
            max_results: Maximum number of results to return (default: 20)
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            results = service.search_directory(query, max_results)
            return format_directory_people(results, query)
        except Exception as e:
            return f"Error: Failed to search directory - {str(e)}"

    @mcp.tool()
    async def get_other_contacts(max_results: int = 50) -> str:
        """Retrieve contacts from the 'Other contacts' section.
        
        Other contacts are people you've interacted with but haven't added to your contacts list.
        These often include email correspondents that aren't in your main contacts.
        
        Args:
            max_results: Maximum number of results to return (default: 50)
        """
        service = _resolve_service()
        if not service:
            return "Error: Google Contacts service is not available. Please check your credentials."
        
        try:
            other_contacts = service.get_other_contacts(max_results)
            
            if not other_contacts:
                return "No 'Other contacts' found in your Google account."
            
            # Count how many have email addresses
            with_email = sum(1 for c in other_contacts if c.get('email'))
            
            # Format and return the results
            formatted_list = format_contacts_list(other_contacts)
            return f"Other Contacts (people you've interacted with but haven't added):\n\n{formatted_list}\n\n{with_email} of these contacts have email addresses."
        except Exception as e:
            return f"Error: Failed to retrieve other contacts - {str(e)}"
