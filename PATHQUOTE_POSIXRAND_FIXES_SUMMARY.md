# Fix Pass 4: Path Quoting and POSIX Random Portability Fixes

## Overview
Fourth TDD fix pass addressing remaining path quoting issues and $RANDOM portability in the fault installer implementation.

## Issues Fixed

### 1. Unquoted Paths in Target Commands
**Problem**: Path variables in transfer_file(), install(), and verify() methods were not quoted with shlex.quote(), creating potential command injection vectors.

**Locations**:
- `serialwrap_reboot_test/fault_installer.py:315` - chmod command in transfer_file()
- `serialwrap_reboot_test/fault_installer.py:359` - ln command in install()
- `serialwrap_reboot_test/fault_installer.py:377,383,389,395` - test and readlink commands in verify()

**Fix**: Wrapped all path variables with `shlex.quote()` for defense-in-depth protection.

**Before**:
```python
# transfer_file()
rc_chmod, _, _ = self.runner.run_target(f"chmod {mode} {remote_path}")

# install()
rc, _, _ = self.runner.run(f"ln -sf {self.INIT_PATH} {self.SYMLINK_PATH}")

# verify()
rc, _, _ = self.runner.run(f"test -x {self.INJECTOR_PATH}")
rc, _, _ = self.runner.run(f"test -L {self.SYMLINK_PATH}")
rc, stdout, _ = self.runner.run(f"readlink {self.SYMLINK_PATH}")
```

**After**:
```python
# transfer_file()
rc_chmod, _, _ = self.runner.run_target(f"chmod {mode} {shlex.quote(remote_path)}")

# install()
rc, _, _ = self.runner.run(f"ln -sf {shlex.quote(self.INIT_PATH)} {shlex.quote(self.SYMLINK_PATH)}")

# verify()
rc, _, _ = self.runner.run(f"test -x {shlex.quote(self.INJECTOR_PATH)}")
rc, _, _ = self.runner.run(f"test -L {shlex.quote(self.SYMLINK_PATH)}")
rc, stdout, _ = self.runner.run(f"readlink {shlex.quote(self.SYMLINK_PATH)}")
```

### 2. Non-POSIX $RANDOM Usage
**Problem**: Fault injector script used bash-specific $RANDOM variable with `#!/bin/sh` shebang, breaking POSIX sh compatibility.

**Locations**:
- `serialwrap_reboot_test/fault_installer.py:25` - 10% probability gate
- `serialwrap_reboot_test/fault_installer.py:30` - 4-way fault type selection
- `serialwrap_reboot_test/fault_installer.py:50` - Random PID line selection

**Fix**: Replaced all $RANDOM usages with POSIX-compatible /dev/urandom + od approach.

**Before**:
```sh
#!/bin/sh
# 10% probability gate: trigger fault if RANDOM % 10 == 0
if [ $(( $RANDOM % 10 )) -ne 0 ]; then
    exit 0
fi

# Choose fault type: 0-3 for equal 25% probability among four faults
FAULT_TYPE=$(( $RANDOM % 4 ))

# ... later in process coredump ...
RANDOM_LINE=$(( ($RANDOM % $PID_COUNT) + 1 ))
```

**After**:
```sh
#!/bin/sh
# POSIX-compatible random number helper using /dev/urandom
# Reads 2 bytes as unsigned 16-bit integer (0-65535)
get_random() {
    od -An -N2 -tu2 /dev/urandom | tr -d ' '
}

# 10% probability gate: trigger fault if random % 10 == 0
GATE_RAND=$(get_random)
if [ $(( $GATE_RAND % 10 )) -ne 0 ]; then
    exit 0
fi

# Choose fault type: 0-3 for equal 25% probability among four faults
FAULT_TYPE_RAND=$(get_random)
FAULT_TYPE=$(( $FAULT_TYPE_RAND % 4 ))

# ... later in process coredump ...
PID_RAND=$(get_random)
RANDOM_LINE=$(( ($PID_RAND % $PID_COUNT) + 1 ))
```

## Tests Added

### Path Quoting Tests (3 tests)
1. **TestTransferFilePathQuoting::test_chmod_quotes_remote_path**
   - Verifies chmod command quotes remote_path with malicious injection attempt
   - Example: `/tmp/test; rm -rf /` becomes `'/tmp/test; rm -rf /'`

2. **TestInstallPathQuoting::test_ln_quotes_paths**
   - Verifies ln command quotes both INIT_PATH and SYMLINK_PATH
   - Uses shlex.quote() behavior (quotes only when necessary)

3. **TestVerifyPathQuoting::test_verify_quotes_paths**
   - Verifies all test and readlink commands quote paths
   - Checks INJECTOR_PATH, INIT_PATH, SYMLINK_PATH

### POSIX Random Portability Tests (3 tests)
1. **TestRandomPortability::test_no_bash_random**
   - Asserts script does NOT contain `$RANDOM` or `${RANDOM}`
   - Ensures bash-specific constructs are removed

2. **TestRandomPortability::test_has_posix_random**
   - Asserts script contains `/dev/urandom` for POSIX randomness
   - Verifies POSIX-compatible approach is used

3. **TestRandomPortability::test_maintains_probability_logic**
   - Asserts script still has `% 10` for 10% gate
   - Asserts script still has `% 4` for 4-way fault selection
   - Ensures logic preserved despite implementation change

## Test Results
- **Fault Installer Tests**: 62 passed (up from 56, +6 new tests)
- **Full Test Suite**: 241 passed (up from 235, +6 new tests)
- **Broad Exception Catches**: 0 (verified with grep)
- **$RANDOM Usage**: 0 (verified script does not contain)

## Files Modified
1. **serialwrap_reboot_test/fault_installer.py**
   - Added shlex.quote() to paths in transfer_file(), install(), verify()
   - Replaced $RANDOM with get_random() helper using /dev/urandom
   - Maintained POSIX sh compatibility with #!/bin/sh shebang

2. **tests/test_fault_installer.py**
   - Added TestTransferFilePathQuoting class (1 test)
   - Added TestInstallPathQuoting class (1 test)
   - Added TestVerifyPathQuoting class (1 test)
   - Added TestRandomPortability class (3 tests)

## Verification Steps Completed
1. ✅ All 6 new tests pass (RED → GREEN workflow)
2. ✅ All 62 fault installer tests pass
3. ✅ All 241 full test suite tests pass
4. ✅ No broad exception catches introduced
5. ✅ Script contains no $RANDOM usage
6. ✅ Script uses /dev/urandom for randomness
7. ✅ Script maintains % 10 and % 4 probability logic
8. ✅ All paths passed through shlex.quote()

## Security Impact
- **Defense-in-depth**: All path variables now quoted, even hardcoded constants
- **Command injection prevention**: Malicious paths like `/tmp/test; rm -rf /` properly escaped
- **POSIX portability**: Script now runs on any POSIX sh, not just bash/ash
- **Busybox compatibility**: od, tr, wc, sed, awk all available in busybox

## Notes
- shlex.quote() only adds quotes when necessary (special characters present)
- For safe paths like `/etc/init.d/test`, no quotes added
- For unsafe paths like `/tmp/test; rm -rf /`, quotes added: `'/tmp/test; rm -rf /'`
- get_random() helper provides 0-65535 range, sufficient for modulo operations
- All previous hardening from fix passes 1-3 preserved and verified
