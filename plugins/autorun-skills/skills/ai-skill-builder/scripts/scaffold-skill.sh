#!/bin/bash
##############################################################################
# AI Skill Scaffolder
#
# Creates a new Agent Skill with proper structure and templates
#
# Usage:
#   bash scaffold-skill.sh <skill-name> [category]
#
# Arguments:
#   skill-name  - Name in kebab-case (e.g., api-test-generator)
#   category    - Optional: document, workflow, or mcp (default: document)
#
# Environment:
#   SKILLS_DIR  - Discovery root to scaffold into. Default ~/.claude/skills.
#                 Set it for another host: SKILLS_DIR=~/.agents/skills bash scaffold-skill.sh x
#
# Example:
#   bash scaffold-skill.sh my-awesome-skill document
#
# The generated SKILL.md is written to pass scripts/audit-skill.sh with zero
# FAILs before any TODO is filled in. If a change here breaks that, the two
# scripts have started disagreeing; fix this one.
#
# Portability: POSIX-compatible parameter expansion only. macOS ships bash
# 3.2.57 as /bin/bash, where bash-4 forms such as ${var^} are a syntax error.
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
    print_header "AI Skill Scaffolder"

    # Parse arguments
    SKILL_NAME=$1
    CATEGORY=${2:-document}

    # Validate skill name
    validate_skill_name "$SKILL_NAME"

    # Get category info
    get_category_info "$CATEGORY"

    # Set paths. SKILLS_DIR is overridable because discovery roots differ by
    # host — see the target-host questions in SKILL.md Step 1.
    SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
    SKILL_DIR="$SKILLS_DIR/$SKILL_NAME"

    # Title for the H1. Written with tr and awk rather than ${SKILL_NAME^}:
    # that form needs bash 4, and macOS /bin/bash is 3.2.57.
    SKILL_TITLE=$(echo "$SKILL_NAME" | tr '-' ' ' \
        | awk '{for (i = 1; i <= NF; i++) $i = toupper(substr($i, 1, 1)) substr($i, 2)} 1')

    print_info "Skill name: $SKILL_NAME"
    print_info "Category: $CATEGORY_NAME"
    print_info "Target directory: $SKILL_DIR"
    echo ""

    # Check if directory exists
    if [ -d "$SKILL_DIR" ]; then
        print_warning "Skill directory already exists: $SKILL_DIR"
        read -p "Move the existing directory aside and continue? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Cancelled"
            exit 0
        fi
        # Never rm -rf a directory the user may have worked in. This package
        # tells skill authors to use trash instead of rm, and audit-skill.sh
        # fails a skill whose examples do otherwise.
        if command -v trash >/dev/null 2>&1; then
            trash "$SKILL_DIR"
            print_success "Existing directory moved to trash"
        else
            SKILL_BACKUP="${SKILL_DIR}.bak.$(date +%Y-%m-%d-%H%M%S)"
            mv "$SKILL_DIR" "$SKILL_BACKUP"
            print_success "Existing directory moved to $SKILL_BACKUP"
        fi
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
description: TODO one sentence on what this produces. Use when user asks to
  "$SKILL_NAME", "TODO second natural phrase", or "TODO third natural phrase".
metadata:
  version: 0.1.0
---

# $SKILL_TITLE

<purpose>

[TODO: Write 50-100 word hook explaining what this skill does and who it's for]

This skill [describe what it produces in one sentence].

**Use this skill when:** [Specific scenario when this is useful]
**Invoke with:** \`/$SKILL_NAME\` or "[Natural language trigger phrase]"

**Category**: $CATEGORY_NAME

</purpose>

<workflow>

## How It Works

[TODO: Write 200-400 word workflow overview with 3-5 steps]

This skill follows these steps:

### Step 1: [Phase Name]
- [What happens in this step]
- [Inputs needed]
- [Outputs produced]
- Definition of Done: [what must be true before Step 2 starts]

### Step 2: [Phase Name]
- [What happens in this step]
- [Inputs needed]
- [Outputs produced]
- Definition of Done: [what must be true before Step 3 starts]

### Step 3: [Phase Name]
- [What happens in this step]
- [Inputs needed]
- [Outputs produced]
- Definition of Done: [what must be true before this skill reports success]

**Definition of Done for the whole workflow**: [the checkable end state]

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

</workflow>

<examples>

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

</examples>

<success_criteria>

## Success Metrics

Measure the manual baseline before building, then state each number with its workload, its
unit, and which direction is better. A figure with none of those is not checkable.

### Quantitative
- [What was measured]: [manual baseline] → [with this skill], over [workload]
- [What was measured]: [manual baseline] → [with this skill], over [workload]

### Qualitative
- Consistency: [How it standardizes the process]
- Best Practices: [What standards it follows]

</success_criteria>

<authoring_notes>

## Next Steps

Delete this region once the skill is written; it instructs the author, not the agent.

1. **Fill in TODOs**: Replace all [TODO] sections with actual content
2. **Add Examples**: Include real examples from your use case
3. **Test Triggering**: Verify the target agent detects the skill
4. **Validate Function**: Test the complete workflow
5. **Measure Performance**: Compare to baseline metrics
6. **Get Feedback**: Test with target users
7. **Distribute**: Share on GitHub and community

Record the version in \`metadata.version\` above and the release history in a changelog file.

For guidance, read these inside the ai-skill-builder skill directory. The paths are relative to
that skill, not to this one, so they will not resolve from here:
- Template: ai-skill-builder/references/examples/SKILL-template.md
- Best practices: ai-skill-builder/references/best-practices.md

Keep every major section inside a balanced, descriptive XML region as above (\`<purpose>\`,
\`<workflow>\`, \`<examples>\`, \`<success_criteria>\`); the audit fails a \`## \` heading that sits
outside every region. Rename regions to fit the task; do not add depth the task does not need.

Validate this file: \`bash ai-skill-builder/scripts/audit-skill.sh $SKILL_DIR\`

</authoring_notes>
EOF
    print_success "SKILL.md created"

    # No placeholder files are written. Earlier versions dropped a README.md in
    # scripts/, references/, and assets/. Two problems: this package's own rule
    # is that a skill folder carries no README.md, and every .md under
    # references/ is loadable context, so a file whose content is "add
    # documentation here" costs tokens to say nothing. The guidance is printed
    # to the terminal instead, where the author reads it once.
    echo ""
    print_info "What goes in each directory:"
    echo "  scripts/     — executables the agent runs without loading into context"
    echo "                 (validators, generators). chmod +x them."
    echo "  references/  — markdown the agent loads on demand (schemas, API docs,"
    echo "                 policies). Every file here costs context when read, so"
    echo "                 name the condition for reading it in SKILL.md."
    echo "  assets/      — files the skill pastes into its output (templates,"
    echo "                 images, boilerplate). Never loaded as instructions."
    echo "  Reference each one from SKILL.md, or the agent will not know it exists."

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
    echo "  1. Edit SKILL.md and replace every [TODO] section"
    echo "  2. Rewrite the description: what it produces, then the phrases users say"
    echo "  3. Add scripts, references, and assets as needed, and link them from SKILL.md"
    echo "  4. Test triggering: /$SKILL_NAME, then a natural phrase, then one that must NOT match"
    echo "  5. Re-run the audit until it reports zero FAILs"
    echo ""
    print_info "Resources, relative to the ai-skill-builder skill directory:"
    echo "  - Guide:          SKILL.md"
    echo "  - Template:       references/examples/SKILL-template.md"
    echo "  - Best practices: references/best-practices.md"
    echo "  - Validate:       bash scripts/audit-skill.sh '$SKILL_DIR'"
}

# Error handling
trap 'print_error "Scaffolding failed at line $LINENO"; exit 1' ERR

# Run main function
main "$@"
