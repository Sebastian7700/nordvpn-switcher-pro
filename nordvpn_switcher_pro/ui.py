import os
from typing import List, Dict, Union, Any
from .exceptions import ConfigurationError, ApiClientError

import questionary
from questionary import Choice


def clear_screen():
    """Clears the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def get_custom_style():
    """Returns a custom style for questionary prompts."""
    return questionary.Style([
        ('qmark', 'fg:#673ab7 bold'),       # Question mark
        ('question', 'bold'),               # Question text
        ('pointer', 'fg:#cc5454 bold'),      # Pointer to current choice
        ('highlighted', 'fg:#cc5454 bold'),  # Highlighted choice
        ('selected', 'fg:#ffffff bg:#673ab7 bold'), # Selected choice
        ('answer', 'fg:#f44336 bold'),      # Answer text
        ('separator', 'fg:#9e9e9e'),
        ('instruction', 'fg:#ffeb3b')
    ])


def prompt_main_menu() -> str:
    """Displays the main menu for connection type selection."""
    clear_screen()
    return questionary.select(
        "How would you like to connect?",
        choices=[
            Choice("To a specific Country (or a sequence of countries)", "country"),
            Choice("To a specific Region (e.g., Europe, The Americas)", "region"),
            Choice("To a random server worldwide (Standard servers only)", "worldwide"),
            Choice("To a Special Server Group (e.g., P2P, Double VPN)", "special"),
            Choice("Exit", "exit")
        ],
        style=get_custom_style(),
        instruction="(Use arrow keys to navigate, <enter> to select; Click into the terminal if needed)"
    ).ask()


def prompt_country_id_input(countries: List[Dict]) -> List[int]:
    """
    Displays a formatted list of countries and prompts the user to enter IDs.
    This is used for the 'country' main menu choice.
    """
    clear_screen()
    print("\x1b[1m\x1b[36m--- Select Country/Countries by ID ---\x1b[0m\n")

    sorted_countries = sorted(countries, key=lambda x: x['name'])
    
    header = f"{'ID':<5} | {'Country':<25} | {'Servers':<10}"
    print(header)
    print("-" * len(header))
    for country in sorted_countries:
        print(f"\x1b[96m{country['id']:<5}\x1b[0m | {country['name']:<25} | {country['serverCount']:<10}")
    print("-" * len(header))
    print("\n\x1b[33mServers will be rotated through each country sequentially (e.g., all of Germany, then all of France).\x1b[0m\n")

    # Use questionary.text for a robust input prompt
    ids_input = questionary.text(
        "Enter one or more country IDs from the list above, separated by commas:",
        validate=lambda text: True if text and all(part.strip().isdigit() for part in text.split(",") if part.strip()) else "Please enter only numbers and commas.",
        style=get_custom_style()
    ).ask()

    if not ids_input:
        return []

    return [int(part.strip()) for part in ids_input.split(",") if part.strip()]


def prompt_country_selection_multi(countries: List[Dict]) -> List[int]:
    """
    Displays an interactive, scrollable, multi-select list of countries.
    This is used for creating custom regions.
    """
    clear_screen()
    sorted_countries = sorted(countries, key=lambda x: x['name'])
    
    choices = [
        Choice(
            title=f"{c['name']:<25} ({c['serverCount']} servers)",
            value=c['id']
        ) for c in sorted_countries
    ]
    
    selected_ids = questionary.checkbox(
        "Select the countries for your custom region:",
        choices=choices,
        style=get_custom_style(),
        instruction="(Use arrow keys to navigate, <space> to select, <a> to toggle, <i> to invert, <enter> to confirm)"
    ).ask()
    
    return selected_ids if selected_ids else []


def prompt_group_selection(groups: List[Dict], group_type: str) -> Union[int, str, None]:
    """Displays a selection list for server groups (Regions or Special)."""
    clear_screen()

    # We only modify the logic for 'regions' to add custom options
    if group_type == 'regions':
        message = "Select a region:"
        # Fetch existing regions
        region_groups = [g for g in groups if g.get('type', {}).get('identifier') == 'regions']
        
        choices = [Choice(title=g['title'], value=g['id']) for g in region_groups]
        
        # Add a separator and the custom region options
        choices.append(questionary.Separator("--- Or Create a Custom Region ---"))
        choices.append(Choice(title="Include a specific list of countries", value="custom_region_in"))
        choices.append(Choice(title="Exclude a specific list of countries", value="custom_region_ex"))
        choices.append(Choice(title="Cancel", value="exit"))

    else: # For 'special' groups, the logic is simpler
        message = "Select a special server group:"
        choices = [
            Choice(
                title=f"{g['title']} ({g.get('serverCount', 'N/A')} servers)", 
                value=g['id']
            ) for g in groups # 'groups' is now the pre-filtered list
        ]
        choices.append(Choice("Cancel", "exit"))

    return questionary.select(
        message,
        choices=choices,
        style=get_custom_style(),
        instruction="(Use arrow keys to navigate, <enter> to select)"
    ).ask()


def prompt_connection_strategy() -> str:
    """Asks the user for their preferred server selection strategy."""
    clear_screen()
    return questionary.select(
        "How should servers be selected?",
        choices=[
            Choice(
                title="Best available (recommended for IP rotation)",
                value="recommended",
                description="Uses NordVPN's algorithm based on distance from you and server load."
            ),
            Choice(
                title="Randomized by load (recommended for Geo rotation)",
                value="randomized_load",
                description="Picks randomly from all of your selected servers, prioritizing lower load."
            ),
            Choice("Cancel", "exit")
        ],
        style=get_custom_style(),
        instruction="(Use arrow keys to navigate, <enter> to select)"
    ).ask()


def prompt_special_server_retry() -> int:
    """Asks if the tool should re-rotate if a used special server is connected."""
    clear_screen()
    print("\x1b[1m\x1b[36mDue to CLI limitations, we can't choose the special server we get connected to. But we can retry if we detect a used IP.\x1b[0m\n")

    answer = questionary.text(
        "If a used server is connected, should we retry? (Y=5 retries)",
        default="Y",
        style=get_custom_style(),
        instruction="(Y/N, or a number for max retries)"
    ).ask()

    if answer.lower() in ['y', 'yes']:
        return 5
    if answer.lower() in ['n', 'no']:
        return 0
    try:
        return int(answer)
    except (ValueError, TypeError):
        print("Invalid input, defaulting to 5 retries.")
        return 5


def get_user_criteria(api_client) -> Dict[str, Any]:
    """
    Guides the user through the entire interactive setup process.

    This function orchestrates all other prompt functions to build a complete
    criteria dictionary for the VpnSwitcher.

    Args:
        api_client: An instance of NordVpnApiClient to fetch data.

    Returns:
        A dictionary containing the user's final connection criteria.
    """
    def _handle_cancel(result):
        """Centralized cancellation handler."""
        if result is None or result == 'exit':
            raise SystemExit("Setup cancelled by user.")
        
    main_choice = prompt_main_menu()
    _handle_cancel(main_choice)

    criteria = {"main_choice": main_choice}

    # --- Country Selection Flow ---
    if main_choice == "country":
        countries = api_client.get_countries()
        selected_ids = prompt_country_id_input(countries)
        if not selected_ids:
            raise ConfigurationError("No countries were selected. Aborting setup.")
        criteria['country_ids'] = selected_ids
        strategy = prompt_connection_strategy()
        _handle_cancel(strategy)
        criteria['strategy'] = strategy
    
    # --- Region/Custom Region Flow ---
    elif main_choice == "region":
        groups = api_client.get_groups()
        region_choice = prompt_group_selection(groups, 'regions')
        _handle_cancel(region_choice)

        if isinstance(region_choice, str) and region_choice.startswith('custom_region'):
            criteria['main_choice'] = region_choice
            countries = api_client.get_countries()
            selected_ids = prompt_country_selection_multi(countries)
            if not selected_ids:
                raise ConfigurationError("No countries were selected for the custom region. Aborting setup.")
            criteria['country_ids'] = selected_ids
        else:
            criteria['group_id'] = region_choice
            
        strategy = prompt_connection_strategy()
        _handle_cancel(strategy)
        criteria['strategy'] = strategy
    
    # --- Worldwide Flow ---
    elif main_choice == "worldwide":
        strategy = prompt_connection_strategy()
        _handle_cancel(strategy)
        criteria['strategy'] = strategy

    # --- Special Server Flow ---
    elif main_choice == "special":
        all_groups = api_client.get_groups()
        
        print("\n\x1b[33mChecking availability of special server groups...\x1b[0m")
        special_groups = []
        for group in all_groups:
            # Filter for "Legacy category" and exclude "Standard VPN servers"
            if group.get('type', {}).get('identifier') == 'legacy_group_category' and \
            group.get('identifier') != 'legacy_standard':
                try:
                    count_data = api_client.get_group_server_count(group['id'])
                    if count_data.get('count', 0) > 0:
                        # Add the server count to the group object for display
                        group['serverCount'] = count_data.get('count')
                        special_groups.append(group)
                except ApiClientError:
                    # If the count endpoint fails for a group, just skip it
                    continue
        
        if not special_groups:
            raise ConfigurationError("Could not find any available special server groups at the moment.")
        
        # Now prompt the user with the filtered, available list
        group_id = prompt_group_selection(special_groups, 'special')
        _handle_cancel(group_id)
            
        group_details = next((g for g in all_groups if g['id'] == group_id), {})
        
        criteria['group_identifier'] = group_details.get('identifier')
        criteria['group_title'] = group_details.get('title')
        retry_count = prompt_special_server_retry()
        _handle_cancel(retry_count)
        criteria['retry_count'] = retry_count

    # Final validation to ensure a strategy was selected where needed
    if main_choice != 'special' and not criteria.get('strategy'):
        raise ConfigurationError("Connection strategy was not selected. Aborting setup.")

    return criteria


def display_critical_error(reason: str):
    """Displays a standardized critical error message for application failures."""
    print("\n" + "="*60)
    print("\x1b[91mCRITICAL ERROR: A VPN connection could not be established.\x1b[0m")
    print(f"\x1b[91mReason: {reason}\x1b[0m")
    print("\x1b[33mThis often happens if the NordVPN application is stuck or unresponsive.\x1b[0m")
    print("\x1b[33m\nRecommended Action:\x1b[0m")
    print("  1. Close NordVPN completely from your system tray (Right-click -> Quit).")
    print("  2. Restart your script.")
    print("\n\x1b[33mTo force-close from the command line, you can use:\x1b[0m")
    print("  taskkill /F /IM nordvpn.exe /T")
    print("="*60)