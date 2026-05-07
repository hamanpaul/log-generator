#!/usr/bin/env python3
"""Test controller reboot loop logic (Task 2.4)."""

import json
import time
import unittest
from unittest.mock import Mock, patch


class FakeCommandRunner:
    """Fake command runner for testing without invoking real serialwrap."""
    
    def __init__(self):
        self.commands = []
        self.responses = {}
        self.call_count = {}
        
    def run(self, cmd, **kwargs):
        """Record command and return configured response."""
        self.commands.append(cmd)
        cmd_str = ' '.join(cmd) if isinstance(cmd, list) else cmd
        
        # Track call count
        self.call_count[cmd_str] = self.call_count.get(cmd_str, 0) + 1
        
        # Check custom responses first
        for pattern, response in self.responses.items():
            if pattern in cmd_str:
                return response
        
        # Default responses
        return (0, "", "")
    
    def set_response(self, pattern, returncode, stdout, stderr=""):
        """Configure response for commands matching pattern."""
        self.responses[pattern] = (returncode, stdout, stderr)


class TestControllerRebootLoop(unittest.TestCase):
    """Test controller reboot loop decision logic."""
    
    def test_check_ready_state_when_ready(self):
        """Test checking READY state when session is READY."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        
        # Simulate READY state
        session_output = json.dumps({
            "sessions": [{
                "selector": "COM0",
                "state": "READY"
            }]
        })
        runner.set_response('session list', 0, session_output)
        
        controller = RebootController("COM0", runner=runner)
        result = controller.check_ready_state()
        
        self.assertTrue(result)
    
    def test_check_ready_state_when_not_ready(self):
        """Test checking READY state when session is not READY."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        
        # Simulate NOT_READY state
        session_output = json.dumps({
            "sessions": [{
                "selector": "COM1",
                "state": "RECOVERY"
            }]
        })
        runner.set_response('session list', 0, session_output)
        
        controller = RebootController("COM1", runner=runner)
        result = controller.check_ready_state()
        
        self.assertFalse(result)
    
    def test_check_self_test_ok(self):
        """Test self-test check when classification is OK."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        
        # Simulate self-test OK
        selftest_output = json.dumps({
            "classification": "OK",
            "probe_ok": True
        })
        runner.set_response('session self-test', 0, selftest_output)
        
        controller = RebootController("COM0", runner=runner)
        result = controller.check_self_test()
        
        self.assertTrue(result)
    
    def test_check_self_test_not_ok(self):
        """Test self-test check when classification is not OK."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        
        # Simulate self-test failure
        selftest_output = json.dumps({
            "classification": "DEGRADED",
            "probe_ok": False
        })
        runner.set_response('session self-test', 0, selftest_output)
        
        controller = RebootController("COM1", runner=runner)
        result = controller.check_self_test()
        
        self.assertFalse(result)
    
    def test_submit_normal_reboot(self):
        """Test submitting normal reboot command."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        controller = RebootController("COM0", runner=runner)
        
        timestamp = controller.submit_normal_reboot()
        
        # Should return a timestamp
        self.assertIsNotNone(timestamp)
        self.assertIsInstance(timestamp, float)
        
        # Should have submitted reboot command with agent source
        cmd_found = False
        for cmd in runner.commands:
            cmd_str = ' '.join(cmd)
            if 'cmd submit' in cmd_str and 'agent:reboot-controller' in cmd_str and 'reboot' in cmd_str:
                cmd_found = True
                break
        self.assertTrue(cmd_found)
    
    def test_should_throttle_recovery_within_five_minutes(self):
        """Test recovery throttling within 5 minutes."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        controller = RebootController("COM1", runner=runner)
        
        # Set last action to 2 minutes ago
        last_action = time.time() - 120
        
        result = controller.should_throttle_recovery(last_action, throttle_seconds=300)
        
        self.assertTrue(result)
    
    def test_should_throttle_recovery_after_five_minutes(self):
        """Test recovery throttling after 5 minutes."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        controller = RebootController("COM0", runner=runner)
        
        # Set last action to 6 minutes ago
        last_action = time.time() - 360
        
        result = controller.should_throttle_recovery(last_action, throttle_seconds=300)
        
        self.assertFalse(result)
    
    def test_run_session_recover(self):
        """Test running session recover command."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        controller = RebootController("COM1", runner=runner)
        
        controller.run_session_recover()
        
        # Should have called session recover
        cmd_found = False
        for cmd in runner.commands:
            cmd_str = ' '.join(cmd)
            if 'session recover' in cmd_str:
                cmd_found = True
                break
        self.assertTrue(cmd_found)
    
    def test_check_log_tail_for_uboot_prompt(self):
        """Test checking log tail for U-Boot prompt."""
        from serialwrap_reboot_test.controller import RebootController
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "mini_COM0_test.log"
            log_file.write_text("boot log\nsome output\n=>\nmore output\n")
            
            runner = FakeCommandRunner()
            controller = RebootController("COM0", runner=runner)
            
            result = controller.check_log_tail_for_prompt(log_file, "=>", tail_lines=50)
            
            self.assertTrue(result)
    
    def test_check_log_tail_for_prplos_prompt(self):
        """Test checking log tail for prplOS prompt."""
        from serialwrap_reboot_test.controller import RebootController
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "mini_COM1_test.log"
            log_file.write_text("boot log\nlogin successful\nroot@prplOS:/# \n")
            
            runner = FakeCommandRunner()
            controller = RebootController("COM1", runner=runner)
            
            result = controller.check_log_tail_for_prompt(log_file, "root@prplOS:/#", tail_lines=50)
            
            self.assertTrue(result)
    
    def test_check_log_tail_no_prompt(self):
        """Test checking log tail when no prompt is found."""
        from serialwrap_reboot_test.controller import RebootController
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "mini_COM0_test.log"
            log_file.write_text("boot log\nsome output\nno prompt here\n")
            
            runner = FakeCommandRunner()
            controller = RebootController("COM0", runner=runner)
            
            result = controller.check_log_tail_for_prompt(log_file, "=>", tail_lines=50)
            
            self.assertFalse(result)
    
    def test_send_raw_broker_command_reset(self):
        """Test sending raw broker command for reset."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        controller = RebootController("COM0", runner=runner)
        
        timestamp = controller.send_raw_broker_command("reset")
        
        # Should return a timestamp
        self.assertIsNotNone(timestamp)
        self.assertIsInstance(timestamp, float)
        
        # Should have sent via broker raw/console
        cmd_found = False
        for cmd in runner.commands:
            cmd_str = ' '.join(cmd)
            if 'broker' in cmd_str and 'raw' in cmd_str and 'reset' in cmd_str:
                cmd_found = True
                break
        self.assertTrue(cmd_found)
    
    def test_send_raw_broker_command_reboot_force(self):
        """Test sending raw broker command for reboot -f."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        controller = RebootController("COM1", runner=runner)
        
        timestamp = controller.send_raw_broker_command("reboot -f")
        
        # Should return a timestamp
        self.assertIsNotNone(timestamp)
        
        # Should have sent via broker raw/console
        cmd_found = False
        for cmd in runner.commands:
            cmd_str = ' '.join(cmd)
            if 'broker' in cmd_str and 'raw' in cmd_str and 'reboot -f' in cmd_str:
                cmd_found = True
                break
        self.assertTrue(cmd_found)
    
    def test_reboot_decision_ready_and_self_test_ok(self):
        """Test reboot decision when READY and self-test OK."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        
        # Simulate READY and self-test OK
        session_output = json.dumps({"sessions": [{"selector": "COM0", "state": "READY"}]})
        selftest_output = json.dumps({"classification": "OK", "probe_ok": True})
        runner.set_response('session list', 0, session_output)
        runner.set_response('session self-test', 0, selftest_output)
        
        controller = RebootController("COM0", runner=runner)
        
        action = controller.decide_reboot_action(None)
        
        self.assertEqual(action['type'], 'normal_reboot')
    
    def test_reboot_decision_not_ready_within_throttle(self):
        """Test reboot decision when not READY and within throttle period."""
        from serialwrap_reboot_test.controller import RebootController
        
        runner = FakeCommandRunner()
        
        # Simulate NOT READY
        session_output = json.dumps({"sessions": [{"selector": "COM1", "state": "RECOVERY"}]})
        runner.set_response('session list', 0, session_output)
        
        controller = RebootController("COM1", runner=runner)
        
        # Last action 2 minutes ago (within 5 minute throttle)
        last_action = time.time() - 120
        
        action = controller.decide_reboot_action(last_action)
        
        self.assertEqual(action['type'], 'wait')
    
    def test_reboot_decision_not_ready_after_throttle_no_prompt(self):
        """Test reboot decision when not READY, after throttle, no prompt found."""
        from serialwrap_reboot_test.controller import RebootController
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "mini_COM0_test.log"
            log_file.write_text("boot log\nno prompt\n")
            
            runner = FakeCommandRunner()
            
            # Simulate NOT READY
            session_output = json.dumps({"sessions": [{"selector": "COM0", "state": "RECOVERY"}]})
            runner.set_response('session list', 0, session_output)
            
            controller = RebootController("COM0", runner=runner, active_log=log_file)
            
            # Last action 6 minutes ago (after 5 minute throttle)
            last_action = time.time() - 360
            
            action = controller.decide_reboot_action(last_action)
            
            # Should have run recover, then wait since no prompt
            self.assertEqual(action['type'], 'wait')


if __name__ == "__main__":
    unittest.main()
