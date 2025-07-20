import json
from dataclasses import dataclass, field, asdict
from typing import Dict, Any


class _SetEncoder(json.JSONEncoder):
    """A custom JSON encoder to handle 'set' objects."""
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        return json.JSONEncoder.default(self, obj)


@dataclass
class RotationSettings:
    """
    Stores all configuration and session state for VPN rotation.
    This object is designed to be lean and is saved to/loaded from a file.
    """
    # --- Static Configuration ---
    exe_path: str
    connection_criteria: Dict[str, Any] = field(default_factory=dict)
    cache_expiry_seconds: int = 86400  # 24 hours by default

    # --- Live Session State (will not be saved if empty) ---
    # Stores {server_id: last_used_timestamp} or {ip_address: last_used_timestamp}
    used_servers_cache: Dict[Any, float] = field(default_factory=dict)

    def save(self, filepath: str):
        """Saves the current settings and state to a JSON file."""
        with open(filepath, 'w') as f:
            # Use asdict to convert dataclass to dict for JSON serialization
            json.dump(asdict(self), f, cls=_SetEncoder, indent=4)
    
    @classmethod
    def load(cls, filepath: str) -> 'RotationSettings':
        """Loads settings and state from a JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        # JSON loads all dict keys as strings. We must convert them
        # back to integers for server IDs, while leaving IP addresses as strings.
        if 'used_servers_cache' in data:
            corrected_cache = {}
            for key, value in data['used_servers_cache'].items():
                try:
                    # This will succeed for server IDs like "12345"
                    new_key = int(key)
                except ValueError:
                    # This will handle IP addresses like "104.28.212.115"
                    new_key = key
                corrected_cache[new_key] = value
            data['used_servers_cache'] = corrected_cache

        return cls(**data)