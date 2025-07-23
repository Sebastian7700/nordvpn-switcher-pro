import os
import random
import time
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

    def __init__(self, settings_path: str = "nordvpn_settings.json", override: bool = False, cache_expiry_hours: int = 24):
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
            override (bool, optional): If `True`, forces the interactive setup
                to run, overwriting any existing settings file. Defaults to `False`.
            cache_expiry_hours (int, optional): The number of hours a server is
                considered "recently used". After this period, it becomes
                available for connection again. Defaults to 24.
        """
        self.settings_path = settings_path
        self.api_client = NordVpnApiClient()
        self.settings = self._load_or_create_settings(override, cache_expiry_hours)
        
        # --- Instance variables for an active session ---
        self._controller: WindowsVpnController | None = None
        self._session_coordinates: Dict | None = None
        self._last_known_ip: str | None = None
        self._current_server_pool: List[Dict] = []
        self._pool_timestamp: float = 0
        self._current_country_index: int = 0
        self._current_limit: int = 0
        self._last_raw_server_count: int = -1
        self._is_session_active: bool = False
        self._are_servers_newly_available_from_cache: bool = False
        self._refresh_interval: int = 3600
    
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
        self._controller = WindowsVpnController(self.settings.exe_path)
        self._controller.disconnect()
        
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
            print(f"\n\x1b[32mSession started and server pool initialized. Ready to rotate.\x1b[0m")
        else:
            print("\n\x1b[32mSession started in 'special' mode. Ready to rotate.\x1b[0m")
    
    def rotate(self, next_country: bool = False):
        """
        Connects to a new NordVPN server based on the configured settings.

        This is the primary action method. It selects a new server that matches
        the criteria defined during setup (e.g., specific country, server type)
        and connects to it. It ensures the new server has not been used recently,
        unless no other servers are available.

        Prerequisites:
            `start_session()` must be called before using this method.

        Args:
            next_country (bool, optional): If `True`, forces the switcher to
                move to the next country in the sequence, even if the current
                country's server pool is not exhausted. This parameter only has
                an effect if the connection setting was set to 'country' with
                multiple countries configured. Defaults to `False`.

        Raises:
            ConfigurationError: If the session has not been started.
            NoServersAvailableError: If no suitable servers can be found that
                match the defined criteria.
            NordVpnConnectionError: If the connection to the new server fails
                or cannot be verified.
        """
        if not self._is_session_active:
            raise ConfigurationError("Session not started. Please call start_session() first.")

        self._prune_cache()

        # Handle manual country switching
        if next_country:
            if self._handle_sequential_country_switch():
                print("\n\x1b[36mInfo: Switching to the next country in the sequence...\x1b[0m")
                # Force a pool refresh for the new country
                self._fetch_and_build_pool()
            else:
                print("\n\x1b[33mWarning: 'next_country=True' was ignored. This feature is only available for the 'country' setting with multiple countries configured.\x1b[0m")

        # Handle special server rotation separately
        if self.settings.connection_criteria.get("main_choice") == "special":
            self._handle_special_rotation()
            return
        
        if (time.time() - self._pool_timestamp) > self._refresh_interval and self._refresh_interval > 0:
            print(f"\n\x1b[36mInfo: Server data is older than {self._refresh_interval // 3600}h. Refreshing pool...\x1b[0m")
            self._fetch_and_build_pool(increase_limit=False)
            
        target_server = self._get_next_server()

        try:
            self._controller.connect(target_server['name'])
            self._verify_connection(target_server['name'])
        except NordVpnConnectionError as e:
            ui.display_critical_error(str(e))
            raise # Re-raise the exception after informing the user

        # On success, update cache and save
        self.settings.used_servers_cache[target_server['id']] = time.time()
        self.settings.save(self.settings_path)

    def terminate(self):
        """
        Gracefully terminates the VPN rotation session.

        This method should be called when you are finished with the VPN switcher.
        It performs two main actions:
        1. Disconnects from the current NordVPN server.
        2. Saves the final session state, including the cache of recently used
           servers, to your settings file.
        """
        if not self._controller:
            print("\x1b[33mSession was not active. Nothing to terminate.\x1b[0m")
            return
            
        self._controller.disconnect()
        self.settings.save(self.settings_path)
        self._is_session_active = False
        print(f"\n\x1b[32mSession terminated. Final state saved to '{self.settings_path}'.\x1b[0m")

    # --- Private Helper Methods ---

    def _load_or_create_settings(self, override: bool, cache_expiry_hours: int) -> RotationSettings:
        """
        Loads settings from a file or creates new ones via an interactive setup.

        If a settings file exists at `self.settings_path` and `override` is False,
        it loads the settings from that file. Otherwise, it launches the
        interactive UI to guide the user through creating a new configuration.

        Args:
            override (bool): If True, forces the interactive setup to run even
                if a settings file exists.
            cache_expiry_hours (int): The number of hours to use for the server
                cache expiry if a new configuration is created.

        Returns:
            RotationSettings: An instance of the settings class, either loaded
                from a file or newly created.

        Raises:
            ConfigurationError: If the user-guided setup fails.
            SystemExit: If the user cancels the setup process.
        """
        if not override and os.path.exists(self.settings_path):
            print(f"\n\x1b[36mLoading existing settings from '{self.settings_path}'...\x1b[0m")
            return RotationSettings.load(self.settings_path)

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
        
        all_recs = self.api_client.get_recommendations(params)
        
        filtered_recs, country_counts = self._filter_servers_by_custom_region(all_recs, settings, counting=True)

        if settings.connection_criteria.get('main_choice') == 'custom_region_in':
            for country_id in settings.connection_criteria['country_ids']:
                if country_counts.get(country_id, 0) == 0:
                    print(f"\x1b[33mWarning: Country ID {country_id} has no recommended servers.\x1b[0m")

        if not filtered_recs:
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
        It also sets the `_are_servers_newly_available_from_cache` flag to True
        if one or more servers were pruned, signaling that a server pool refresh
        might yield new results.
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
                print(f"\n\x1b[36mInfo: Pruned {pruned_count} expired servers from cache. They are now available for rotation.\x1b[0m")
                self._are_servers_newly_available_from_cache = True

    def _transform_v2_response_to_v1_format(self, response_v2: dict) -> list:
        """
        Transforms the v2 API server response format to the v1 format.

        The v2 response separates servers and locations. This function creates a 
        lookup map for locations and then reconstructs the server list with
        embedded location data, matching the v1 structure.

        Args:
            response_v2: The dictionary response from the get_servers_v2 API call.

        Returns:
            A list of server dictionaries in the v1 format.
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
            server_locations = [
                locations_by_id[loc_id] 
                for loc_id in server_data.get('location_ids', []) 
                if loc_id in locations_by_id
            ]
            
            # Construct the new server dictionary in the v1 format.
            transformed_servers.append({
                'id': server_data.get('id'),
                'name': server_data.get('name'),
                'load': server_data.get('load'),
                'locations': server_locations,  # This now matches the v1 structure
            })
            
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
            if self._handle_sequential_country_switch():
                print(f"\x1b[36mInfo: Exhausted servers for current country. Switching to next country.\x1b[0m")
                # Reset the raw server count before the recursive call for the new country.
                self._last_raw_server_count = -1 
                return self._fetch_and_build_pool() # Recursive call for new country
            else:
                self._current_server_pool = [] # Truly exhausted
                return

        # Store the count of servers before filtering.
        self._last_raw_server_count = len(servers)

        self._current_server_pool = self._filter_and_sort_servers(servers)
        self._pool_timestamp = time.time()
        self._are_servers_newly_available_from_cache = False
    
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
                if self._are_servers_newly_available_from_cache:
                    print("\x1b[36mInfo: Server pool is empty, but newly available servers were found in cache. Refetching...\x1b[0m")
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
        
        if main_choice == "country":
            params["filters[country_id]"] = crit["country_ids"][self._current_country_index]
            params["filters[servers_groups][id]"] = 11
        elif main_choice == "region":
            params["filters[servers_groups][id]"] = crit["group_id"]
        elif main_choice in ["worldwide", "custom_region_in", "custom_region_ex"]:
            params["filters[servers_groups][id]"] = 11

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
        custom_ids = crit['country_ids']
        exclude = crit['main_choice'] == "custom_region_ex"
        
        result_servers = []
        country_counts = {}

        for server in servers:
            # This logic assumes v1 server structure for location.
            country_id = server['locations'][0].get('country', {}).get('id')
            if country_id is None: continue

            if counting:
                if country_id in country_counts:
                    country_counts[country_id] += 1
                else:
                    country_counts[country_id] = 1

            if (exclude and country_id in custom_ids) or \
               (not exclude and country_id not in custom_ids):
                continue
            
            result_servers.append(server)
            
        return result_servers, country_counts

    def _apply_connection_settings(self, override: dict = None):
        """
        Set self._refresh_interval and self._current_limit based on connection criteria.

        Parameters
        ----------
        override : dict, optional
            If provided, must contain keys 'refresh_interval' and 'current_limit'. These values
            will be used directly instead of the defaults. E.g. {'refresh_interval': 6, 'current_limit': 0}

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
        - **randomized_load**: Must fetch all available entries (`limit=0`) to randomize them
          properly. We refresh less often (longer interval) because this returns many candidates;
          before connecting, we still check live load to ensure it's low.
        - refresh=0 means never refresh; limit=0 means fetch all available servers; limit=-1 means fetch no servers.

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
            ('recommended',     'region'):             (1, 50),
            ('randomized_load', 'region'):             (12, 300),
            ('recommended',     'custom_region_in'):   (12, custom_limit),
            ('recommended',     'custom_region_ex'):   (12, custom_limit),
            ('randomized_load', 'custom_region_in'):   (12, 0),
            ('randomized_load', 'custom_region_ex'):   (12, 0),
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
    
    def _handle_sequential_country_switch(self) -> bool:
        """Switches to the next country in a sequential rotation. Returns True if switched."""
        crit = self.settings.connection_criteria
        if crit.get('main_choice') == 'country' and len(crit.get('country_ids', [])) > 1:
            self._current_country_index = (self._current_country_index + 1) % len(crit['country_ids']) # Reset to 0 if at the end
            self._apply_connection_settings() # Reset limit for new country
            return True
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

    def _verify_connection(self, target_name: str, delays: List[int] = [3, 5, 7]):
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
                print(f"\n\x1b[32mRotation successful! Connected to '{target_name}'. New IP: {new_ip}\x1b[0m")
                self._last_known_ip = new_ip
                return # Success!
            
            print(f"\x1b[33mConnection not verified. Waiting {delay}s before re-checking (Attempt {i+1}/{num_checks})...\x1b[0m")
            time.sleep(delay)

        # If the loop completes without returning, all retries have failed.
        raise NordVpnConnectionError(f"Failed to verify connection to {target_name} after multiple attempts.")