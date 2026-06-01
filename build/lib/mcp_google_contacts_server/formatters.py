from typing import Dict, List, Optional, Any, Union

def format_contact(contact: Dict[str, Any]) -> str:
    """Format a contact dictionary into a readable string.
    
    Args:
        contact: Dictionary containing contact information
        
    Returns:
        Formatted string representation of the contact
    """
    if not contact:
        return "No contact data available"
        
    if "status" in contact and contact["status"] == "error":
        return f"Error: {contact.get('message', 'Unknown error')}"
        
    parts = []
    
    if "displayName" in contact and contact["displayName"]:
        parts.append(f"Name: {contact['displayName']}")
    elif "givenName" in contact:
        name_parts = []
        if contact.get("givenName"):
            name_parts.append(contact["givenName"])
        if contact.get("familyName"):
            name_parts.append(contact["familyName"])
        if name_parts:
            parts.append(f"Name: {' '.join(name_parts)}")
        
    if contact.get("email"):
        parts.append(f"Email: {contact['email']}")
        
    if contact.get("phone"):
        parts.append(f"Phone: {contact['phone']}")
    
    if contact.get("department"):
        parts.append(f"Department: {contact['department']}")
        
    if contact.get("jobTitle"):
        parts.append(f"Title: {contact['jobTitle']}")
        
    if contact.get("resourceName"):
        parts.append(f"ID: {contact['resourceName']}")
        
    return "\n".join(parts) if parts else "Contact has no details"

def format_contacts_list(contacts: List[Dict[str, Any]]) -> str:
    """Format a list of contacts into a readable string.
    
    Args:
        contacts: List of contact dictionaries
        
    Returns:
        Formatted string representation of the contacts list
    """
    if not contacts:
        return "No contacts found."
        
    if isinstance(contacts, dict) and "status" in contacts and contacts["status"] == "error":
        return f"Error: {contacts.get('message', 'Unknown error')}"
        
    parts = []
    
    for i, contact in enumerate(contacts, 1):
        parts.append(f"Contact {i}:\n{format_contact(contact)}\n")
        
    summary = f"Found {len(contacts)} contact(s)"
    parts.append(summary)
    
    return "\n".join(parts)

def format_directory_people(people: List[Dict[str, Any]], query: Optional[str] = None) -> str:
    """Format a list of directory people into a readable string.
    
    Args:
        people: List of directory people dictionaries
        query: Optional search query used to find these people
        
    Returns:
        Formatted string representation of the directory people
    """
    if not people:
        if query:
            return f"No directory members found matching '{query}'."
        return "No directory members found."
    
    # Count how many users have emails
    users_with_email = sum(1 for user in people if user.get('email'))
    
    # Format the results
    formatted_users = []
    for i, user in enumerate(people, 1):
        user_parts = []
        user_parts.append(f"Directory Member {i}:")
        
        if user.get('displayName'):
            user_parts.append(f"Name: {user['displayName']}")
        
        if user.get('email'):
            user_parts.append(f"Email: {user['email']}")
        
        if user.get('department'):
            user_parts.append(f"Department: {user['department']}")
        
        if user.get('jobTitle'):
            user_parts.append(f"Title: {user['jobTitle']}")
        
        if user.get('phone'):
            user_parts.append(f"Phone: {user['phone']}")
        
        if user.get('resourceName'):
            user_parts.append(f"ID: {user['resourceName']}")
        
        formatted_users.append("\n".join(user_parts))
    
    query_part = f" matching '{query}'" if query else ""
    summary = f"Found {len(people)} directory member(s){query_part}. {users_with_email} have email addresses."
    formatted_users.append(summary)
    
    return "\n\n".join(formatted_users)
