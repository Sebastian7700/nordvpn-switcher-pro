import os
import random
import time
import platform as _platform
from typing import Dict, List, Tuple

from . import ui
from .api_client import NordVpnApiClient
from .exceptions import ApiClientError, ConfigurationError, NordVpnConnectionError, NoServersAvailableError
from .settings import RotationSettings
from .windows_controller import WindowsVpnController, find_nordvpn_executable

class VpnSwitcher:
    """
    Manages NordVPN connections, providing an automated way to rotate servers.

    This class encapsulates all logic for setting up, starting a session,
    rotating connections, and terminating the session gracefully.
    """

    def __init__(self, settings_path: str = "nordvpn_settings.json", force_setup: bool = False, cache_expiry_hours: int = 24, custom_exe_path: str = None, clear_server_cache: bool = False):
        """
        Creates a VpnSwitcher to automate NordVPN server connections.

        This is the main entry point for this library. When you create a VpnSwitcher,
        it either loads your preferences from a settings file (e.g., "nordvpn_settings.json")
        or launches a one-time interactive setup to help you configure your desired
        rotation strategy (e.g., specific countries, server types).

        The switcher remembers which servers you've used recently to avoid connecting
        to the same IP address repeatedly.

        Basic Usage Example:
        ```python
        from nordvpn_switcher import VpnSwitcher
        import time

        # 1. Initialize the switcher. If settings don't exist, it will
        #    launch an interactive setup in your terminal.
        switcher = VpnSwitcher()

        try:
            # 2. Start the session (connects to the network, prepares server list).
            switcher.start_session()

            for i in range(3):
                # 3. Rotate to a new server based on your settings.
                print(f"\\n--- Rotation attempt {i+1} ---")
                switcher.rotate()
                print("Waiting 15 seconds before next rotation...")
                time.sleep(15)

        finally:
            # 4. Always terminate the session to disconnect and save the cache.
            switcher.terminate()
        ```

        Args:
            settings_path (str, optional): The path to the JSON file for
                loading and saving your rotation preferences and server cache.
                Defaults to "nordvpn_settings.json".
            force_setup (bool, optional): If `True`, forces the interactive setup
                to run, overwriting any existing settings file. Defaults to `False`.
            cache_expiry_hours (int, optional): The number of hours a server is
                considered "recently used". After this period, it becomes
                available for connection again. Defaults to 24.
            custom_exe_path (str, optional): A custom path to the NordVPN
                executable. If provided, this path is used instead of attempting
                to find it automatically. (Use if NordVPN is installed in a
                non-standard location.)
            clear_server_cache (bool, optional): If `True`, clears all used
                servers from the settings file immediately after loading
                (or creating) settings and saves the cleared state back to
                `settings_path`. This is useful if you want to force the
                switcher to treat all servers as unused on startup.
                Defaults to `False`.
        """
        self.settings_path = settings_path
        # Detect platform for fake-useragent restriction and controller setup
        _os = _platform.system()
        _message = "If you'd like to contribute, please visit 'https://github.com/Sebastian7700/nordvpn-switcher-pro'!"
        match _os:
            case "Windows":
                fakeua_os = "Windows"
                controller_type = WindowsVpnController
            case "Linux":
                fakeua_os = "Linux"
                controller_type = None
                print(f"[nordvpn-switcher-pro] Linux is not yet supported. {_message}")
            case "Darwin":
                fakeua_os = "Mac OS X"
                controller_type = None
                print(f"[nordvpn-switcher-pro] Mac OS X is not yet supported. {_message}")
            case _:
                fakeua_os = None
                controller_type = None
                print(f"[nordvpn-switcher-pro] Platform '{_os}' is not supported. {_message}")
        self.api_client = NordVpnApiClient(fakeua_os)
        self.settings = self._load_or_create_settings(force_setup, cache_expiry_hours, custom_exe_path)

        # Optionally clear the used-servers cache on startup.
        if clear_server_cache:
            self._clear_server_cache()

        # --- Instance variables for an active session ---
        self._controller_type: type[WindowsVpnController] | None = controller_type  # Store the controller class for later instantiation
        self._controller: WindowsVpnController | None = None  # Will be initialized when session starts
        self._session_coordinates: Dict | None = None
        self._last_known_ip: str | None = None
        self._current_server_pool: List[Dict] = []
        self._pool_timestamp: float = 0
        self._current_country_index: int = 0
        self._current_limit: int = 0
        self._last_raw_server_count: int = -1
        self._is_session_active: bool = False
        self._servers_available_from_cache_count: int = 0
        self._refresh_interval: int = 3600
        self._session_connections: int = 0  # Track number of successful connections in sessio
        # Server pool cache: Indexed by _current_country_index, stores pool state to avoid re-fetching when returning to a previous location
        self._server_pool_cache: Dict[int, Dict] = {}
    
    def start_session(self):
        """
        Initializes a live VPN rotation session.

        This method prepares the switcher for active use. It performs several
        key actions:
        - Establishes control over the NordVPN application.
        - Disconnects from any pre-existing VPN connection to ensure a clean state.
        - Records your initial public IP address to verify future changes.
        - Fetches and prepares the initial list of servers that match your
          configured criteria.

        This method must be called once before you can use `rotate()` or `terminate()`.
        """
        print("\n\x1b[1m\x1b[36m--- Starting VPN Session ---\x1b[0m")
        
        self._is_session_active = True
        if self._controller_type:
            self._controller = self._controller_type(self.settings.exe_path)
            self._controller.disconnect()
        else:
            raise ConfigurationError("No VPN controller available for your platform")
        
        print("\x1b[33mWaiting 3s for network adapter to settle...\x1b[0m")
        time.sleep(3)
        
        session_data = self.api_client.get_current_ip_info()
        self._last_known_ip = session_data.get("ip")
        self._session_coordinates = {
            "latitude": session_data.get("latitude"),
            "longitude": session_data.get("longitude")
        }

        self._prune_cache()

        # Set self._refresh_interval and self._current_limit based on connection criteria.
        self._apply_connection_settings()

        if self._current_limit >= 0:
            self._fetch_and_build_pool()
            print(f"\x1b[32mSession started and server pool initialized. Ready to rotate.\x1b[0m")
        else:
            print("\x1b[32mSession started in 'special' mode. Ready to rotate.\x1b[0m")
        self._session_connections = 0  # Reset connection count at session start
    
    def rotate(self, next_location: bool = False):
        """
        Connects to a new NordVPN server based on the configured settings.

        This is the primary action method. It selects a new server that matches
        the criteria defined during setup (e.g., specific country, city, server type)
        and connects to it. It ensures the new server has not been used recently,
        unless no other servers are available.

        Prerequisites:
            `start_session()` must be called before using this method.

        Args:
            next_location (bool, optional): If `True`, forces the switcher to
                move to the next country or city in the sequence, even if the current
                pool is not exhausted. This only works if at least one connection
                was made during the session; otherwise, the first country/city is not skipped.
                This parameter only has an effect if the connection setting was set to 'country' or 'city'
                with multiple countries/cities configured. Defaults to `False`.

        Raises:
            ConfigurationError: If the session has not been started.
            NoServersAvailableError: If no suitable servers can be found that
                match the defined criteria.
            NordVpnConnectionError: If the connection to the new server fails
                or cannot be verified.
        """
        if not self._is_session_active:
            raise ConfigurationError("Session not started. Please call start_session() first.")

        print(f"\n\x1b[34m[{time.strftime('%H:%M:%S', time.localtime())}] Rotation started...\x1b[0m")

        self._prune_cache()

        # Handle manual switching
        if next_location:
            # Only allow next_location/city switch if at least one connection was made in this session
            if self._session_connections > 0:
                switch_result = self._handle_sequential_country_switch()
                if switch_result in ['country', 'city']:
                    # Try to restore cached pool state for the new location
                    if not self._restore_pool_state():
                        # Cache was not available or stale, fetch fresh servers
                        print(f"\x1b[36mInfo: Switching to the next {switch_result} in the sequence. Fetching servers...\x1b[0m")
                        self._fetch_and_build_pool()
                    else:
                        print(f"\x1b[36mInfo: Switched to the next {switch_result} in the sequence. Using cached pool.\x1b[0m")
                else:
                    print("\x1b[33mWarning: 'next_location=True' was ignored. This feature is only available for the 'country' or 'city' setting with multiple countries/cities configured.\x1b[0m")
            else:
                print("\x1b[33mInfo: 'next_location=True' ignored because no connection has been made yet in this session.\x1b[0m")

        # Handle special server rotation separately
        if self.settings.connection_criteria.get("main_choice") == "special":
            self._handle_special_rotation()
            return
        
        if (time.time() - self._pool_timestamp) > self._refresh_interval and self._refresh_interval > 0:
            print(f"\x1b[36mInfo: Server data is older than {self._refresh_interval // 3600}h. Refreshing pool...\x1b[0m")
            self._fetch_and_build_pool(increase_limit=False)
            
        target_server = self._get_next_server()

        logging_name = f"'{target_server['name']}'"
        if target_server.get('locations'):
            for loc in target_server['locations']:
                country_info = loc.get('country', {})
                city_info = country_info.get('city', {})
                if city_info and 'name' in city_info:
                    logging_name += f" ({city_info['name']})"
                    break

        try:
            self._controller.connect(target_server['name'])
            self._verify_connection(logging_name)
            self._session_connections += 1
        except NordVpnConnectionError as e:
            ui.display_critical_error(str(e))
            raise # Re-raise the exception after informing the user

        # On success, update cache and save
        self.settings.used_servers_cache[target_server['id']] = time.time()
        self.settings.save(self.settings_path)

    def terminate(self, close_app: bool = False):
        """
        Gracefully terminates the VPN rotation session.

        This method should be called when you are finished with the VPN switcher.
        It performs two main actions:
        1. Disconnects from the current NordVPN server.
        2. Saves the final session state, including the cache of recently used
           servers, to your settings file.

        Args:
            close_app (bool, optional): If `True`, closes the NordVPN process and
                its GUI entirely after disconnecting. Defaults to `False`.
        """
        if not self._controller:
            print("\x1b[33mSession was not active. Nothing to terminate.\x1b[0m")
            return
            
        self._controller.disconnect()
        self.settings.save(self.settings_path)
        self._is_session_active = False
        # Clear the in-memory server pool cache to free memory
        self._server_pool_cache.clear()
        
        if close_app:
            self._controller.close()
        print(f"\x1b[32mSession terminated. Final state saved to '{self.settings_path}'.\x1b[0m\n")

    # --- Private Helper Methods ---

    def _load_or_create_settings(self, force_setup: bool, cache_expiry_hours: int, custom_exe_path: str = None) -> RotationSettings:
        """
        Loads settings from a file or creates new ones via an interactive setup.

        If a settings file exists at `self.settings_path` and `force_setup` is False,
        it loads the settings from that file. Otherwise, it launches the
        interactive UI to guide the user through creating a new configuration.

        Args:
            force_setup (bool): If True, forces the interactive setup to run even
                if a settings file exists.
            cache_expiry_hours (int): The number of hours to use for the server
                cache expiry if a new configuration is created.
            custom_exe_path (str, optional): A custom path to the NordVPN
                executable. If provided, this path is used instead of attempting
                to find it automatically.

        Returns:
            RotationSettings: An instance of the settings class, either loaded
                from a file or newly created.

        Raises:
            ConfigurationError: If the user-guided setup fails.
            SystemExit: If the user cancels the setup process.
        """
        if not force_setup and os.path.exists(self.settings_path):
            print(f"\n\x1b[36mLoading existing settings from '{self.settings_path}'...\x1b[0m")
            return RotationSettings.load(self.settings_path)

        # If a custom path is provided, use it; otherwise, try to find automatically
        if custom_exe_path and os.path.exists(custom_exe_path):
            exe_path = custom_exe_path
        else:
            exe_path = find_nordvpn_executable()
        try:
            criteria = ui.get_user_criteria(self.api_client)
        except SystemExit as e:
            # Catch the exit and re-raise to cleanly terminate the program
            raise SystemExit(e)
        except Exception as e:
            # Catch other errors and provide context
            raise ConfigurationError(f"Failed to create configuration: {e}")

        settings = RotationSettings(
            exe_path=exe_path,
            connection_criteria=criteria,
            cache_expiry_seconds=cache_expiry_hours * 3600
        )

        if criteria.get('main_choice', '').startswith('custom_region') and criteria.get('strategy') == 'recommended':
            self._preflight_check_custom_region(settings)

        settings.save(self.settings_path)
        print(f"\n\x1b[32mSettings saved to '{self.settings_path}'.\x1b[0m")
        return settings

    def _clear_server_cache(self):
        """
        Clears the `used_servers_cache` and persists the cleared settings if requested.
        """
        # If settings aren't loaded for some reason, do nothing.
        if getattr(self, 'settings', None) is None:
            return

        if self.settings.used_servers_cache:
            self.settings.used_servers_cache = {}
            try:
                self.settings.save(self.settings_path)
            except Exception:
                print(f"\x1b[33mWarning: Failed to save cleared settings to '{self.settings_path}'.\x1b[0m")
            else:
                print(f"\x1b[32mInfo: Cleared used servers cache and saved to '{self.settings_path}'.\x1b[0m")
        else:
            print(f"\x1b[36mInfo: Used servers cache already empty.\x1b[0m")

    def _save_pool_state(self):
        """
        Saves the current pool state to the cache indexed by the current country/city index.
        
        This caches the following state variables:
        - _current_server_pool: The list of available servers
        - _pool_timestamp: When the pool was last fetched
        - _current_limit: The API fetch limit used
        - _last_raw_server_count: The count of servers before filtering
        - _servers_available_from_cache_count: The count of servers newly available from cache
        
        This allows us to restore the exact state when switching back to a previously
        used location without re-fetching the data.
        """
        self._server_pool_cache[self._current_country_index] = {
            'pool': self._current_server_pool.copy(),
            'timestamp': self._pool_timestamp,
            'limit': self._current_limit,
            'raw_count': self._last_raw_server_count,
            'newly_available': self._servers_available_from_cache_count,
        }

    def _restore_pool_state(self) -> bool:
        """
        Restores the pool state from the cache if available and still valid.
        
        Returns:
            bool: True if the pool state was successfully restored, False otherwise.
            
        A cached pool is considered valid if:
        - It exists in the cache for the current country/city index
        - The cached timestamp is still within the refresh interval
        
        If the pool is restored, the following variables are updated:
        - _current_server_pool
        - _pool_timestamp
        - _current_limit
        - _last_raw_server_count
        - _servers_available_from_cache_count
        """
        if self._current_country_index not in self._server_pool_cache:
            return False
        
        cached = self._server_pool_cache[self._current_country_index]
        now = time.time()
        
        # Check if the cached pool is still valid (within refresh interval)
        if (now - cached['timestamp']) <= self._refresh_interval:
            self._current_server_pool = cached['pool'].copy()
            self._pool_timestamp = cached['timestamp']
            self._current_limit = cached['limit']
            self._last_raw_server_count = cached['raw_count']
            self._servers_available_from_cache_count = cached['newly_available']
            return True
        
        return False

    def _preflight_check_custom_region(self, settings: RotationSettings):
        """
        Validates and optimizes settings for a new 'custom_region'.

        This one-time check fetches all recommended servers to:
        1. Ensure the user's custom region (inclusion/exclusion list) returns at
           least one server.
        2. Calculate an optimal 'limit' parameter for API calls. This is done
           by finding the index of the 50th (or last, if fewer) server from the
           filtered list within the original, unfiltered list. This ensures future
           API calls fetch just enough servers to cover the top 50 valid ones.

        The calculated limit is saved back into `settings.connection_criteria`.

        Args:
            settings (RotationSettings): The newly created settings object to
                validate and modify.

        Raises:
            NoServersAvailableError: If the specified custom region yields no
                recommended servers.
        """
        print("\n\x1b[36mInfo: Performing a one-time check on your custom region...\x1b[0m")

        params = self.api_client._DEFAULT_SERVER_FIELDS.copy()
        params.update({
            "limit": 0,
            "filters[servers_groups][id]": 11,
            "filters[servers_technologies][id]": 35,
            "filters[servers_technologies][pivot][status]": "online",
        })

        # If we're validating a custom city-based region, request city fields from the API
        if settings.connection_criteria.get('main_choice') == 'custom_region_city':
            params.update({
                "fields[servers.locations.country.city.id]": ""
            })

        all_recs = self.api_client.get_recommendations(params)

        filtered_recs, id_counts = self._filter_servers_by_custom_region(all_recs, settings, counting=True)

        # If the user created an inclusion custom region, warn about any empty entries
        if settings.connection_criteria.get('main_choice') == 'custom_region_in':
            for country_id in settings.connection_criteria.get('country_ids', []):
                if id_counts.get(country_id, 0) == 0:
                    print(f"\x1b[33mWarning: Country ID {country_id} has no recommended servers.\x1b[0m")

        # If the user created a custom city region, check by city ids
        if settings.connection_criteria.get('main_choice') == 'custom_region_city':
            for city_id in settings.connection_criteria.get('city_ids', []):
                if id_counts.get(city_id, 0) == 0:
                    print(f"\x1b[33mWarning: City ID {city_id} has no recommended servers.\x1b[0m")

        if not filtered_recs:
            print("\x1b[33mWarning: Your custom region has no servers recommended by the NordVPN algorithm. Please try again using the 'Randomized by load' strategy.\x1b[0m")
            raise NoServersAvailableError("Your custom region has no recommended servers. Please try again using the 'Randomized by load' strategy.")
        
        target_server = filtered_recs[min(49, len(filtered_recs) - 1)]
        try:
            original_index = [s['id'] for s in all_recs].index(target_server['id'])
            settings.connection_criteria['custom_limit'] = original_index + 1
        except ValueError:
            settings.connection_criteria['custom_limit'] = 50

        print(f"\x1b[32mSuccess! Your custom region has {len(filtered_recs)} recommended servers.\x1b[0m")

    def _prune_cache(self):
        """
        Removes expired server entries from the `used_servers_cache`.

        This method iterates through the `used_servers_cache` and removes any
        server IDs whose last-used timestamp is older than `cache_expiry_seconds`.
        It stores the count of pruned servers in `_servers_available_from_cache_count`,
        signaling that a server pool refresh might yield new results.
        """
        now = time.time()
        initial_cache_size = len(self.settings.used_servers_cache)
        
        expired_keys = [
            k for k, v in self.settings.used_servers_cache.items()
            if (now - v) > self.settings.cache_expiry_seconds
        ]
        
        if expired_keys:
            for key in expired_keys:
                del self.settings.used_servers_cache[key]
            
            pruned_count = initial_cache_size - len(self.settings.used_servers_cache)
            if pruned_count > 0:
                print(f"\x1b[36mInfo: Pruned {pruned_count} expired servers from cache. They are now available for rotation.\x1b[0m")
                self._servers_available_from_cache_count += pruned_count

    def _transform_v2_response_to_v1_format(self, response_v2: dict) -> list:
        """
        Transforms the v2 API server response format to the v1 format.

        The v2 response separates servers and locations. This function creates a 
        lookup map for locations and then reconstructs the server list with
        embedded location data, matching the v1 structure.

        The v2 API response may include city information if the appropriate fields
        were requested. This method preserves that city data in the transformed
        structure to support city-based filtering.

        Args:
            response_v2: The dictionary response from the get_servers_v2 API call.

        Returns:
            A list of server dictionaries in the v1 format. The 'locations' field
            contains location objects with nested country and city data matching
            the v1 structure used by filtering functions.
        """
        if not response_v2 or 'servers' not in response_v2 or 'locations' not in response_v2:
            return []

        # Create a lookup map for locations by their ID for efficient access.
        # E.g., {367: {"country": {...}, "id": 367}, ...}
        locations_by_id = {loc['id']: loc for loc in response_v2['locations']}
        
        transformed_servers = []
        for server_data in response_v2['servers']:
            # For each server, find its full location objects using the lookup map.
            # Use a list comprehension for a concise and Pythonic way to build the list.
            # The `if loc_id in locations_by_id` check adds robustness.
            server_locations = []
            for loc_id in server_data.get('location_ids', []):
                if loc_id not in locations_by_id:
                    continue
                    
                location = locations_by_id[loc_id].copy()
                
                # Ensure city data is properly nested under country if present
                if 'country' in location and 'city' in location['country']:
                    city_data = location['country']['city']
                    if isinstance(city_data, dict) and 'id' in city_data:
                        # City data is already properly structured
                        server_locations.append(location)
                        continue
                
                # If no proper city data, ensure at least country data is preserved
                if 'country' in location:
                    server_locations.append(location)
            
            # Construct the new server dictionary in the v1 format.
            server_dict = {
                'id': server_data.get('id'),
                'name': server_data.get('name'),
                'load': server_data.get('load'),
                'locations': server_locations,  # Now includes properly nested city data
            }
            
            # Preserve the groups field from v2 response if present.
            # This is used to filter servers by group ID (e.g., region filtering).
            if 'group_ids' in server_data:
                server_dict['groups'] = [{'id': gid} for gid in server_data.get('group_ids', [])]
            
            transformed_servers.append(server_dict)
            
        return transformed_servers

    def _fetch_and_build_pool(self, increase_limit: bool = False):
        """
        Fetches, filters, and sorts servers to populate the active server pool.

        This is the core data-gathering method. It prepares API parameters based
        on user settings, calls the appropriate NordVPN API endpoint, and then
        processes the results. The processing includes filtering out servers that
        are high-load or in the recently-used cache, and then sorting them
        according to the selected strategy ('recommended' or 'randomized_load').

        If the pool is empty and a sequential country rotation is configured, it
        may switch to the next country and recursively call itself.

        Args:
            increase_limit (bool, optional): If True, the API 'limit' parameter
                is increased before fetching, in an attempt to find more servers
                when the initial pool is exhausted. Defaults to False.
        """
        if increase_limit:
            self._handle_limit_increase()

        api_params = self._prepare_api_params()
        
        servers = []
        if self.settings.connection_criteria.get('strategy') == 'recommended':
            servers = self.api_client.get_recommendations(api_params)
        else:
            response_v2 = self.api_client.get_servers_v2(api_params)
            servers = self._transform_v2_response_to_v1_format(response_v2)
            
        # Check if the API returned the same number of servers as last time.
        # This correctly detects when we've exhausted a country's list.
        if increase_limit and len(servers) == self._last_raw_server_count:
            switch_result = self._handle_sequential_country_switch()
            if switch_result in ['country', 'city']:
                print(f"\x1b[36mInfo: Exhausted servers for current {switch_result}. Fetching servers and switching to next {switch_result}...\x1b[0m")
                # Reset the raw server count before the recursive call for the new location
                self._last_raw_server_count = -1 
                return self._fetch_and_build_pool() # Recursive call for new location
            else:
                self._current_server_pool = [] # Truly exhausted
                return

        # Store the count of servers before filtering.
        self._last_raw_server_count = len(servers)

        self._current_server_pool = self._filter_and_sort_servers(servers)
        self._pool_timestamp = time.time()
        self._servers_available_from_cache_count = 0
        
        # If the pool is empty after filtering, recursively fetch with increased limit
        # This is especially important for region mode where group ID filtering may
        # result in an empty pool that can be refilled with more servers.
        if not self._current_server_pool and not increase_limit:
            print(f"\x1b[36mInfo: Server pool is empty after filtering. Fetching more servers...\x1b[0m")
            return self._fetch_and_build_pool(increase_limit=True)
    
    def _get_next_server(self) -> Dict:
        """
        Retrieves the next available and valid server for connection.

        This method follows a multi-stage process to find a suitable server:
        1.  It first tries to pop a server from the live `_current_server_pool`.
        2.  If the pool is empty, it triggers `_fetch_and_build_pool` to refill it,
            potentially with an increased server limit or from newly available
            cached servers.
        3.  For each candidate server, it fetches its latest details to validate
            that it is online and has a low load.
        4.  If the live pool is completely exhausted and cannot be refilled, it
            falls back to the `used_servers_cache`, starting with the least
            recently used server.
        5.  It validates the cached server similarly, though with a slightly more
            lenient load tolerance.

        Returns:
            Dict: The dictionary containing details of the validated server.

        Raises:
            NoServersAvailableError: If both the live pool and the cache are
                exhausted and no valid, online server can be found.
        """
        # Helper to fetch & validate one server by ID
        def _fetch_and_validate(server_id: str, allowed_load: int = 50) -> Dict:
            details = self.api_client.get_server_details(server_id)
            if not details:
                return None
            srv = details[0]
            if srv.get('load', 100) >= allowed_load:
                return None
            if srv.get('status') != 'online':
                return None
            return srv

        # First sweep: live pool
        while True:
            if not self._current_server_pool:
                # refill logic
                if self._servers_available_from_cache_count > 10:
                    print(f"\x1b[36mInfo: Server pool is empty, but {self._servers_available_from_cache_count} servers expired from cache. Refetching...\x1b[0m")
                    self._fetch_and_build_pool(increase_limit=False)
                else:
                    print("\x1b[36mInfo: Server pool is empty. Attempting to fetch more servers...\x1b[0m")
                    self._fetch_and_build_pool(increase_limit=True)

            # still empty?
            if not self._current_server_pool:
                break

            candidate = self._current_server_pool.pop(0)
            server_id = candidate['id']
            new_server = _fetch_and_validate(server_id)
            if new_server:
                return new_server
            # otherwise loop to next candidate

        # --- live pool exhausted, try cache once ---
        if not self.settings.used_servers_cache:
            raise NoServersAvailableError("Server pool is exhausted and the cache is empty. Cannot rotate.")

        print("\x1b[91mCRITICAL: No new servers available. Falling back to the least-recently-used server from cache.\x1b[0m")
        print("\x1b[93mIt is highly recommended to clear the cache or select settings with more servers.\x1b[0m")
        # sorted by oldest timestamp
        for server_id in sorted(self.settings.used_servers_cache, key=self.settings.used_servers_cache.get):
            new_server = _fetch_and_validate(server_id, allowed_load=70)  # Allow higher load for cache fallback
            if new_server:
                return new_server

        # nothing left
        raise NoServersAvailableError("Exhausted both live pool and cache without finding a good server.")
    
    def _prepare_api_params(self) -> Dict:
        """Translates user criteria into a dictionary of API parameters."""
        crit = self.settings.connection_criteria
        main_choice = crit.get("main_choice")

        params = self.api_client._DEFAULT_SERVER_FIELDS.copy()
        params.update({
            "limit": self._current_limit,
            "filters[servers_technologies][id]": 35,
            "filters[servers_technologies][pivot][status]": "online",
        })

        match main_choice:
            case "country":
                params["filters[country_id]"] = crit["country_ids"][self._current_country_index]
                params["filters[servers_groups][id]"] = 11

            case "city":
                params["filters[country_city_id]"] = crit["city_ids"][self._current_country_index]
                params["filters[servers_groups][id]"] = 11

            case "region":
                params["filters[servers_groups][id]"] = crit["group_id"]
                params["fields[servers.groups.id]"] = ""

            case "custom_region_city":
                params["filters[servers_groups][id]"] = 11
                params["fields[servers.locations.country.city.id]"] = ""

            case m if m in ["worldwide", "custom_region_in", "custom_region_ex"]:
                params["filters[servers_groups][id]"] = 11

            case _:
                pass

        if crit.get('strategy') == 'recommended':
            params["coordinates[latitude]"] = self._session_coordinates["latitude"]
            params["coordinates[longitude]"] = self._session_coordinates["longitude"]

        return params

    def _filter_and_sort_servers(self, servers: List[Dict]) -> List[Dict]:
        """Filters a raw server list by load, cache, and custom region criteria, then sorts it."""
        now = time.time()
        filtered = []
        for server in servers:
            if server.get("load", 100) > 50:
                continue
            
            server_id = server['id']
            if server_id in self.settings.used_servers_cache:
                if (now - self.settings.used_servers_cache[server_id]) < self.settings.cache_expiry_seconds:
                    continue
            
            # If the server has a 'groups' field (from v2 API region filtering),
            # verify that group ID 11 (standard VPN servers) is included.
            # This ensures we don't get non-standard servers in region mode.
            if 'groups' in server:
                group_ids = [g.get('id') for g in server.get('groups', [])]
                if 11 not in group_ids:
                    continue
            
            filtered.append(server)
        
        # Apply custom region filter if necessary
        crit = self.settings.connection_criteria
        if crit.get('main_choice', '').startswith('custom_region'):
            filtered, _ = self._filter_servers_by_custom_region(filtered, self.settings)

        # Sort based on strategy
        if crit.get('strategy') == "randomized_load":
            buckets = {}
            for server in filtered:
                load = server.get("load", 100)
                bucket_key = 0 if load < 20 else (load // 10) * 10
                if bucket_key not in buckets: buckets[bucket_key] = []
                buckets[bucket_key].append(server)
            
            sorted_servers = []
            for key in sorted(buckets.keys()):
                random.shuffle(buckets[key])
                sorted_servers.extend(buckets[key])
            return sorted_servers
        
        return filtered # 'recommended' servers are already sorted by the API

    def _filter_servers_by_custom_region(self, servers: List[Dict], settings: RotationSettings, counting: bool = False) -> Tuple[List[Dict], Dict[int, int]]:
        """
        Filters a server list based on custom country inclusion/exclusion rules.

        This helper reads the `custom_region_in` or `custom_region_ex` rules from
        the settings and filters the provided list of servers accordingly. It
        can also optionally count the number of servers per country.

        Args:
            servers (List[Dict]): The list of servers to filter.
            settings (RotationSettings): The settings object containing the custom
                region criteria.
            counting (bool, optional): If True, the method will count servers
                per country ID before filtering. Defaults to False.

        Returns:
            Tuple[List[Dict], Dict[int, int]]: A tuple containing:
                - The filtered list of server dictionaries.
                - A dictionary mapping country IDs to their server counts
                  (only populated if `counting` is True).
        """
        crit = settings.connection_criteria
        exclude = crit['main_choice'] == "custom_region_ex"
        is_city_mode = crit['main_choice'] == 'custom_region_city'

        # custom_ids can be either country_ids or city_ids depending on mode
        custom_ids = crit['city_ids'] if is_city_mode else crit['country_ids']

        result_servers = []
        counts = {}

        for server in servers:
            # This logic assumes v1 server structure for location.
            loc = server.get('locations', [None])[0]
            if not loc:
                continue

            if is_city_mode:
                # city id is nested under country -> city
                city_id = loc.get('country', {}).get('city', {}).get('id')
                if city_id is None:
                    continue

                key = city_id
            else:
                country_id = loc.get('country', {}).get('id')
                if country_id is None:
                    continue

                key = country_id

            if counting:
                counts[key] = counts.get(key, 0) + 1

            if (exclude and key in custom_ids) or (not exclude and key not in custom_ids):
                continue

            result_servers.append(server)

        return result_servers, counts

    def _apply_connection_settings(self, override: dict = None):
        """
        Set `self._refresh_interval` and `self._current_limit` based on connection criteria.

        Parameters
        ----------
        override : dict, optional
            If provided, must contain the keys `'refresh_interval'` and `'current_limit'`. These values
            will be used directly instead of the defaults. E.g. `{'refresh_interval': 6, 'current_limit': 0}`

        Connection Criteria Table
        -------------------------
        | Strategy         | Scope               | Refresh (h) | Fetch (limit)  |
        |------------------|---------------------|-------------|----------------|
        | recommended      | country             | 1           | 50             |
        | randomized_load  | country             | 1           | 300            |
        | recommended      | region              | 1           | 50             |
        | randomized_load  | region              | 12          | 300            |
        | recommended      | custom_region_in    | 12          | custom_limit   |
        | recommended      | custom_region_ex    | 12          | custom_limit   |
        | randomized_load  | custom_region_in    | 12          | 0              |
        | randomized_load  | custom_region_ex    | 12          | 0              |
        | recommended      | worldwide           | 1           | 50             |
        | randomized_load  | worldwide           | 12          | 0              |
        | -                | special             | 0           | -1             |

        Notes
        -----
        - **recommended**: Fetch a low number of servers (`limit > 0`) since the first entries
          returned are already the best. We refresh these more frequently (shorter interval)
          because fetching is cheap and users expect top-performing servers.
        - **randomized_load**: Must fetch all available entries (`limit = 0`) to randomize them
          properly. We refresh less often (longer interval) because this returns many candidates;
          before connecting, we still check live load to ensure it's low.
        - `refresh=0` means never refresh; `limit=0` means fetch all available servers; `limit=-1` means fetch no servers.

        """
        # Shortcut override
        if override is not None:
            self._refresh_interval = override.get('refresh_interval') * 3600
            self._current_limit    = override.get('current_limit')
            return

        crit         = self.settings.connection_criteria
        strat        = crit.get('strategy')
        scope        = crit.get('main_choice')
        custom_limit = crit.get('custom_limit')

        # default fallback
        CONFIG = {
            ('recommended',     'country'):            (1, 50),
            ('randomized_load', 'country'):            (1, 300),
            ('recommended',     'city'):               (1, 50),
            ('randomized_load', 'city'):               (1, 300),
            ('recommended',     'region'):             (1, 50),
            ('randomized_load', 'region'):             (12, 300),
            ('recommended',     'custom_region_in'):   (12, custom_limit),
            ('recommended',     'custom_region_ex'):   (12, custom_limit),
            ('recommended',     'custom_region_city'): (12, custom_limit),
            ('randomized_load', 'custom_region_in'):   (12, 0),
            ('randomized_load', 'custom_region_ex'):   (12, 0),
            ('randomized_load', 'custom_region_city'): (12, 0),
            ('recommended',     'worldwide'):          (1, 50),
            ('randomized_load', 'worldwide'):          (12, 0),
            (None, 'special'):                         (0, -1),
        }

        # fallback is (12h, limit=0)
        refresh, limit = CONFIG.get((strat, scope), (12, 0))
        self._refresh_interval = refresh * 3600
        self._current_limit    = limit

    def _handle_limit_increase(self):
        """
        Increases the API fetch limit (`_current_limit`) for subsequent calls.

        This method is called when the server pool is exhausted, allowing the
        next API call to request a larger batch of servers. The increment size
        depends on the connection strategy. If the limit grows excessively large
        (>= 3000), it is set to 0 to signify "fetch all".
        """
        if self._current_limit == 0:
            return
        
        crit = self.settings.connection_criteria
        strat = crit.get('strategy')
        scope = crit.get('main_choice')

        if scope.startswith('custom_region'):
            limit_increase = 500
        elif strat == 'randomized_load':
            limit_increase = 300
        else:
            limit_increase = 50

        self._current_limit = self._current_limit + limit_increase
        if self._current_limit >= 3000:
            self._current_limit = 0
    
    def _handle_sequential_country_switch(self) -> str | bool:
        """
        Switches to the next country or city in a sequential rotation.
        
        This method saves the current pool state before switching so it can be
        restored later if the user switches back to this location. The restoration
        logic is handled by the caller (typically in rotate() with next_location=True).

        Returns:
            Union[str, bool]: Returns 'country' if switched to next country,
            'city' if switched to next city, or False if no switch occurred.
        """
        crit = self.settings.connection_criteria
        scope = crit.get('main_choice')

        if scope == 'country' and len(crit.get('country_ids', [])) > 1:
            # Save current state before switching
            self._save_pool_state()
            
            # Switch to next country
            self._current_country_index = (self._current_country_index + 1) % len(crit['country_ids'])
            self._apply_connection_settings()
            
            return 'country'

        if scope == 'city' and len(crit.get('city_ids', [])) > 1:
            # Save current state before switching
            self._save_pool_state()
            
            # Switch to next city
            self._current_country_index = (self._current_country_index + 1) % len(crit['city_ids'])
            self._apply_connection_settings()
            
            return 'city'

        return False

    def _handle_special_rotation(self):
        """
        Manages connection and rotation logic for special server groups.

        Unlike standard servers, special groups (e.g., "P2P", "Double VPN") are
        connected to by name, and the client app chooses the specific server.
        This method handles this by:
        1. Connecting to the chosen group (e.g., "P2P").
        2. Verifying the connection and retrieving the new IP address.
        3. If retries are enabled, it checks the new IP against the cache of
           used servers. If the IP is new, the rotation is a success. If the IP
           is already used, the process is retried up to a configured number of
           times (`retry_count`).
        4. If retries are disabled, it accepts the first successful connection
           without checking it against the cache.

        Raises:
            NordVpnConnectionError: If it fails to connect to a new, unused
                server after all retry attempts.
        """
        crit = self.settings.connection_criteria
        group_title = crit.get('group_title')
        consecutive_failures = 0
        disabled_retries = not (crit.get('retry_count', 1) > 0)

        for i in range(crit.get('retry_count', 1) + 1): # +1 to make the loop intuitive
            self._controller.connect(group_title, is_group=True)

            # --- Dynamic Delay Logic ---
            if consecutive_failures == 0:
                delays = [3, 5, 10]
            elif consecutive_failures == 1:
                delays = [3, 5]
            else:
                delays = [5]
            
            try:
                self._verify_connection(group_title, delays=delays)
                new_ip = self._last_known_ip
                consecutive_failures = 0 # Reset counter on a successful connection
            except NordVpnConnectionError as e:
                consecutive_failures += 1
                
                if consecutive_failures >= 5:
                    ui.display_critical_error("All consecutive connection attempts failed.")
                    raise NordVpnConnectionError("NordVPN app appears to be unresponsive.") from e
                else:
                    print(f"\x1b[91mConnection verification failed: {e}. Retrying...\x1b[0m")
                    continue

            # If retries are disabled, any successful connection is a success.
            # We still cache the IP for future runs, but we don't verify it against the cache now.
            if disabled_retries:
                if new_ip:
                    self.settings.used_servers_cache[new_ip] = time.time()
                    self.settings.save(self.settings_path)
                return

            # Check if the resulting IP is in our cache
            is_used_recently = False
            if new_ip in self.settings.used_servers_cache:
                if (time.time() - self.settings.used_servers_cache[new_ip]) < self.settings.cache_expiry_seconds:
                    is_used_recently = True

            if new_ip and not is_used_recently:
                self.settings.used_servers_cache[new_ip] = time.time()
                self.settings.save(self.settings_path)
                return # Success!
            
            # If we are here, the server was used recently or couldn't be identified
            if i < crit.get('retry_count', 1):
                print(f"\x1b[33mGot a previously used server. Retrying rotation ({i+1}/{crit.get('retry_count', 1)})...\x1b[0m")
        
        # If the loop completes without returning, all retries have failed.
        raise NordVpnConnectionError(f"Failed to get an unused special server for '{group_title}' after multiple retries.")

    def _verify_connection(self, target_name: str, delays: List[int] = [3, 5, 7, 10]):
        """
        Verifies a new connection is active, protected, and different from the
        last known IP. It uses a flexible list of delays for retries.

        Args:
            target_name (str): The name of the server/group for logging.
            delays (List[int]): A list of sleep durations (in seconds) to wait
                                between checks. First element is the initial wait.
        
        Raises:
            NordVpnConnectionError: If the connection cannot be verified after all retries.
        """
        # The number of checks we'll perform is one more than the number of delays.
        print(f"\x1b[33mWaiting {delays[0]}s before checking connection status...\x1b[0m")
        time.sleep(delays[0])

        num_checks = len(delays) - 1
        for i in range(num_checks):
            delay = delays[i+1]
            try:
                new_ip_info = self.api_client.get_current_ip_info()
                new_ip = new_ip_info.get("ip")
            except ApiClientError:
                print(f"\x1b[33mCould not fetch IP, network may be changing. Waiting {delay}s before re-checking (Attempt {i+1}/{num_checks})...\x1b[0m")
                time.sleep(delay)
                continue

            if new_ip and new_ip != self._last_known_ip and new_ip_info.get("protected"):
                print(f"\x1b[32m[{time.strftime('%H:%M:%S', time.localtime())}] Rotation successful!\x1b[0m")
                print(f"\x1b[32mConnected to {target_name}. New IP: {new_ip}\x1b[0m")
                self._last_known_ip = new_ip
                return # Success!
            
            print(f"\x1b[33mConnection not verified. Waiting {delay}s before re-checking (Attempt {i+1}/{num_checks})...\x1b[0m")
            time.sleep(delay)

        # If the loop completes without returning, all retries have failed.
        raise NordVpnConnectionError(f"Failed to verify connection to {target_name} after multiple attempts.")