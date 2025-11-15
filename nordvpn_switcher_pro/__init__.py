__version__ = "2.0.0"
__author__ = "Sebastian Hanisch"

from .core import VpnSwitcher
from .exceptions import (
    NordVpnSwitcherError,
    ConfigurationError,
    ApiClientError,
    NordVpnCliError,
    NordVpnConnectionError,
    NoServersAvailableError,
    UnsupportedPlatformError
)

__all__ = [
    # Core Class
    "VpnSwitcher",
    # Exceptions
    "NordVpnSwitcherError",
    "ConfigurationError",
    "ApiClientError",
    "NordVpnCliError",
    "NordVpnConnectionError",
    "NoServersAvailableError",
    "UnsupportedPlatformError"
]