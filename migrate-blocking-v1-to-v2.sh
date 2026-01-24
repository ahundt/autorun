#!/bin/bash
# Migration Script: block-rm-command.py (v1.0) to clautorun v2.0 Command Blocking
#
# This script migrates from the legacy block-rm-command.py hook to the new
# integrated command blocking system in clautorun v2.0.
#
# Usage: bash migrate-blocking-v1-to-v2.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "clautorun Command Blocking Migration"
echo "v1.0 (block-rm-command.py) → v2.0 (integrated)"
echo "=========================================="
echo ""

# Check if old state file exists
OLD_STATE_FILE="$HOME/.claude/hooks/.rm-state"

if [ -f "$OLD_STATE_FILE" ]; then
    echo "📂 Found legacy state file: $OLD_STATE_FILE"

    # Read enabled state from old file
    ENABLED=$(grep -o '"enabled":[^,}]*' "$OLD_STATE_FILE" 2>/dev/null | cut -d: -f2 | tr -d ' "')

    if [ "$ENABLED" = "true" ]; then
        echo "✅ Legacy rm blocking was ENABLED"
        echo ""
        echo "🔄 Migrating to clautorun v2.0..."

        # Create global config directory if it doesn't exist
        GLOBAL_CONFIG_DIR="$HOME/.claude/config"
        GLOBAL_CONFIG_FILE="$GLOBAL_CONFIG_DIR/command-blocks.json"

        mkdir -p "$GLOBAL_CONFIG_DIR"

        # Check if global config already exists
        if [ -f "$GLOBAL_CONFIG_FILE" ]; then
            echo "⚠️  Global config already exists: $GLOBAL_CONFIG_FILE"
            echo "   Checking if rm is already blocked..."

            if grep -q '"pattern": "rm"' "$GLOBAL_CONFIG_FILE" 2>/dev/null; then
                echo "   'rm' is already globally blocked in v2.0"
            else
                echo "   Adding 'rm' to existing global blocks..."
                # This would require Python or jq to modify JSON properly
                echo "   Please manually run: /cr:globalno rm"
            fi
        else
            echo "📝 Creating new global config file..."
            cat > "$GLOBAL_CONFIG_FILE" << 'EOF'
{
  "version": "2.0",
  "global_blocked_patterns": [
    {
      "pattern": "rm",
      "suggestion": "Use 'trash' CLI instead for safe file deletion",
      "added_at": "MIGRATED_FROM_V1"
    }
  ]
}
EOF
            echo "✅ Created global config with rm block"
        fi

        echo ""
        echo "✅ Migration complete!"
        echo ""
        echo "📋 Next steps:"
        echo "   1. Verify status: /cr:globalstatus"
        echo "   2. Test blocking: Try running 'rm test.txt'"
        echo "   3. Remove old hook from settings.json (see below)"
        echo ""

    else
        echo "ℹ️  Legacy rm blocking was DISABLED"
        echo "   No migration needed"
    fi

    # Backup old state file
    BACKUP_FILE="$OLD_STATE_FILE.backup"
    mv "$OLD_STATE_FILE" "$BACKUP_FILE"
    echo "📦 Old state file backed up to: $BACKUP_FILE"

else
    echo "ℹ️  No legacy state file found"
    echo "   Skipping migration (fresh v2.0 installation)"
fi

echo ""
echo "=========================================="
echo "🧹 Manual Cleanup Required"
echo "=========================================="
echo ""
echo "To complete migration, remove the old hook from settings.json:"
echo ""
echo "1. Open settings.json:"
echo "   code ~/.claude/settings.json"
echo ""
echo "2. Look for 'hooks' section with block-rm-command.py"
echo ""
echo "3. Remove the entry (or entire hooks array if empty):"
echo ""
echo "BEFORE:"
echo '{'
echo '  "hooks": {'
echo '    "PreToolUse": ['
echo '      {'
echo '        "type": "command",'
echo '        "command": "python3 ~/.claude/hooks/block-rm-command.py"'
echo '      }'
echo '    ]'
echo '  }'
echo '}'
echo ""
echo "AFTER:"
echo '{'
echo '  "hooks": {'
echo '  }'
echo '}'
echo ""
echo "4. Save the file"
echo ""
echo "5. Update/reinstall clautorun:"
echo "   /plugin update clautorun"
echo ""
echo "=========================================="
echo "✅ Migration script complete!"
echo "=========================================="
