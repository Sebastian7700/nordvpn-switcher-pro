import os
import subprocess
import time
import psutil
from typing import List

from .exceptions import ConfigurationError, NordVpnCliError

_CLI_IS_READY = False  # Tracks if the NordVPN CLI is ready for commands.


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
        "Could not find NordVPN.exe. Please install NordVPN in a standard directory "
        "or provide the correct path in VpnSwitcher(custom_exe_path='C:/Path/To/NordVPN.exe')."
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
        self.cwd_path = os.path.dirname(exe_path)

    def _wait_for_cli_ready(self, threshold_mb: int = 200, stability_window: int = 6, variance_pct: float = 1.0, timeout: int = 60):
        """
        Waits until the NordVPN GUI has fully started and stabilized.
        Stability is determined by both a memory threshold and minimal variance.
        Args:
            threshold_mb: Minimum memory usage in MB to consider the app started.
            stability_window: Number of consecutive samples to check for stability (check every 0.5s -> window of 6, means 3 seconds).
            variance_pct: Maximum allowed percentage variance in memory usage.
            timeout: Maximum time to wait in seconds.
        """
        global _CLI_IS_READY
        if _CLI_IS_READY:
            return

        print("\n\x1b[33mNordVPN launch command issued.\x1b[0m")

        # Launch GUI via Popen so it doesn’t block.
        try:
            subprocess.Popen(
                [self.exe_path],
                shell=True,
                cwd=self.cwd_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            print(f"\x1b[31mLaunch failed: {e}\x1b[0m")

        # steady-state detector
        print("\x1b[33mWaiting for NordVPN to become stable...\x1b[0m")
        start_time = time.time()
        samples = []

        while time.time() - start_time < timeout:
            for proc in psutil.process_iter(["name", "memory_info"]):
                if proc.info["name"] == "NordVPN.exe":
                    mem_mb = proc.info["memory_info"].rss / (1024 * 1024)
                    samples.append(mem_mb)
                    if len(samples) > stability_window:
                        samples.pop(0)

                    if mem_mb > threshold_mb and len(samples) == stability_window:
                        avg = sum(samples) / stability_window
                        max_dev = max(abs(s - avg) for s in samples)
                        if (max_dev / avg) * 100 <= variance_pct:
                            print("\x1b[32mNordVPN CLI is ready.\x1b[0m\n")
                            _CLI_IS_READY = True
                            return
            time.sleep(0.5)

        raise NordVpnCliError(
            f"NordVPN did not reach steady state within {timeout} seconds. "
            "Please ensure the application is running and logged in."
        )

    def _run_command(self, args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Executes a NordVPN CLI command after ensuring readiness."""
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
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.cwd_path,
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

    def close(self, force: bool = False):
        """
        Closes the NordVPN process entirely.

        Args:
            force: If True, kills the process immediately instead of attempting graceful termination.
        """
        global _CLI_IS_READY
        print("\n\x1b[34mClosing NordVPN...\x1b[0m")
        found = False

        for proc in psutil.process_iter(["name"]):
            if proc.info["name"] == "NordVPN.exe":
                found = True
                try:
                    if force:
                        proc.kill()
                    else:
                        proc.terminate()
                    proc.wait(timeout=5)
                    print("NordVPN.exe closed.")
                except psutil.TimeoutExpired:
                    if not force:
                        print("Process did not exit in time, forcing close.")
                        proc.kill()
                except Exception as e:
                    print(f"Failed to close NordVPN.exe: {e}")
                _CLI_IS_READY = False

        if not found:
            print("NordVPN.exe was not running.")
