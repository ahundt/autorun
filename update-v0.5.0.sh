#!/bin/bash
# Update script for clautorun v0.5.0
# Major changes: tmux API enhancements, batch window operations, Claude status detection
#
# Usage: ./update-v0.5.0.sh

set -e

echo "=== Clautorun v0.5.0 Update ==="
echo ""

# Step 1: Uninstall old plugin
echo "Step 1: Uninstalling old clautorun plugin..."
claude plugin uninstall clautorun@clautorun 2>/dev/null || echo "  (Plugin may not have been installed)"

# Step 2: Install new clautorun plugin
echo ""
echo "Step 2: Installing clautorun plugin (v0.5.0)..."
claude plugin install clautorun@clautorun

# Step 3: Install plan-export plugin
echo ""
echo "Step 3: Installing plan-export plugin..."
claude plugin install plan-export@clautorun

echo ""
echo "=== Update Complete ==="
echo ""
echo "Verify with:"
echo "  - Start a new Claude Code session"
echo "  - Run /cr:st to check clautorun status"
echo "  - Run /plugin to see installed plugins"
