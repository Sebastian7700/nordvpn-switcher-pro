from typing import Dict, List, Any
import time

import requests
from fake_useragent import UserAgent

from .exceptions import ApiClientError


class NordVpnApiClient:
    """A client for interacting with the public NordVPN API."""
    
    _DEFAULT_SERVER_FIELDS = {
        "fields[servers.id]": "",
        "fields[servers.name]": "",
        "fields[servers.load]": "",
        "fields[servers.locations.id]": "",
        "fields[servers.locations.country.id]": ""
    }

    def __init__(self, os_name: str = None):
        self.session = requests.Session()
        if os_name:
            self.session.headers.update({"User-Agent": UserAgent(os=os_name).random})
        else:
            self.session.headers.update({"User-Agent": UserAgent().random})

    def _get(self, url: str, params: Dict = None, error_message_prefix: str = None) -> Any:
        """
        Performs a GET request to a given API URL with a built-in retry mechanism.

        This method will automatically retry with increasing delays if a ConnectionError
        or Timeout occurs, which could happen immediately after a VPN network switch.
        HTTP errors (4xx, 5xx) are not retried as they indicate server-side issues.

        Args:
            url: The API URL to request.
            params: Optional query parameters.
            error_message_prefix: Optional custom prefix for error messages. If provided,
                                 messages will be: "{error_message_prefix}. Waiting {delay}s
                                 before re-checking (Attempt {attempt + 1}/{max_retries})..."
        
        Returns:
            The JSON response from the API.
        
        Raises:
            ApiClientError: If the request fails after all retries.
        """
        last_exception = None
        delays = [3, 5, 7, 10]  # Increasing delays for retries
        max_retries = len(delays)

        for attempt in range(max_retries):
            try:
                # print(f"Fetching data from {url} with params: {params}")
                response = self.session.get(url, params=params, timeout=20)
                response.raise_for_status()
                return response.json()

            except requests.exceptions.HTTPError as e:
                # For HTTP errors (like 404, 500), we don't retry. The server responded, but with an error.
                raise ApiClientError(f"HTTP Error for {url}: {e.response.status_code} - {e.response.text}") from e

            except requests.exceptions.RequestException as e:
                # This catches ConnectionError, Timeout, etc. These are worth retrying.
                last_exception = e
                if attempt < max_retries - 1:
                    delay = delays[attempt]
                    prefix = error_message_prefix if error_message_prefix else "Network request failed"
                    print(f"\x1b[33m{prefix}. Waiting {delay}s before re-checking (Attempt {attempt + 1}/{max_retries})...\x1b[0m")
                    time.sleep(delay)
                else:
                    print(f"\x1b[91mError: Network request failed after {max_retries} attempts.\x1b[0m")

        # If all retries fail, raise the final exception.
        raise ApiClientError(f"Request failed for {url} after {max_retries} attempts") from last_exception

    def get_current_ip_info(self, error_message_prefix: str = None) -> Dict:
        """
        Fetches information about the current IP address.
        
        Args:
            error_message_prefix: Optional custom prefix for error messages.
        """
        url = "https://api.nordvpn.com/v1/helpers/ips/insights"
        return self._get(url, error_message_prefix=error_message_prefix)

    def get_countries(self) -> List[Dict]:
        """Fetches a list of all countries with NordVPN servers."""
        url = "https://api.nordvpn.com/v1/servers/countries"
        return self._get(url)

    def get_groups(self) -> List[Dict]:
        """Fetches a list of all server groups (e.g., P2P, Regions)."""
        url = "https://api.nordvpn.com/v1/servers/groups"
        return self._get(url)
    
    def get_technologies(self) -> List[Dict]:
        """Fetches a list of all supported technologies."""
        url = "https://api.nordvpn.com/v1/technologies"
        return self._get(url)
    
    def get_group_server_count(self, group_id: int) -> Dict:
        """Fetches the number of servers in a specific group."""
        url = "https://api.nordvpn.com/v1/servers/count"
        params = {"filters[servers_groups][id]": group_id}
        return self._get(url, params=params)

    def get_recommendations(self, params: Dict) -> List[Dict]:
        """
        Fetches recommended servers based on filters.
        """
        url = "https://api.nordvpn.com/v1/servers/recommendations"
        return self._get(url, params=params)

    def get_servers_v2(self, params: Dict) -> Dict:
        """
        Fetches server data from the efficient v2 endpoint.
        """
        url = "https://api.nordvpn.com/v2/servers"
        return self._get(url, params=params)

    def get_server_details(self, server_id: int) -> List[Dict]:
        """
        Fetches detailed information for a single server by its ID.
        """
        url = "https://api.nordvpn.com/v1/servers"
        params = self._DEFAULT_SERVER_FIELDS.copy()
        params.update({
            "filters[servers.id]": server_id,
            "fields[servers.status]": "",
            "fields[servers.locations.country.city.name]": "",
        })
        return self._get(url, params=params)