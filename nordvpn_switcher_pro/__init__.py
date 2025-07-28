__version__ = "1.0.3"
__author__ = "Sebastian Hanisch"

from .core import VpnSwitcher
from .exceptions import (
    NordVpnSwitcherError,
    ConfigurationError,
    ApiClientError,
    NordVpnCliError,
    NordVpnConnectionError,
    NoServersAvailableError
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
    "NoServersAvailableError"
]