import json
import os
from typing import Dict, List, Optional, Union, Any
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

from mcp_google_contacts_server.config import config

class GoogleContactsError(Exception):
    """Exception raised for errors in the Google Contacts service."""
    pass

class GoogleContactsService:
    """Service to interact with Google Contacts API."""
    
    def __init__(
        self,
        credentials_info: Optional[Dict[str, Any]] = None,
        token_path: Optional[Path] = None,
        cache_token: bool = True,
        refresh_token: Optional[str] = None,
    ):
        """Initialize the Google Contacts service with credentials info.

        Args:
            credentials_info: OAuth client credentials information
            token_path: Path to store the token file (defaults to config.token_path)
            cache_token: If False, skip all token.json read/write. Required for
                multi-tenant use where one process serves many users.
            refresh_token: Explicit refresh token. Takes precedence over
                GOOGLE_REFRESH_TOKEN env var when provided.
        """
        self.credentials_info = credentials_info
        self.refresh_token = refresh_token
        self.token_path = (token_path or config.token_path) if cache_token else None
        self.service = self._authenticate()
    
    @classmethod
    def from_file(cls, credentials_path: Union[str, Path], token_path: Optional[Path] = None) -> 'GoogleContactsService':
        """Create service instance from a credentials file.
        
        Args:
            credentials_path: Path to the credentials.json file
            token_path: Optional custom path to store the token
            
        Returns:
            Configured GoogleContactsService instance
            
        Raises:
            GoogleContactsError: If credentials file cannot be read
        """
        try:
            # Load the credentials from the provided file
            with open(credentials_path, 'r') as file:
                credentials_info = json.load(file)
            
            return cls(credentials_info, token_path)
        except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
            raise GoogleContactsError(f"Failed to load credentials from {credentials_path}: {str(e)}")
    
    @classmethod
    def from_env(cls, token_path: Optional[Path] = None) -> 'GoogleContactsService':
        """Create service instance from environment variables.
        
        Args:
            token_path: Optional custom path to store the token
            
        Returns:
            Configured GoogleContactsService instance
            
        Raises:
            GoogleContactsError: If required environment variables are missing
        """
        client_id = os.environ.get("GOOGLE_CLIENT_ID") or config.google_client_id
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or config.google_client_secret
        
        if not client_id or not client_secret:
            raise GoogleContactsError(
                "Missing Google API credentials. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
                "environment variables or provide a credentials file."
            )
        
        # Build credentials info from environment variables
        credentials_info = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
            }
        }
        
        return cls(credentials_info, token_path)

    @classmethod
    def from_tokens(
        cls,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> 'GoogleContactsService':
        """Create service for a single caller in multi-tenant mode.

        Skips token.json caching so concurrent users don't stomp each other.
        The refresh_token is used in-memory to mint access tokens on demand.
        """
        credentials_info = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            }
        }
        return cls(
            credentials_info=credentials_info,
            cache_token=False,
            refresh_token=refresh_token,
        )

    def _authenticate(self):
        """Authenticate with Google using credentials info.
        
        Returns:
            Authenticated Google service client
            
        Raises:
            GoogleContactsError: If authentication fails
        """
        try:
            creds = None
            token_path = self.token_path

            if token_path is not None:
                token_path.parent.mkdir(parents=True, exist_ok=True)
                if token_path.exists():
                    with open(token_path, 'r') as token_file:
                        creds = Credentials.from_authorized_user_info(
                            json.load(token_file), config.scopes)

            refresh_token = (
                self.refresh_token
                or os.environ.get("GOOGLE_REFRESH_TOKEN")
                or config.google_refresh_token
            )
            if not creds and refresh_token and self.credentials_info:
                client_id = self.credentials_info["installed"]["client_id"]
                client_secret = self.credentials_info["installed"]["client_secret"]
                
                creds = Credentials(
                    None,  # No access token initially
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=config.scopes
                )
            
            # If credentials don't exist or are invalid, go through auth flow
            if not creds or not creds.valid:
                if creds and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    if not self.credentials_info:
                        raise GoogleContactsError(
                            "No valid credentials found and no credentials info provided for authentication."
                        )
                    
                    flow = InstalledAppFlow.from_client_config(
                        self.credentials_info, config.scopes)
                    creds = flow.run_local_server(port=0)

                if token_path is not None:
                    with open(token_path, 'w') as token:
                        token.write(creds.to_json())

                    if creds.refresh_token:
                        print("\nNew refresh token obtained. Consider setting this in your environment:")
                        print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}\n")
            
            # Build and return the Google Contacts service
            return build('people', 'v1', credentials=creds)
        
        except Exception as e:
            raise GoogleContactsError(f"Authentication failed: {str(e)}")
    
    def list_contacts(self, name_filter: Optional[str] = None, 
                     max_results: int = None) -> List[Dict[str, Any]]:
        """List contacts, optionally filtering by name.
        
        Args:
            name_filter: Optional filter to find contacts by name
            max_results: Maximum number of results to return
            
        Returns:
            List of contact dictionaries
            
        Raises:
            GoogleContactsError: If API request fails
        """
        max_results = max_results or config.default_max_results
        
        try:
            # Get list of connections (contacts)
            results = self.service.people().connections().list(
                resourceName='people/me',
                pageSize=max_results,
                personFields='names,emailAddresses,phoneNumbers',
                sortOrder='FIRST_NAME_ASCENDING'
            ).execute()
            
            connections = results.get('connections', [])
            
            if not connections:
                return []
            
            contacts = []
            for person in connections:
                names = person.get('names', [])
                if not names:
                    continue
                
                name = names[0]
                given_name = name.get('givenName', '')
                family_name = name.get('familyName', '')
                display_name = name.get('displayName', '')
                
                # Apply name filter if provided
                if name_filter and name_filter.lower() not in display_name.lower():
                    continue
                
                # Get email addresses
                emails = person.get('emailAddresses', [])
                email = emails[0].get('value') if emails else None
                
                # Get phone numbers
                phones = person.get('phoneNumbers', [])
                phone = phones[0].get('value') if phones else None
                
                contacts.append({
                    'resourceName': person.get('resourceName'),
                    'givenName': given_name,
                    'familyName': family_name,
                    'displayName': display_name,
                    'email': email,
                    'phone': phone
                })
            
            return contacts
        
        except HttpError as error:
            raise GoogleContactsError(f"Error listing contacts: {error}")
    
    def get_contact(self, identifier: str, include_email: bool = True, 
                   use_directory_api: bool = False) -> Dict[str, Any]:
        """Get a contact by resource name or email.
        
        Args:
            identifier: Resource name (people/*) or email address
            include_email: Whether to include email addresses
            use_directory_api: Whether to try the directory API as well
            
        Returns:
            Contact dictionary
            
        Raises:
            GoogleContactsError: If contact cannot be found or API request fails
        """
        try:
            if identifier.startswith('people/'):
                # Determine which API to use based on parameters
                if use_directory_api:
                    # For directory contacts
                    try:
                        person = self.service.people().people().get(
                            resourceName=identifier,
                            personFields='names,emailAddresses,phoneNumbers,organizations'
                        ).execute()
                    except HttpError:
                        # Fall back to standard contacts API if directory API fails
                        person = self.service.people().get(
                            resourceName=identifier,
                            personFields='names,emailAddresses,phoneNumbers'
                        ).execute()
                else:
                    # Standard contacts API
                    person = self.service.people().get(
                        resourceName=identifier,
                        personFields='names,emailAddresses,phoneNumbers'
                    ).execute()
                
                return self._format_contact(person)
            else:
                # Assume it's an email address and search for it
                contacts = self.list_contacts()
                for contact in contacts:
                    if contact.get('email') == identifier:
                        return contact
                
                # If not found in regular contacts, try directory
                if use_directory_api:
                    directory_users = self.list_directory_people(query=identifier, max_results=1)
                    if directory_users:
                        return directory_users[0]
                
                raise GoogleContactsError(f"Contact with email {identifier} not found")
        
        except HttpError as error:
            raise GoogleContactsError(f"Error getting contact: {error}")
    
    def create_contact(self, given_name: str, family_name: Optional[str] = None, 
                       email: Optional[str] = None, phone: Optional[str] = None) -> Dict:
        """Create a new contact."""
        try:
            contact_body = {
                'names': [
                    {
                        'givenName': given_name,
                        'familyName': family_name or ''
                    }
                ]
            }
            
            if email:
                contact_body['emailAddresses'] = [{'value': email}]
            
            if phone:
                contact_body['phoneNumbers'] = [{'value': phone}]
            
            person = self.service.people().createContact(
                body=contact_body
            ).execute()
            
            return self._format_contact(person)
        
        except HttpError as error:
            raise GoogleContactsError(f"Error creating contact: {error}")
    
    def update_contact(self, resource_name: str, given_name: Optional[str] = None, 
                      family_name: Optional[str] = None, email: Optional[str] = None,
                      phone: Optional[str] = None) -> Dict:
        """Update an existing contact."""
        try:
            # Get the etag for the contact first
            person = self.service.people().get(
                resourceName=resource_name,
                personFields='names,emailAddresses,phoneNumbers'
            ).execute()
            
            etag = person.get('etag')
            
            # Prepare update masks and body
            update_person = {'etag': etag, 'resourceName': resource_name}
            update_fields = []
            
            # Update name if provided
            if given_name or family_name:
                current_name = person.get('names', [{}])[0]
                update_person['names'] = [{
                    'givenName': given_name if given_name is not None else current_name.get('givenName', ''),
                    'familyName': family_name if family_name is not None else current_name.get('familyName', '')
                }]
                update_fields.append('names')
            
            # Update email if provided
            if email:
                update_person['emailAddresses'] = [{'value': email}]
                update_fields.append('emailAddresses')
            
            # Update phone if provided
            if phone:
                update_person['phoneNumbers'] = [{'value': phone}]
                update_fields.append('phoneNumbers')
            
            # Execute update
            if update_fields:
                updated_person = self.service.people().updateContact(
                    resourceName=resource_name,
                    updatePersonFields=','.join(update_fields),
                    body=update_person
                ).execute()
                
                return self._format_contact(updated_person)
            else:
                return self._format_contact(person)
        
        except HttpError as error:
            raise GoogleContactsError(f"Error updating contact: {error}")
    
    def delete_contact(self, resource_name: str) -> Dict:
        """Delete a contact by resource name."""
        try:
            self.service.people().deleteContact(
                resourceName=resource_name
            ).execute()
            
            return {'success': True, 'resourceName': resource_name}
        
        except HttpError as error:
            raise GoogleContactsError(f"Error deleting contact: {error}")
    
    def list_directory_people(self, query: Optional[str] = None, max_results: int = 50) -> List[Dict]:
        """List people from the Google Workspace directory.
        
        Args:
            query: Optional search query to filter directory results
            max_results: Maximum number of results to return
            
        Returns:
            List of formatted directory contact dictionaries
        """
        try:
            # Check if directory API access is available
            directory_fields = 'names,emailAddresses,organizations,phoneNumbers'
            
            # Build the request, with or without a query
            if query:
                request = self.service.people().searchDirectoryPeople(
                    query=query,
                    readMask=directory_fields,
                    sources=['DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT', 'DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE'],
                    pageSize=max_results
                )
            else:
                request = self.service.people().listDirectoryPeople(
                    readMask=directory_fields,
                    sources=['DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT', 'DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE'],
                    pageSize=max_results
                )
            
            # Execute the request
            response = request.execute()
            print("response; ", response)

            
            # Process the results
            people = response.get('people', [])
            if not people:
                return []
            
            # Format each person entry
            directory_contacts = []
            for person in people:
                contact = self._format_directory_person(person)
                directory_contacts.append(contact)
            
            return directory_contacts
            
        except HttpError as error:
            # Handle gracefully if not a Google Workspace account
            if error.resp.status == 403:
                print("Directory API access forbidden. This may not be a Google Workspace account.")
                return []
            raise Exception(f"Error listing directory people: {error}")
    
    def search_directory(self, query: str, max_results: int = 20) -> List[Dict]:
        """Search for people in the Google Workspace directory.
        
        This is a more focused search function that uses the searchDirectoryPeople endpoint.
        
        Args:
            query: Search query to find specific users
            max_results: Maximum number of results to return
            
        Returns:
            List of matching directory contact dictionaries
        """
        try:
            response = self.service.people().searchDirectoryPeople(
                query=query,
                readMask='names,emailAddresses,organizations,phoneNumbers',
                sources=['DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT', 'DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE'],
                pageSize=max_results
            ).execute()
            
            people = response.get('people', [])
            
            
            if not people:
                return []
            
            # Format the results
            directory_results = []
            for person in people:
                contact = self._format_directory_person(person)
                directory_results.append(contact)
            
            return directory_results
            
        except HttpError as error:
            if error.resp.status == 403:
                print("Directory search access forbidden. This may not be a Google Workspace account.")
                return []
            raise Exception(f"Error searching directory: {error}")
    
    def get_other_contacts(self, max_results: int = 100) -> List[Dict]:
        """Get contacts from the 'Other contacts' section of Google Contacts.
        
        These are contacts that the user has interacted with but has not added to their contacts.
        
        Args:
            max_results: Maximum number of results to return
            
        Returns:
            List of other contact dictionaries
        """
        try:
            response = self.service.otherContacts().list(
                readMask='names,emailAddresses,phoneNumbers',
                pageSize=max_results
            ).execute()
            
            other_contacts = response.get('otherContacts', [])
            
            if not other_contacts:
                return []
            
            # Format the results
            contacts = []
            for person in other_contacts:
                contact = self._format_contact(person)
                contacts.append(contact)
            
            return contacts
            
        except HttpError as error:
            raise Exception(f"Error getting other contacts: {error}")
    
    def _format_contact(self, person: Dict) -> Dict:
        """Format a Google People API person object into a simplified contact."""
        names = person.get('names', [])
        emails = person.get('emailAddresses', [])
        phones = person.get('phoneNumbers', [])
        
        given_name = names[0].get('givenName', '') if names else ''
        family_name = names[0].get('familyName', '') if names else ''
        display_name = names[0].get('displayName', '') if names else f"{given_name} {family_name}".strip()
        
        return {
            'resourceName': person.get('resourceName'),
            'givenName': given_name,
            'familyName': family_name,
            'displayName': display_name,
            'email': emails[0].get('value') if emails else None,
            'phone': phones[0].get('value') if phones else None
        }
    
    def _format_directory_person(self, person: Dict) -> Dict:
        """Format a Google Directory API person object into a simplified contact.
        
        This handles the specific format of directory contacts which may have different
        organization and other fields compared to regular contacts.
        """
        names = person.get('names', [])
        emails = person.get('emailAddresses', [])
        phones = person.get('phoneNumbers', [])
        orgs = person.get('organizations', [])
        
        given_name = names[0].get('givenName', '') if names else ''
        family_name = names[0].get('familyName', '') if names else ''
        display_name = names[0].get('displayName', '') if names else f"{given_name} {family_name}".strip()
        
        # Get organization details - these are often present in directory entries
        department = ''
        job_title = ''
        if orgs:
            department = orgs[0].get('department', '')
            job_title = orgs[0].get('title', '')
        
        return {
            'resourceName': person.get('resourceName'),
            'givenName': given_name,
            'familyName': family_name,
            'displayName': display_name,
            'email': emails[0].get('value') if emails else None,
            'phone': phones[0].get('value') if phones else None,
            'department': department,
            'jobTitle': job_title
        }
