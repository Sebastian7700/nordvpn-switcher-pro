class NordVpnSwitcherError(Exception):
    """Base exception for all errors raised by this library."""
    pass


class ConfigurationError(NordVpnSwitcherError):
    """Raised for configuration-related errors."""
    pass


class ApiClientError(NordVpnSwitcherError):
    """Raised when the NordVPN API returns an error or invalid data."""
    pass


class NordVpnCliError(NordVpnSwitcherError):
    """Raised when the NordVPN command-line tool fails."""
    pass


class NordVpnConnectionError(NordVpnSwitcherError):
    """Raised when a VPN connection attempt fails or times out."""
    pass


class NoServersAvailableError(NordVpnSwitcherError):
    """Raised specifically when no servers match the given criteria after fetching."""
    pass