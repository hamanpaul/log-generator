"""Constants for serialwrap reboot test toolkit."""

import os
from pathlib import Path

SERIALWRAP_CMD = os.environ.get(
    'SERIALWRAP_CMD',
    '/home/paul_chen/.paul_tools/serialwrap'
)

# Absolute path to the event handler wrapper. The serialwrap daemon launches
# this via `subprocess.Popen` without a shell, so a bare name like
# `serialwrap-event-handler` would only work if it sat on the daemon's PATH —
# which it does not. Resolve from this module's location (project_root/bin/...)
# and let the operator override via env when running out-of-tree.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERIALWRAP_EVENT_HANDLER = os.environ.get(
    'SERIALWRAP_EVENT_HANDLER',
    str(_PROJECT_ROOT / 'bin' / 'serialwrap-event-handler')
)
