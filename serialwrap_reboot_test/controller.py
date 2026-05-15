"""Controller module for serialwrap-reboot-controller."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List

from .constants import SERIALWRAP_CMD, SERIALWRAP_EVENT_HANDLER


class ControllerError(Exception):
    """Controller operation error."""
    pass


class CommandRunner:
    """Run commands and return results.

    For `serialwrap` invocations, automatically inject `--timeout 30` before
    the subcommand unless the caller already specified `--timeout`. The CLI
    default (5 s) is not enough when the daemon is under load — high-volume
    RX (e.g. eth link bouncing during reboot churn) can keep the RPC queued
    long enough to miss the 5 s window, causing rc=2 and false-positive
    "daemon not running" errors on the first call after a quiet period.
    """

    SERIALWRAP_RPC_TIMEOUT_S = "30"

    def _augment_serialwrap(self, cmd: List[str]) -> List[str]:
        if not cmd:
            return cmd
        if not cmd[0].endswith("/serialwrap") and cmd[0] != "serialwrap":
            return cmd
        if "--timeout" in cmd or "--endpoint" in cmd:
            return cmd
        return [cmd[0], "--timeout", self.SERIALWRAP_RPC_TIMEOUT_S] + list(cmd[1:])

    def run(self, cmd: List[str], **kwargs) -> tuple[int, str, str]:
        """Run a command and return (returncode, stdout, stderr)."""
        cmd = self._augment_serialwrap(cmd)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            **kwargs
        )
        return result.returncode, result.stdout, result.stderr


def positive_int(value: str) -> int:
    """Parse a positive integer argument."""
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be greater than zero")
    return parsed


class RebootController:
    """Main controller for reboot testing."""
    
    def __init__(
        self,
        selector: str,
        runner: Optional[Any] = None,
        log_dir: Optional[Path] = None,
        hours_limit: Optional[int] = None,
        count_limit: Optional[int] = None,
        active_log: Optional[Path] = None
    ):
        """Initialize the controller.
        
        Args:
            selector: COM selector (e.g., COM0, COM1).
            runner: Command runner (defaults to real CommandRunner).
            log_dir: Directory for minicom logs (defaults to ~/b-log).
            hours_limit: Optional hours limit for stop condition.
            count_limit: Optional count limit for stop condition.
            active_log: Optional active minicom log path for testing.
            
        Raises:
            ValueError: If selector is invalid or contains path traversal.
        """
        # Validate selector format (must match ^COM[0-9]+$)
        if not re.match(r'^COM[0-9]+$', selector):
            raise ValueError(f"Invalid selector: {selector}. Must match pattern COM[0-9]+")
        
        self.selector = selector
        self.runner = runner or CommandRunner()
        # Ensure log_dir is a Path object
        if log_dir is None:
            self.log_dir = Path.home() / "b-log"
        elif isinstance(log_dir, str):
            self.log_dir = Path(log_dir)
        else:
            self.log_dir = log_dir
        self.hours_limit = hours_limit
        self.count_limit = count_limit
        self.active_log_path = active_log
        
        self.state_dir: Optional[Path] = None
        self.start_time = time.time()
        self.reboot_count = 0
        self._stop_requested = False
        self.last_action_time: Optional[float] = None
        self.sleep_fn = time.sleep  # Injectable for testing
        self.loop_delay = 10  # Configurable loop delay in seconds
        # Boot guard: after submitting a reboot the target passes through
        # u-boot, where ANY byte aborts the autoboot countdown and traps us
        # at the `=>` prompt. Suppress every UART-sending probe (self-test /
        # session recover / raw reset / raw reboot) for this many seconds
        # after the last action so probes never land in the u-boot window.
        # 90 s covers BGW720/prplOS bootmsg's ~46 s of "Delay complete(23 secs)"
        # plus u-boot + kernel + init headroom.
        self.boot_guard_seconds = 90.0
        
    def check_serialwrap_event_support(self) -> bool:
        """Check if serialwrap supports event subcommand."""
        returncode, stdout, stderr = self.runner.run(
            [SERIALWRAP_CMD, "event", "--help"]
        )
        return returncode == 0
    
    def check_daemon_status(self) -> bool:
        """Check if serialwrap daemon is running."""
        returncode, stdout, stderr = self.runner.run(
            [SERIALWRAP_CMD, "daemon", "status"]
        )
        return returncode == 0
    
    def send_marker_command(self) -> str:
        """Send marker command through serialwrap and return marker string.
        
        Raises:
            ControllerError: If marker submission fails.
        """
        # Generate unique marker
        run_id = str(uuid.uuid4())[:8]
        marker = f"__SW_REBOOT_TEST_{self.selector}_{run_id}__"
        
        # Submit echo command with marker
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "cmd", "submit",
            "--selector", self.selector,
            "--source", "agent:reboot-controller",
            "--mode", "line",
            "--cmd", f"echo {marker}"
        ])
        
        if returncode != 0:
            raise ControllerError(f"Failed to submit marker command: {stderr}")
        
        return marker
    
    def find_active_minicom_log(
        self,
        marker: str,
        max_age_seconds: int = 600,
        max_wait_seconds: float = 15.0,
        poll_interval_seconds: float = 0.5,
    ) -> Optional[Path]:
        """Find active minicom log containing marker.

        Polls because `serialwrap cmd submit --mode line` is async — the marker
        echo can take up to a few seconds to be transmitted to the target,
        echoed back, and captured by minicom into the log file.

        Args:
            marker: Marker string to search for.
            max_age_seconds: Maximum age for log file in seconds.
            max_wait_seconds: Maximum total time to wait for marker echo to land.
            poll_interval_seconds: Interval between rescans while waiting.

        Returns:
            Path to active log or None if not found within max_wait_seconds.
        """
        pattern = f"mini_{self.selector}_*.log"
        deadline = time.time() + max_wait_seconds

        while True:
            current_time = time.time()
            for log_file in self.log_dir.glob(pattern):
                try:
                    mtime = log_file.stat().st_mtime
                    if current_time - mtime > max_age_seconds:
                        continue
                except OSError as e:
                    print(f"WARNING: Cannot stat {log_file}: {e}", file=sys.stderr)
                    continue

                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            if marker in line:
                                return log_file
                except OSError as e:
                    print(f"WARNING: Cannot read {log_file}: {e}", file=sys.stderr)
                    continue

            if time.time() >= deadline:
                return None
            time.sleep(poll_interval_seconds)
    
    def derive_report_path(self, minicom_log: Path) -> Path:
        """Derive report path from minicom log name.
        
        Args:
            minicom_log: Path to minicom log file.
            
        Returns:
            Path to report file.
        """
        # Extract timestamp from minicom log name
        # mini_COM1_260506-152744.log -> event-triggered_COM1_260506-152744.md
        name = minicom_log.name
        prefix = f"mini_{self.selector}_"
        if name.startswith(prefix):
            timestamp = name[len(prefix):].replace('.log', '')
            report_name = f"event-triggered_{self.selector}_{timestamp}.md"
            return minicom_log.parent / report_name
        
        # Fallback
        return minicom_log.parent / f"event-triggered_{self.selector}.md"
    
    def create_run_state_directory(self) -> Path:
        """Create /tmp run-state directory.
        
        Returns:
            Path to created state directory.
            
        Raises:
            ValueError: If resolved path is not under /tmp.
        """
        pid = os.getpid()
        state_dir = Path(f"/tmp/serialwrap-reboot-test.{self.selector}.{pid}")
        
        # Verify resolved path is under /tmp (protect against symlink attacks)
        resolved = state_dir.resolve()
        if not str(resolved).startswith("/tmp/"):
            raise ValueError(f"State directory must be under /tmp, got: {resolved}")
        
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir
    
    def store_run_state(
        self,
        state_dir: Path,
        minicom_log: Path,
        report_path: Path
    ) -> None:
        """Store run state files.
        
        Args:
            state_dir: State directory path.
            minicom_log: Active minicom log path.
            report_path: Report file path.
        """
        (state_dir / "active_minicom_log.txt").write_text(str(minicom_log))
        (state_dir / "report_path.txt").write_text(str(report_path))
    
    def generate_event_rules(self) -> List[Dict[str, Any]]:
        """Generate event rule definitions.
        
        Returns:
            List of rule dictionaries.
        """
        owner = "agent-reboot-controller"
        rules = [
            {
                "schema_version": 1,
                "owner": owner,
                "name": "brcm-therm",
                "rule_id": f"{owner}.brcm-therm",
                "kind": "tool",
                "selectors": ["COM0", "COM1"],
                "pattern": {"kind": "contains", "value": "brcm-therm"},
                "handler": {"exec": [SERIALWRAP_EVENT_HANDLER]},
                "auto_enable_com_on_load": False
            },
            {
                "schema_version": 1,
                "owner": owner,
                "name": "link-down",
                "rule_id": f"{owner}.link-down",
                "kind": "tool",
                "selectors": ["COM0", "COM1"],
                "pattern": {"kind": "contains", "value": "Link is Down"},
                "handler": {"exec": [SERIALWRAP_EVENT_HANDLER]},
                "auto_enable_com_on_load": False
            },
            {
                "schema_version": 1,
                "owner": owner,
                "name": "pstate",
                "rule_id": f"{owner}.pstate",
                "kind": "tool",
                "selectors": ["COM0", "COM1"],
                "pattern": {"kind": "contains", "value": "pstate"},
                "handler": {"exec": [SERIALWRAP_EVENT_HANDLER]},
                "auto_enable_com_on_load": False
            },
            {
                "schema_version": 1,
                "owner": owner,
                "name": "kernel-panic",
                "rule_id": f"{owner}.kernel-panic",
                "kind": "tool",
                "selectors": ["COM0", "COM1"],
                "pattern": {"kind": "contains", "value": "Kernel panic"},
                "handler": {"exec": [SERIALWRAP_EVENT_HANDLER]},
                "auto_enable_com_on_load": False
            },
            {
                "schema_version": 1,
                "owner": owner,
                "name": "smc-bootloader",
                "rule_id": f"{owner}.smc-bootloader",
                "kind": "tool",
                "selectors": ["COM0", "COM1"],
                "pattern": {"kind": "contains", "value": "SMC bootloader"},
                "handler": {"exec": [SERIALWRAP_EVENT_HANDLER]},
                "auto_enable_com_on_load": False
            }
        ]
        return rules
    
    def register_event_rules(self) -> None:
        """Register event rules with serialwrap.

        Current serialwrap CLI accepts rules via `event add --file <path>`
        rather than the legacy `--rule <json>` form.

        Raises:
            ControllerError: If rule registration fails.
        """
        import tempfile
        rules = self.generate_event_rules()
        for rule in rules:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix=f"rule_{rule['name']}_",
                delete=False, encoding="utf-8"
            ) as tf:
                json.dump(rule, tf)
                tmp_path = tf.name
            try:
                returncode, stdout, stderr = self.runner.run([
                    SERIALWRAP_CMD, "event", "add",
                    "--file", tmp_path
                ])
                if returncode != 0:
                    raise ControllerError(
                        f"Failed to add event rule {rule['name']}: {stderr}"
                    )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    
    def enable_selector(self) -> None:
        """Enable event matcher for this selector.
        
        Raises:
            ControllerError: If enable command fails.
        """
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "event", "enable",
            "--selector", self.selector
        ])
        
        if returncode != 0:
            raise ControllerError(f"Failed to enable selector {self.selector}: {stderr}")
    
    def disable_selector(self) -> None:
        """Disable event matcher for this selector.
        
        Raises:
            ControllerError: If disable command fails.
        """
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "event", "disable",
            "--selector", self.selector
        ])
        
        if returncode != 0:
            raise ControllerError(f"Failed to disable selector {self.selector}: {stderr}")
    
    def reset_selector(self) -> None:
        """Reset event state for this selector.
        
        Raises:
            ControllerError: If reset command fails.
        """
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "event", "reset",
            "--selector", self.selector
        ])
        
        if returncode != 0:
            raise ControllerError(f"Failed to reset selector {self.selector}: {stderr}")
    
    def check_other_selectors_enabled(self) -> Optional[bool]:
        """Check if other COM selectors are still enabled.
        
        Returns:
            True if any other selector is enabled, False if none are enabled,
            None if status could not be determined safely.
        """
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "event", "status"
        ])
        
        if returncode != 0:
            print(f"WARNING: Failed to query event status: {stderr}", file=sys.stderr)
            return None
        
        try:
            status = json.loads(stdout)
            if not isinstance(status, dict):
                print("WARNING: Unexpected event status format: top-level JSON is not an object", file=sys.stderr)
                return None

            # Current schema: {"coms": ["COM0", ...]} listing enabled COMs.
            coms = status.get("coms")
            if isinstance(coms, list):
                for com in coms:
                    if com != self.selector:
                        return True
                return False

            # Legacy schema fallback: {"selectors": {"COMx": {"enabled": bool}}}.
            selectors = status.get("selectors")
            if isinstance(selectors, dict):
                for selector, info in selectors.items():
                    if not isinstance(info, dict):
                        print(f"WARNING: Unexpected event status format for {selector}", file=sys.stderr)
                        return None
                    if selector != self.selector and info.get("enabled"):
                        return True
                return False

            print("WARNING: event status missing both 'coms' and 'selectors' fields", file=sys.stderr)
            return None
        except json.JSONDecodeError as e:
            print(f"WARNING: Failed to parse event status JSON: {e}", file=sys.stderr)
            return None
        except (KeyError, TypeError) as e:
            print(f"WARNING: Unexpected event status format: {e}", file=sys.stderr)
            return None
    
    def remove_event_rules(self) -> None:
        """Remove shared event rules.

        Current serialwrap CLI accepts `event rm <rule_id>` (positional) rather
        than the legacy `--name <name>` form.

        Raises:
            ControllerError: If rule removal fails.
        """
        rules = self.generate_event_rules()
        for rule in rules:
            returncode, stdout, stderr = self.runner.run([
                SERIALWRAP_CMD, "event", "rm",
                rule["rule_id"]
            ])

            if returncode != 0:
                raise ControllerError(f"Failed to remove event rule {rule['name']}: {stderr}")
    
    def check_ready_state(self) -> bool:
        """Check if session is in READY state."""
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "session", "list"
        ])
        
        if returncode != 0:
            return False
        
        try:
            data = json.loads(stdout)
            sessions = data.get("sessions", [])
            for session in sessions:
                if session.get("com") == self.selector or session.get("selector") == self.selector:
                    return session.get("state") == "READY"
            return False
        except json.JSONDecodeError as e:
            print(f"WARNING: Failed to parse session list JSON: {e}", file=sys.stderr)
            return False
        except (KeyError, TypeError) as e:
            print(f"WARNING: Unexpected session list format: {e}", file=sys.stderr)
            return False
    
    def check_self_test(self) -> bool:
        """Run self-test and check if OK."""
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "session", "self-test",
            "--selector", self.selector,
            "--probe-timeout", "10"
        ])
        
        if returncode != 0:
            return False
        
        try:
            data = json.loads(stdout)
            return (
                data.get("classification") == "OK" and
                data.get("probe_ok") is True
            )
        except json.JSONDecodeError as e:
            print(f"WARNING: Failed to parse self-test JSON: {e}", file=sys.stderr)
            return False
        except (KeyError, TypeError) as e:
            print(f"WARNING: Unexpected self-test format: {e}", file=sys.stderr)
            return False
    
    def submit_normal_reboot(self) -> float:
        """Submit normal reboot command.
        
        Returns:
            Timestamp of reboot submission.
            
        Raises:
            ControllerError: If reboot submission fails.
        """
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "cmd", "submit",
            "--selector", self.selector,
            "--source", "agent:reboot-controller",
            "--mode", "line",
            "--cmd", "reboot"
        ])
        
        if returncode != 0:
            raise ControllerError(f"Failed to submit reboot command: {stderr}")
        
        self.reboot_count += 1
        return time.time()
    
    def should_throttle_recovery(
        self,
        last_action: Optional[float],
        throttle_seconds: int = 300
    ) -> bool:
        """Check if recovery should be throttled.
        
        Args:
            last_action: Timestamp of last reboot or fallback action.
            throttle_seconds: Throttle period in seconds.
            
        Returns:
            True if should wait, False if can proceed.
        """
        if last_action is None:
            return False
        
        elapsed = time.time() - last_action
        return elapsed < throttle_seconds
    
    def run_session_recover(self) -> None:
        """Run session recover command.
        
        Raises:
            ControllerError: If recover command fails.
        """
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "session", "recover",
            "--selector", self.selector
        ])
        
        if returncode != 0:
            raise ControllerError(f"Failed to run session recover for {self.selector}: {stderr}")
    
    def check_log_tail_for_prompt(
        self,
        log_file: Path,
        prompt: str,
        tail_lines: int = 50
    ) -> bool:
        """Check log tail for specific prompt.
        
        Args:
            log_file: Path to log file.
            prompt: Prompt string to search for.
            tail_lines: Number of lines to check from end.
            
        Returns:
            True if prompt found in tail.
        """
        try:
            from collections import deque
            
            # Use bounded deque to keep only last N lines in memory
            tail = deque(maxlen=tail_lines)
            
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    tail.append(line)
            
            tail_content = ''.join(tail)
            return prompt in tail_content
        except OSError as e:
            print(f"WARNING: Cannot read {log_file}: {e}", file=sys.stderr)
            return False
    
    def send_raw_broker_command(self, command: str) -> float:
        """Send raw command directly to the session via `cmd submit`.

        The legacy `serialwrap broker raw` subcommand was removed from the
        current CLI; recovery paths now route raw input through `cmd submit`
        with source `agent:reboot-controller-raw`, which still hands the bytes
        plus a trailing newline to the UART even when the target is sitting at
        a non-shell prompt (e.g. u-boot `=>`).

        Args:
            command: Command string to send.

        Returns:
            Timestamp of command submission.

        Raises:
            ControllerError: If submit fails.
        """
        returncode, stdout, stderr = self.runner.run([
            SERIALWRAP_CMD, "cmd", "submit",
            "--selector", self.selector,
            "--source", "agent:reboot-controller-raw",
            "--mode", "line",
            "--cmd", command
        ])

        if returncode != 0:
            raise ControllerError(f"Failed to send raw command '{command}' to {self.selector}: {stderr}")

        return time.time()
    
    def in_boot_guard(self, last_action: Optional[float]) -> bool:
        """Return True if we are still in the post-reboot boot-guard window.

        While the target is booting through u-boot, any byte we send aborts
        the autoboot countdown and strands us at the `=>` prompt. The guard
        keeps the controller silent on the UART until u-boot has handed off
        to the kernel.
        """
        if last_action is None:
            return False
        return (time.time() - last_action) < self.boot_guard_seconds

    def decide_reboot_action(
        self,
        last_action: Optional[float]
    ) -> Dict[str, Any]:
        """Decide next reboot action.

        Args:
            last_action: Timestamp of last reboot or fallback action.

        Returns:
            Dictionary with action type and details.
        """
        # Boot guard: during the boot window, neither probe (self-test /
        # session recover) nor raw key injection is safe — sending any byte
        # while the target is in u-boot autoboot traps it at `=>`.
        if self.in_boot_guard(last_action):
            return {"type": "wait"}

        # Check if READY and self-test OK
        if self.check_ready_state() and self.check_self_test():
            return {"type": "normal_reboot"}

        # Not READY - check throttle
        if self.should_throttle_recovery(last_action):
            return {"type": "wait"}
        
        # Run recover
        try:
            self.run_session_recover()
        except ControllerError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return {"type": "wait"}
        
        # Check if now ready
        if self.check_ready_state():
            return {"type": "wait"}  # Will reboot on next cycle
        
        # Check log tail for prompts
        if self.active_log_path:
            if self.check_log_tail_for_prompt(self.active_log_path, "=>"):
                return {"type": "raw_reset"}
            elif self.check_log_tail_for_prompt(self.active_log_path, "root@prplOS:/#"):
                return {"type": "raw_reboot"}
        
        return {"type": "wait"}
    
    def startup(self) -> bool:
        """Run startup sequence.
        
        Returns:
            True if startup succeeded, False otherwise.
        """
        # 1. Check serialwrap event support
        if not self.check_serialwrap_event_support():
            print("ERROR: serialwrap event subcommand not available", file=sys.stderr)
            return False
        
        # 2. Check daemon status
        if not self.check_daemon_status():
            print("ERROR: serialwrap daemon not running", file=sys.stderr)
            return False
        
        # 3. Send marker command
        try:
            marker = self.send_marker_command()
        except ControllerError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return False
        
        # 4. Find active minicom log
        active_log = self.find_active_minicom_log(marker)
        if not active_log:
            print(f"ERROR: No active minicom log found for {self.selector}", file=sys.stderr)
            return False
        
        # Set active_log_path for use in reboot loop
        self.active_log_path = active_log
        
        # 5. Derive report path
        report_path = self.derive_report_path(active_log)
        
        # 6. Create run-state directory
        self.state_dir = self.create_run_state_directory()
        
        # Wrap everything after state creation to ensure cleanup on failure
        try:
            # 7. Store run state
            self.store_run_state(self.state_dir, active_log, report_path)
            
            # 8. Register event rules
            self.register_event_rules()
            
            # 9. Enable this selector
            self.enable_selector()
            
            # 10. Register signal handlers
            self.register_signal_handlers()
            
            return True
        except ControllerError as e:
            # Cleanup on any failure after state dir creation
            print(f"ERROR: {e}", file=sys.stderr)
            self._cleanup_on_startup_failure()
            return False
    
    def run_loop(self) -> None:
        """Run one iteration of the reboot loop."""
        # Decide next action
        action = self.decide_reboot_action(self.last_action_time)
        
        # Execute action
        if action['type'] == 'normal_reboot':
            try:
                self.last_action_time = self.submit_normal_reboot()
                self.sleep_fn(self.loop_delay)
            except ControllerError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                self.sleep_fn(self.loop_delay)
        elif action['type'] == 'raw_reset':
            try:
                self.last_action_time = self.send_raw_broker_command("reset")
            except ControllerError as e:
                print(f"ERROR: {e}", file=sys.stderr)
            self.sleep_fn(self.loop_delay)
        elif action['type'] == 'raw_reboot':
            try:
                self.last_action_time = self.send_raw_broker_command("reboot -f")
            except ControllerError as e:
                print(f"ERROR: {e}", file=sys.stderr)
            self.sleep_fn(self.loop_delay)
        elif action['type'] == 'wait':
            self.sleep_fn(self.loop_delay)
    
    def _cleanup_on_startup_failure(self) -> None:
        """Best-effort cleanup when startup fails after state creation.
        
        Attempts to clean up state directory and event rules. Errors are logged
        as warnings but cleanup continues through all steps.
        """
        # Try to disable and reset selector (may not be enabled yet)
        try:
            self.disable_selector()
        except ControllerError as e:
            print(f"WARNING: Failed to disable selector during startup cleanup: {e}", file=sys.stderr)
        
        try:
            self.reset_selector()
        except ControllerError as e:
            print(f"WARNING: Failed to reset selector during startup cleanup: {e}", file=sys.stderr)
        
        # Try to remove event rules if no other selectors are enabled
        try:
            other_selectors_enabled = self.check_other_selectors_enabled()
            if other_selectors_enabled is False:
                self.remove_event_rules()
        except ControllerError as e:
            print(f"WARNING: Failed to remove event rules during startup cleanup: {e}", file=sys.stderr)
        
        # Always try to remove state directory
        if self.state_dir and self.state_dir.exists():
            try:
                import shutil
                shutil.rmtree(self.state_dir)
            except OSError as e:
                print(f"WARNING: Failed to remove state directory during startup cleanup: {e}", file=sys.stderr)
    
    def cleanup(self) -> None:
        """Cleanup on exit.
        
        Attempts all cleanup steps. Errors are logged but don't prevent
        state directory removal.
        """
        # Disable and reset this selector
        try:
            self.disable_selector()
        except ControllerError as e:
            print(f"WARNING: Failed to disable selector during cleanup: {e}", file=sys.stderr)
        
        try:
            self.reset_selector()
        except ControllerError as e:
            print(f"WARNING: Failed to reset selector during cleanup: {e}", file=sys.stderr)
        
        # Check if other selectors are still enabled
        try:
            other_selectors_enabled = self.check_other_selectors_enabled()
            if other_selectors_enabled is False:
                # Remove shared rules
                self.remove_event_rules()
        except ControllerError as e:
            print(f"WARNING: Failed to remove event rules during cleanup: {e}", file=sys.stderr)
        
        # Remove state directory (always attempt this)
        if self.state_dir and self.state_dir.exists():
            import shutil
            try:
                shutil.rmtree(self.state_dir)
            except OSError as e:
                print(f"WARNING: Failed to remove state directory: {e}", file=sys.stderr)
    
    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle signals."""
        self._stop_requested = True
        self.cleanup()
        sys.exit(0)
    
    def register_signal_handlers(self) -> None:
        """Register signal handlers for SIGINT and SIGTERM."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def exit_gracefully(self) -> None:
        """Exit gracefully with cleanup."""
        self.cleanup()
    
    def should_stop(self) -> bool:
        """Check if controller should stop.
        
        Returns:
            True if stop condition met.
        """
        if self._stop_requested:
            return True
        
        # Check hours limit
        if self.hours_limit is not None:
            elapsed_hours = (time.time() - self.start_time) / 3600
            if elapsed_hours >= self.hours_limit:
                return True
        
        # Check count limit
        if self.count_limit is not None:
            if self.reboot_count >= self.count_limit:
                return True
        
        return False


def parse_args(argv):
    """Parse command-line arguments for the reboot controller.
    
    Args:
        argv: List of command-line arguments (excluding program name).
        
    Returns:
        Namespace object with parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Control reboot testing for a single COM selector."
    )
    parser.add_argument(
        "--selector",
        required=True,
        help="COM selector (e.g., COM0, COM1)"
    )
    parser.add_argument(
        "--hours",
        type=positive_int,
        help="Stop after N hours"
    )
    parser.add_argument(
        "--count",
        type=positive_int,
        help="Stop after N completed reboot attempts"
    )
    
    args = parser.parse_args(argv)
    
    # Validate selector format
    if not re.match(r'^COM[0-9]+$', args.selector):
        parser.error(f"Invalid selector: {args.selector}. Must match pattern COM[0-9]+")
    
    return args


def main_with_runner(
    argv: List[str],
    runner: Optional[Any] = None,
    log_dir: Optional[Path] = None
) -> int:
    """Main entry point with injectable runner for testing.
    
    Args:
        argv: Command-line arguments.
        runner: Optional command runner.
        log_dir: Optional log directory.
        
    Returns:
        Exit code.
    """
    args = parse_args(argv)
    
    # Create controller
    controller = RebootController(
        selector=args.selector,
        runner=runner,
        log_dir=log_dir,
        hours_limit=args.hours,
        count_limit=args.count
    )
    
    # Run startup
    if not controller.startup():
        return 1
    
    # Run loop until stop condition
    try:
        try:
            while not controller.should_stop():
                controller.run_loop()
        except KeyboardInterrupt:
            print("\nInterrupted by user", file=sys.stderr)
    finally:
        controller.cleanup()
    return 0


def main():
    """Main entry point for the reboot controller."""
    return main_with_runner(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
