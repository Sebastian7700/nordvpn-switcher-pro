import os
import subprocess
import time
from typing import List

from .exceptions import ConfigurationError, NordVpnCliError

_CLI_IS_READY = False # Tracks if the NordVPN CLI is ready for commands.

def find_nordvpn_executable() -> str:
    """
    Finds the path to the NordVPN executable on Windows.

    Checks a list of common installation directories.

    Returns:
        The full path to NordVPN.exe.

    Raises:
        ConfigurationError: If the executable cannot be found.
    """
    potential_paths = [
        os.path.join(os.environ["ProgramFiles"], "NordVPN", "NordVPN.exe"),
        os.path.join(os.environ["ProgramFiles(x86)"], "NordVPN", "NordVPN.exe"),
    ]

    for path in potential_paths:
        if os.path.exists(path):
            return path

    raise ConfigurationError(
        "Could not find NordVPN.exe. Please ensure NordVPN is installed "
        "in a standard directory."
    )


class WindowsVpnController:
    """
    Controls the NordVPN Windows client via its command-line interface.
    """
    def __init__(self, exe_path: str):
        """
        Initializes the controller with the path to NordVPN.exe.

        Args:
            exe_path: The full path to the NordVPN executable.
        """
        if not os.path.exists(exe_path):
            raise ConfigurationError(f"Executable not found at path: {exe_path}")
        self.exe_path = exe_path

    def _wait_for_cli_ready(self, timeout: int = 45):
        """
        Waits for the NordVPN application and its CLI service to become responsive.
        """
        global _CLI_IS_READY

        if _CLI_IS_READY:
            return

        print(f"\n\x1b[33mWaiting for NordVPN to be ready (timeout: {timeout}s)...\x1b[0m")
        start_time = time.time()
        
        # Use Popen to launch the GUI without blocking. It might already be running.
        try:
            subprocess.Popen([self.exe_path])
            print("\x1b[33mNordVPN launch command issued. Checking service status...\x1b[0m")
            time.sleep(5)  # Initial grace period for the app to start.
        except Exception:
            pass # Fails silently if already running, which is fine.

        while time.time() - start_time < timeout:
            try:
                # Use a lightweight command that requires the background service.
                subprocess.run(
                    [self.exe_path, "--version"],
                    check=True,
                    capture_output=True,
                    timeout=5
                )
                print("\n\x1b[32mNordVPN CLI is ready.\x1b[0m")
                _CLI_IS_READY = True
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                print(".", end="", flush=True)
                time.sleep(2)
        
        raise NordVpnCliError(
            f"NordVPN CLI did not become responsive within {timeout} seconds. "
            "Please ensure the NordVPN application is running and logged in."
        )

    def _run_command(self, args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Helper method to execute a command with the NordVPN CLI."""
        self._wait_for_cli_ready()
        
        command = [self.exe_path] + args
        # print(f"\n\x1b[34mRunning NordVPN CLI command: {' '.join(command)}\x1b[0m")
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW # Hide the console window
            )
            return result
        except FileNotFoundError:
            raise ConfigurationError(f"Executable not found at path: {self.exe_path}")
        except subprocess.CalledProcessError as e:
            error_message = e.stderr.strip() if e.stderr else e.stdout.strip()
            raise NordVpnCliError(
                f"NordVPN CLI command '{' '.join(command)}' failed.\nError: {error_message}"
            )
        except subprocess.TimeoutExpired:
            raise NordVpnCliError(f"NordVPN CLI command timed out after {timeout} seconds.")

    def connect(self, target: str, is_group: bool = False):
        """
        Connects to a specific server or group.

        Args:
            target: The server name (e.g., 'Germany #123') or a group name.
            is_group: If True, uses the '-g' flag for group connection.
        """
        args = ["-c", "-g", f"{target}"] if is_group else ["-c", "-n", f"{target}"]
        print(f"\n\x1b[34mConnecting to '{target}'...\x1b[0m")
        self._run_command(args)

    def disconnect(self):
        """Disconnects from the VPN."""
        print("\n\x1b[34mDisconnecting from NordVPN...\x1b[0m")
        self._run_command(["-d"])
        
    def get_status(self) -> str:
        """Gets the current connection status from the CLI."""
        # This command is lightweight and good for status checks.
        result = self._run_command(["status"], timeout=10)
        return result.stdout