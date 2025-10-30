from typing import Dict, List, Any

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
        "fields[servers.locations.country.id]": "",
        "fields[servers.locations.country.name]": ""
    }

    def __init__(self, os_name: str = None):
        self.session = requests.Session()
        if os_name:
            self.session.headers.update({"User-Agent": UserAgent(os=os_name).random})
        else:
            self.session.headers.update({"User-Agent": UserAgent().random})

    def _get(self, url: str, params: Dict = None) -> Any:
        """
        Performs a GET request to a given API URL.
        """
        try:
            # print(f"Fetching data from {url} with params: {params}")
            response = self.session.get(url, params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise ApiClientError(f"HTTP Error for {url}: {e.response.status_code} - {e.response.text}")
        except requests.exceptions.RequestException as e:
            raise ApiClientError(f"Request failed for {url}: {e}")

    def get_current_ip_info(self) -> Dict:
        """
        Fetches information about the current IP address.
        """
        url = "https://api.nordvpn.com/v1/helpers/ips/insights"
        return self._get(url)

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
        })
        return self._get(url, params=params)