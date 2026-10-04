#!/bin/bash
set -euo pipefail

# Only run in Claude Code cloud (remote) sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install markitdown (used by the .claude/skills/markitdown skill). Idempotent.
pip install --quiet 'markitdown[all]'
