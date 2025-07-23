# NordVPN Switcher Pro

[![PyPI version](https://badge.fury.io/py/nordvpn-switcher-pro.svg)](https://badge.fury.io/py/nordvpn-switcher-pro)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Python versions](https://img.shields.io/pypi/pyversions/nordvpn-switcher-pro.svg)
![Platform](https://img.shields.io/badge/platform-Windows-blue.svg)

**NordVPN Switcher Pro** is a powerful Python library for automating NordVPN server connections on Windows. It is designed for developers and automation engineers who need reliable, criteria-based IP rotation for tasks like web scraping, data collection, and application testing.

The library provides a simple interface to a complex process:
- It uses a **stable, current NordVPN API (verified working as of July 2025)**.
- It features a **one-time interactive setup** to configure your rotation rules.
- Once configured, your scripts can run **headlessly without any user input**.
- It intelligently **caches used servers** to avoid reconnecting to the same IP.

The core focus is providing robust control over the NordVPN client. It does not include unrelated functionality.

## Key Features

- **Interactive Setup**: A guided command-line interface to create your connection settings.
- **Criteria-Based Rotation**: Connect to servers by Country, Region, a custom list of countries (inclusion or exclusion), or special groups (e.g., P2P, Double VPN).
- **Smart Caching**: Remembers recently used servers and avoids them for a configurable duration (default: 24 hours).
- **Resilient**: Gracefully handles connection failures and automatically falls back to the least-recently-used server if all fresh options are exhausted.
- **Headless Operation**: After the initial setup, it runs without any prompts, making it perfect for automated scripts and servers.
- **Modular Design**: Built with a clear separation between the core logic and the Windows controller, making it possible for the community to add support for Linux or macOS in the future.

> **Note on Platform Support**
> Currently, **NordVPN Switcher Pro** officially supports **Windows only**, as it relies on the NordVPN for Windows command-line interface. The project is designed to be extensible, and contributions for a `LinuxVpnController` or `MacVpnController` are welcome!

## Installation

```bash
pip install nordvpn-switcher-pro
```

## Quick Start

The easiest way to get started is to let the interactive setup guide you. The first time you run your script, a settings file (`nordvpn_settings.json`) will be created based on your answers.

Here is a basic example of rotating your IP address three times:

```python
from nordvpn_switcher_pro import VpnSwitcher
import time

# 1. Initialize the switcher.
# If "nordvpn_settings.json" doesn't exist, it will launch an
# interactive setup in your terminal to create it.
switcher = VpnSwitcher()

try:
    # 2. Start the session.
    # This prepares the switcher, disconnects from any current VPN,
    # and fetches the initial server list.
    switcher.start_session()

    for i in range(3):
        # 3. Rotate to a new server based on your settings.
        print(f"\n--- Rotation attempt {i+1} ---")
        switcher.rotate()

        # Do some work, e.g. scraping.
        print("Waiting 15 seconds before the next rotation...")
        time.sleep(15)

finally:
    # 4. Terminate the session.
    # This disconnects from the VPN and saves the updated server cache.
    print("\nTerminating session.")
    switcher.terminate()
```

## Connection Settings Explained

During the interactive setup, you'll be asked to define your rotation strategy. This involves two choices: **what** to connect to, and **how** servers are selected.

#### Connection Scope (What to connect to)

| Scope | Description |
| :--- | :--- |
| **Specific Country** | Rotates through servers in one or more specified countries. If multiple countries are given, it will exhaust all servers in the first country before moving to the next. |
| **Specific Region** | Connects to servers within a broad geographical region, like "The Americas" or "Europe". |
| **Custom Region (Include)** | Rotates only through servers in a custom list of countries you select. Ideal for targeting specific markets. |
| **Custom Region (Exclude)** | Rotates through servers in any country *except* for those in a custom list you provide. |
| **Worldwide** | Connects to any standard NordVPN server across the globe. |
| **Special Server Group** | Connects to a specific group like P2P, Double VPN, or Obfuscated. Since the app chooses the server, the switcher can be configured to retry if it gets a recently used IP. |

#### Server Selection Strategy (How to select)

- **Best available (recommended for IP rotation)**: Uses NordVPN's algorithm to find a server with the best combination of low latency (distance from you) and low load. This is ideal for quickly getting a new, high-performance IP.
- **Randomized by load (recommended for Geo rotation)**: Fetches *all* available servers for your chosen scope, groups them by load (0-20%, 20-30%, 30-40%, etc.), and picks a random server from the lowest-load bucket that still has unused servers. This provides greater server diversity.

## Visual Setup Examples

Here are a few GIFs demonstrating how to create different rotation strategies using the interactive setup.

<details>
<summary><strong>Example 1: Specific Countries (Germany & France)</strong></summary>

**Goal:** Create a rotation that uses servers first from Germany, then from France, using the fastest available servers in each.

**Configuration:**
- **Scope:** Specific Country
- **Countries:** Germany, France
- **Strategy:** Best available

![Setup for Germany and France](https://raw.githubusercontent.com/Sebastian7700/nordvpn-switcher-pro/main/assets/setup_country_de_fr.gif)

</details>

<details>
<summary><strong>Example 2: Custom Region (DACH - Germany, Austria, Switzerland)</strong></summary>

**Goal:** Create a rotation that targets the German-speaking "DACH" region for specific geo-targeting.

**Configuration:**
- **Scope:** Custom Region (Include)
- **Countries:** Germany, Austria, Switzerland
- **Strategy:** Randomized by load (for greater server diversity within the region)

![Setup for DACH region](https://raw.githubusercontent.com/Sebastian7700/nordvpn-switcher-pro/main/assets/setup_custom_region_dach.gif)

</details>

<details>
<summary><strong>Example 3: Worldwide Random</strong></summary>

**Goal:** Get a random IP address from any standard server in the world.

**Configuration:**
- **Scope:** Worldwide
- **Strategy:** Randomized by load

![Setup for Worldwide Random](https://raw.githubusercontent.com/Sebastian7700/nordvpn-switcher-pro/main/assets/setup_worldwide_random.gif)

</details>

<details>
<summary><strong>Example 4: Special P2P Group</strong></summary>

**Goal:** Connect to NordVPN's optimized P2P server group.

**Configuration:**
- **Scope:** Special Server Group
- **Group:** P2P

![Setup for P2P Special Group](https://raw.githubusercontent.com/Sebastian7700/nordvpn-switcher-pro/main/assets/setup_special_p2p.gif)

</details>

## Advanced Usage

### Multiple Rotation Strategies

You can maintain multiple, independent rotation strategies by creating different `VpnSwitcher` instances, each with its own settings file. This is useful for complex workflows that require different geographic targets.

In this example, we'll simulate downloading region-locked videos from the US and Japan.

```python
from nordvpn_switcher_pro import VpnSwitcher

# A dummy function to represent our download logic
def download_video(video):
    print(f"-> Downloading '{video['title']}'...")

# 1. Define the content to download, each with a region attribute.
videos_to_download = [
    {'title': 'American Psycho (Unrated Cut)', 'region': 'us'},
    {'title': 'Neon Genesis Evangelion (Remastered)', 'region': 'jp'},
    {'title': 'The Matrix (Original 1999 Release)', 'region': 'us'},
]

# 2. Initialize a VPN switcher for each region
us_switcher = VpnSwitcher(settings_path="us_settings.json")
jp_switcher = VpnSwitcher(settings_path="jp_settings.json")

# 3. Start sessions for both
us_switcher.start_session()
jp_switcher.start_session()

# 4. Process the videos, switching IPs as needed
for video in videos_to_download:
    region = video['region']
    
    try:
        if region == 'us':
            us_switcher.rotate()
            download_video(video)
        elif region == 'jp':
            jp_switcher.rotate()
            download_video(video)

    except Exception as e:
        print(f"!! Error downloading '{video['title']}': {e}")

# 5. Terminate sessions and save caches
us_switcher.terminate()
jp_switcher.terminate()

print("\nAll tasks complete.")

```

### Manual Rotation Control

If you have selected the **Specific Country** scope, for more fine-grained control, you can manually trigger a switch to the next country in your list using `rotate(next_country=True)`.

This parameter forces the switcher to advance to the next country in your configured sequence, even if you haven't used all the servers in the current country.

> **Note**: This feature only works if you have configured the switcher with the **Specific Country** scope and provided more than one country ID during setup.

#### Example: Forcing a Country Switch

Imagine you need to process 5 tasks in Germany, then immediately switch to France for the next 5 tasks.

```python
from nordvpn_switcher_pro import VpnSwitcher

# Assume this switcher was configured with Germany then France.
switcher = VpnSwitcher(settings_path='de_fr_settings.json')
switcher.start_session()

try:
    # --- Process Germany Tasks ---
    for i in range(5):
        switcher.rotate()
        print(f"Processing Germany task {i+1}...")

    # --- Manually switch to France for the next set of tasks ---
    print("\nFinished Germany tasks. Forcing switch to next country...")
    switcher.rotate(next_country=True)
    
    # --- Process France Tasks ---
    for i in range(5):
        # We don't need next_country=True here anymore
        switcher.rotate()
        print(f"Processing France task {i+1}...")

finally:
    switcher.terminate()
```

## How It Works

The library is composed of a few key components:
- `VpnSwitcher`: The main class that orchestrates the entire process.
- `WindowsVpnController`: A dedicated module that interacts with the `NordVPN.exe` command-line interface.
- `NordVpnApiClient`: A client that communicates with the public NordVPN API to fetch server lists and data.
- `RotationSettings`: A data class for saving and loading your configuration and server cache.

## Disclaimer

This project is an unofficial tool and is not affiliated with, endorsed by, or sponsored by NordVPN or Tefincom S.A. It is a personal project intended for educational and research purposes. Use it at your own risk.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.