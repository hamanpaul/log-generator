# Robustness Fixes Summary

## Overview
Fixed 3 robustness issues in event handler following strict TDD methodology.

## Test Results
- **Before**: 170 tests passing
- **After**: 179 tests passing (+9 tests)
- **Broad exceptions**: None (verified via grep)

## Issues Fixed

### Issue 1: Cursor file load failure silently resets cursors
**Problem**: When cursor file was corrupted or unreadable, `load_scan_cursors()` returned empty dict with no warning.

**Solution**: Added warnings to stderr while preserving fail-open behavior.
- Corrupt JSON → Warning + return {}
- I/O error → Warning + return {}
- Valid file → No warning

**Tests added**: 3 tests in `test_robustness_warnings.py`

### Issue 2: Report parse failures silently ignored
**Problem**: When report file had malformed rows, `load_report_data()` silently skipped them.

**Solution**: Added warnings to stderr when skipping malformed rows.
- Malformed summary row → Warning + skip row
- Malformed event row → Warning + skip row
- Valid rows preserved

**Tests added**: 3 tests in `test_robustness_warnings.py`

### Issue 3: Atomic write cleanup test gap
**Problem**: No test verified temp file cleanup on write failure.

**Solution**: Added tests to verify existing cleanup works correctly.
- Write failure → Temp file cleaned + IOError raised
- Rename failure → Temp file cleaned + IOError raised
- Success → No temp files remain

**Tests added**: 3 tests in `test_robustness_warnings.py`
**Production changes**: None (cleanup already implemented)

## Files Changed

### New Files
- `tests/test_robustness_warnings.py` (9 tests)

### Modified Files
- `serialwrap_reboot_test/event_handler.py`:
  - `load_scan_cursors()`: Split exception handling to emit specific warnings
  - `load_report_data()`: Added warning when skipping malformed rows

## Code Changes Detail

### load_scan_cursors() changes:
```python
# BEFORE: Silent failure
except (json.JSONDecodeError, IOError):
    return {}

# AFTER: Warnings emitted
except json.JSONDecodeError as e:
    print(f"Warning: Failed to parse cursor file {cursor_file}: {e}", file=sys.stderr)
    return {}
except IOError as e:
    print(f"Warning: Failed to read cursor file {cursor_file}: {e}", file=sys.stderr)
    return {}
```

### load_report_data() changes:
```python
# BEFORE: Silent skip
except ValueError:
    pass

# AFTER: Warning when skipping
except ValueError as e:
    print(f"Warning: Malformed summary row in {report_path}, skipping: {line.strip()}", file=sys.stderr)

# Same pattern for event rows
```

## Behavior Preserved
- **Fail-open**: Cursor/report parse failures don't crash handler
- **Thread-safety**: All locking behavior unchanged
- **Atomic writes**: Temp cleanup already worked, now tested
- **No broad exceptions**: Still only narrow exception handling

## Operator Benefits
Operators now see warnings when:
- Cursor files are corrupted (manual editing errors)
- Report files are malformed (concurrent write issues)
- But system continues operating (fail-open preserved)

This provides visibility into data corruption issues without causing handler failures.
