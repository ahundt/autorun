#!/bin/bash
##############################################################################
# Claude Skill Scaffolder
#
# Creates a new Claude skill with proper structure and templates
#
# Usage:
#   bash scaffold-skill.sh <skill-name> [category]
#
# Arguments:
#   skill-name  - Name in kebab-case (e.g., api-test-generator)
#   category    - Optional: document, workflow, or mcp (default: document)
#
# Example:
#   bash scaffold-skill.sh my-awesome-skill document
##############################################################################

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print functions
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_header() {
    echo ""
    echo "========================================"
    echo "$1"
    echo "========================================"
    echo ""
}

# Validate skill name (kebab-case)
validate_skill_name() {
    local name=$1

    # Check if empty
    if [ -z "$name" ]; then
        print_error "Skill name is required"
        echo "Usage: bash scaffold-skill.sh <skill-name> [category]"
        exit 1
    fi

    # Check for invalid characters
    if [[ ! "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
        print_error "Invalid skill name: $name"
        echo ""
        echo "Skill name must:"
        echo "  - Use lowercase letters and numbers only"
        echo "  - Use hyphens (-) to separate words"
        echo "  - Not start or end with hyphen"
        echo ""
        echo "Valid examples:"
        echo "  ✅ api-test-generator"
        echo "  ✅ deploy-to-production"
        echo "  ✅ smart-search"
        echo ""
        echo "Invalid examples:"
        echo "  ❌ API-Test-Generator (uppercase)"
        echo "  ❌ api_test_generator (underscores)"
        echo "  ❌ apiTestGenerator (camelCase)"
        exit 1
    fi
}

# Get category templates
get_category_info() {
    local category=$1

    case "$category" in
        document|doc)
            CATEGORY_NAME="Document & Asset Creation"
            CATEGORY_DESC="Transforms inputs into structured outputs (documents, code, diagrams, reports)"
            CATEGORY_EXAMPLE="Generate API documentation from code comments"
            ;;
        workflow|work)
            CATEGORY_NAME="Workflow Automation"
            CATEGORY_DESC="Automates multi-step processes requiring coordination"
            CATEGORY_EXAMPLE="Deploy application with validation and rollback"
            ;;
        mcp)
            CATEGORY_NAME="MCP Enhancement"
            CATEGORY_DESC="Extends or combines MCP server capabilities"
            CATEGORY_EXAMPLE="Combine database and API tools for data sync"
            ;;
        *)
            print_warning "Unknown category: $category, using 'document'"
            CATEGORY_NAME="Document & Asset Creation"
            CATEGORY_DESC="Transforms inputs into structured outputs"
            CATEGORY_EXAMPLE="Generate structured output from input"
            ;;
    esac
}

# Main function
main() {
    print_header "Claude Skill Scaffolder"

    # Parse arguments
    SKILL_NAME=$1
    CATEGORY=${2:-document}

    # Validate skill name
    validate_skill_name "$SKILL_NAME"

    # Get category info
    get_category_info "$CATEGORY"

    # Set paths
    SKILLS_DIR="$HOME/.claude/skills"
    SKILL_DIR="$SKILLS_DIR/$SKILL_NAME"

    print_info "Skill name: $SKILL_NAME"
    print_info "Category: $CATEGORY_NAME"
    print_info "Target directory: $SKILL_DIR"
    echo ""

    # Check if directory exists
    if [ -d "$SKILL_DIR" ]; then
        print_warning "Skill directory already exists: $SKILL_DIR"
        read -p "Overwrite? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Cancelled"
            exit 0
        fi
        rm -rf "$SKILL_DIR"
    fi

    # Create directory structure
    print_info "Creating directory structure..."
    mkdir -p "$SKILL_DIR"
    mkdir -p "$SKILL_DIR/scripts"
    mkdir -p "$SKILL_DIR/references"
    mkdir -p "$SKILL_DIR/assets"
    print_success "Directories created"

    # Create SKILL.md
    print_info "Creating SKILL.md..."
    cat > "$SKILL_DIR/SKILL.md" << EOF
---
name: $SKILL_NAME
description: [TODO: One sentence describing the outcome users achieve]
---

# ${SKILL_NAME^}

[TODO: Write 50-100 word hook explaining what this skill does and who it's for]

This skill [describe the outcome in one sentence].

**Use this skill when:** [Specific scenario when this is useful]
**Invoke with:** \`/$SKILL_NAME\` or "[Natural language trigger phrase]"

**Category**: $CATEGORY_NAME

---

## How It Works

[TODO: Write 200-400 word workflow overview with 3-5 steps]

This skill follows these steps:

### Step 1: [Phase Name] (X minutes)
- [What happens in this step]
- [Inputs needed]
- [Outputs produced]

### Step 2: [Phase Name] (X minutes)
- [What happens in this step]
- [Inputs needed]
- [Outputs produced]

### Step 3: [Phase Name] (X minutes)
- [What happens in this step]
- [Inputs needed]
- [Outputs produced]

**Total Time**: ~X-Y minutes (compare to [baseline])

---

## Detailed Workflow

[TODO: Write comprehensive documentation with no word limit]

### Prerequisites
- [Required tool/dependency 1]
- [Required tool/dependency 2]
- [Required knowledge/skill]

### Step-by-Step Guide

#### Step 1: [Detailed Phase Name]

**Purpose**: [Why this step matters]

**Process**:
1. [Detailed sub-step 1]
2. [Detailed sub-step 2]
3. [Detailed sub-step 3]

**Inputs**:
- Input 1: [Description, format, example]

**Outputs**:
- Output 1: [Description, format, example]

**Common Issues**:
- Issue: [Problem and solution]

[TODO: Continue for all steps]

---

## Examples

### Example 1: [Common Use Case]

**Scenario**: [Describe the situation]

**Input**:
\`\`\`
[Actual input example]
\`\`\`

**Output**:
\`\`\`
[Actual output example]
\`\`\`

**Result**: [Outcome achieved]

---

## Success Metrics

### Quantitative
- Time Reduction: [X%] faster than manual process
- Quality Improvement: [Specific measurable improvement]

### Qualitative
- Consistency: [How it standardizes the process]
- Best Practices: [What standards it follows]

---

## Version History

**v1.0.0** - $(date +%Y-%m-%d)
- Initial release
- [Key features]

---

## Next Steps

1. **Fill in TODOs**: Replace all [TODO] sections with actual content
2. **Add Examples**: Include real examples from your use case
3. **Test Triggering**: Verify Claude detects the skill
4. **Validate Function**: Test the complete workflow
5. **Measure Performance**: Compare to baseline metrics
6. **Get Feedback**: Test with target users
7. **Distribute**: Share on GitHub and community

For guidance, see:
- Template: ~/.claude/skills/claude-skill-builder/templates/SKILL-template.md
- Best Practices: ~/.claude/skills/claude-skill-builder/references/best-practices.md
EOF
    print_success "SKILL.md created"

    # Create placeholder files
    print_info "Creating placeholder files..."

    # Scripts placeholder
    cat > "$SKILL_DIR/scripts/README.md" << EOF
# Scripts Directory

Add automation scripts here (optional).

## Examples
- \`generate.py\` - Main generation script
- \`validate.sh\` - Validation script
- \`deploy.sh\` - Deployment automation

Scripts should be:
- Executable (\`chmod +x script.sh\`)
- Well-documented with usage comments
- Tested independently
- Referenced in SKILL.md
EOF

    # References placeholder
    cat > "$SKILL_DIR/references/README.md" << EOF
# References Directory

Add documentation and reference materials here (optional).

## Examples
- \`api-docs.md\` - API documentation
- \`examples.md\` - Usage examples
- \`best-practices.md\` - Guidelines
- \`troubleshooting.md\` - Common issues

References should:
- Be in markdown format
- Include links to external docs
- Provide context for the skill
- Be referenced in SKILL.md
EOF

    # Assets placeholder
    cat > "$SKILL_DIR/assets/README.md" << EOF
# Assets Directory

Add templates, configs, and other assets here (optional).

## Examples
- \`template.yaml\` - Configuration template
- \`config.json\` - Default settings
- \`sample-input.txt\` - Example input
- \`schema.json\` - Data schema

Assets should:
- Be well-formatted
- Include usage documentation
- Be versioned if they change
- Be referenced in SKILL.md
EOF

    print_success "Placeholder files created"

    # Create .gitignore
    cat > "$SKILL_DIR/.gitignore" << EOF
# Generated outputs
outputs/
*.log

# Temporary files
*.tmp
.DS_Store

# Virtual environments
venv/
.venv/
env/

# IDE files
.vscode/
.idea/
*.swp
EOF

    # Summary
    print_header "Skill Scaffolding Complete!"

    print_success "Skill created at: $SKILL_DIR"
    echo ""
    print_info "Directory structure:"
    tree -L 2 "$SKILL_DIR" 2>/dev/null || ls -R "$SKILL_DIR"
    echo ""
    print_info "Next steps:"
    echo "  1. Edit SKILL.md and replace all [TODO] sections"
    echo "  2. Update YAML frontmatter description"
    echo "  3. Add scripts/references/assets as needed"
    echo "  4. Test with: /$SKILL_NAME"
    echo "  5. Iterate based on feedback"
    echo ""
    print_info "Resources:"
    echo "  - Template: ~/.claude/skills/claude-skill-builder/templates/SKILL-template.md"
    echo "  - Guide: ~/.claude/skills/claude-skill-builder/SKILL.md"
    echo "  - Best Practices: ~/.claude/skills/claude-skill-builder/references/best-practices.md"
    echo ""
    print_success "Happy skill building! 🎉"
}

# Error handling
trap 'print_error "Scaffolding failed at line $LINENO"; exit 1' ERR

# Run main function
main "$@"
