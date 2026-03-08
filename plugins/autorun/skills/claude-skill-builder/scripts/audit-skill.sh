#!/bin/bash
##############################################################################
# Claude Skill Auditor
#
# Validates a skill against Anthropic's best practices.
# Source: "The Complete Guide to Building Skills for Claude" (January 2026)
#
# Usage:
#   bash audit-skill.sh <skill-path>
#
# Example:
#   bash audit-skill.sh ~/.claude/skills/my-skill
#
# Score guide:
#   90-100%  No structural problems detected
#   70-89%   Minor warnings — review action items
#   50-69%   Multiple issues found — address before distributing
#   <50%     Critical structural problems — fix FAILs first
#
# Note: This is a smoke test of skill structure, not a comprehensive
# quality or activation-rate review. A passing score means the file
# structure follows conventions, not that the skill content is correct.
##############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PASSED=0
FAILED=0
WARNINGS=0

# Collect action items for summary
FAIL_ITEMS=()
WARN_ITEMS=()

print_pass() {
    echo -e "  ${GREEN}✅ PASS${NC}: $1"
    PASSED=$((PASSED+1))
}

# print_fail "issue" ["fix instruction"]
print_fail() {
    echo -e "  ${RED}❌ FAIL${NC}: $1"
    if [ -n "$2" ]; then
        echo -e "     ${CYAN}→ Fix${NC}: $2"
        FAIL_ITEMS+=("$1 — $2")
    fi
    FAILED=$((FAILED+1))
}

# print_warn "issue" ["fix instruction"]
print_warn() {
    echo -e "  ${YELLOW}⚠️  WARN${NC}: $1"
    if [ -n "$2" ]; then
        echo -e "     ${CYAN}→ Fix${NC}: $2"
        WARN_ITEMS+=("$1 — $2")
    fi
    WARNINGS=$((WARNINGS+1))
}

print_info() {
    echo -e "  ${BLUE}ℹ️  INFO${NC}: $1"
}

print_section() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

audit_skill() {
    local skill_path=$1

    if [ -z "$skill_path" ]; then
        echo "Usage: bash audit-skill.sh <skill-path>"
        echo "Example: bash audit-skill.sh ~/.claude/skills/my-skill"
        exit 1
    fi

    if [ ! -d "$skill_path" ]; then
        echo "Error: Directory not found: $skill_path"
        exit 1
    fi

    local skill_name
    skill_name=$(basename "$skill_path")
    local has_frontmatter=0
    local frontmatter=""

    print_section "Auditing: $skill_name"
    print_info "Path: $skill_path"

    # ──────────────────────────────────────────────────────────
    # 1. File Structure
    # ──────────────────────────────────────────────────────────
    print_section "1. File Structure"

    if [ -f "$skill_path/SKILL.md" ]; then
        print_pass "SKILL.md exists (correct filename and case)"
    else
        local fix_rename=""
        if [ -f "$skill_path/skill.md" ]; then
            fix_rename="mv '$skill_path/skill.md' '$skill_path/SKILL.md'"
        elif [ -f "$skill_path/readme.md" ] || [ -f "$skill_path/README.md" ]; then
            fix_rename="mv '$skill_path/README.md' '$skill_path/SKILL.md'"
        fi
        print_fail "SKILL.md not found — Claude only loads files named exactly 'SKILL.md'" "$fix_rename"
    fi

    if [ -f "$skill_path/README.md" ]; then
        # Detect if skill folder IS the GitHub repo root — README.md is acceptable there as the GitHub landing page
        local git_root
        git_root=$(git -C "$skill_path" rev-parse --show-toplevel 2>/dev/null || echo "")
        local skill_realpath
        skill_realpath=$(realpath "$skill_path" 2>/dev/null || echo "$skill_path")
        if [ "$git_root" = "$skill_realpath" ]; then
            print_pass "README.md present — OK (skill folder IS the GitHub repo root; README.md is the landing page for human visitors)"
        else
            print_warn "README.md in skill folder — Claude ignores it" \
                "Move content to SKILL.md or references/. Exception: when distributing via GitHub, a README.md at the REPO ROOT (outside the skill folder) is acceptable as a landing page for human visitors — just not inside the skill folder itself."
        fi
    else
        print_pass "No README.md in skill folder"
    fi

    if [[ "$skill_name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
        print_pass "Folder uses kebab-case: $skill_name"
        # Reserved prefix check — Anthropic reserves 'claude' and 'anthropic' prefixes for their own official skills
        if [[ "$skill_name" =~ ^(claude|anthropic) ]]; then
            print_warn "Skill name '$skill_name' starts with reserved prefix 'claude' or 'anthropic'" \
                "Anthropic reserves the 'claude' and 'anthropic' name prefixes for their own official skills — rename before public distribution (e.g., 'claude-skill-builder' → 'skill-builder')."
        fi
    else
        local kebab_fix
        kebab_fix=$(echo "$skill_name" | tr '[:upper:]' '[:lower:]' | tr '_' '-' | tr ' ' '-')
        print_fail "Folder '$skill_name' is not kebab-case" \
            "mv '$(dirname "$skill_path")/$skill_name' '$(dirname "$skill_path")/$kebab_fix'"
    fi

    # ──────────────────────────────────────────────────────────
    # 2. YAML Frontmatter
    # ──────────────────────────────────────────────────────────
    print_section "2. YAML Frontmatter"

    if [ -f "$skill_path/SKILL.md" ]; then
        has_frontmatter=$(head -1 "$skill_path/SKILL.md" | grep -c "^---$" || true)

        if [ "$has_frontmatter" -eq 1 ]; then
            print_pass "YAML frontmatter found (file starts with ---)"
            # Extract ONLY the first YAML block (lines between first and second ---).
            # Using awk instead of sed range to avoid re-triggering on markdown --- separators in the body.
            frontmatter=$(awk 'NR==1 && /^---$/{in_fm=1; next} in_fm && /^---$/{exit} in_fm{print}' "$skill_path/SKILL.md")

            # name field
            if echo "$frontmatter" | grep -q "^name:"; then
                local name_value
                name_value=$(echo "$frontmatter" | grep "^name:" | cut -d: -f2- | tr -d ' ')
                print_pass "name: $name_value"
                if [ "$name_value" = "$skill_name" ]; then
                    print_pass "name matches folder name"
                else
                    print_warn "name '$name_value' differs from folder '$skill_name'" \
                        "Either rename folder to '$name_value' or change name: to '$skill_name' in frontmatter"
                fi
            else
                print_fail "name field missing" \
                    "Add 'name: $skill_name' to frontmatter"
            fi

            # description field
            if echo "$frontmatter" | grep -q "^description:"; then
                print_pass "description field exists"

                # Extract full multi-line description value
                local desc_full
                desc_full=$(echo "$frontmatter" | awk '/^description:/{p=1; sub(/^description: */,""); print; next} p && /^  /{sub(/^ */,""); print; next} p{p=0}')
                local desc_chars
                desc_chars=$(echo "$desc_full" | tr -d '\n' | wc -c | tr -d ' ')

                # Trigger-phrase format (critical for Claude auto-activation)
                if echo "$frontmatter" | grep -q '"'; then
                    print_pass "description has quoted trigger phrases (enables Claude auto-activation)"
                else
                    print_fail "description has no quoted trigger phrases — Claude won't auto-activate without them" \
                        'Add: description: This skill should be used when user wants to "build a skill", "create a skill".'
                fi

                # 1024-character hard limit — Claude silently truncates longer descriptions, cutting off trigger phrases
                if [ "$desc_chars" -gt 1024 ]; then
                    print_fail "description is $desc_chars characters — hard limit is 1024 (Claude silently truncates longer)" \
                        "Shorten description to under 1024 characters"
                elif [ "$desc_chars" -gt 900 ]; then
                    print_warn "description is $desc_chars characters — approaching 1024-char limit" \
                        "Trim to stay under 1024; beyond that Claude silently truncates and trigger phrases may be lost"
                else
                    print_pass "description length OK ($desc_chars chars of 1024-char limit)"
                fi

                # Angle bracket check — YAML parsers reject < > in frontmatter values, silently breaking frontmatter parsing
                if echo "$frontmatter" | grep -q '[<>]'; then
                    print_fail "Angle brackets < > found in frontmatter — forbidden in YAML (causes parse errors)" \
                        "Replace < > with words: 'less than', 'greater than', or remove them"
                else
                    print_pass "No angle brackets in frontmatter"
                fi

            else
                print_fail "description field missing" \
                    'Add: description: This skill should be used when user wants to "trigger phrase", or needs help with [domain].'
            fi

            # version field
            if echo "$frontmatter" | grep -q "^version:"; then
                local version_value
                version_value=$(echo "$frontmatter" | grep "^version:" | cut -d: -f2- | tr -d ' ')
                print_pass "version: $version_value"
            else
                print_warn "version field missing" \
                    "Add 'version: 0.1.0' to frontmatter (use semantic versioning: patch/minor/major)"
            fi

        else
            print_fail "YAML frontmatter missing — file must start with ---" \
                "Insert at line 1:
     ---
     name: $skill_name
     description: This skill should be used when user wants to \"build a skill\", \"create a skill\".
     version: 0.1.0
     ---"
        fi
    fi

    # ──────────────────────────────────────────────────────────
    # 3. Progressive Disclosure
    # ──────────────────────────────────────────────────────────
    print_section "3. Progressive Disclosure"

    if [ -f "$skill_path/SKILL.md" ]; then
        local word_count
        word_count=$(wc -w < "$skill_path/SKILL.md" | tr -d ' ')
        print_info "SKILL.md word count: $word_count (hard limit: 5,000)"

        # Hard limit from Anthropic guide: SKILL.md over 5,000 words causes slow responses and degraded quality.
        # Plugin-dev guideline (not a hard rule): ideally 1,500-2,000 words; anything under 5,000 is valid.
        # Short skills that are complete and accurate for their task are fine — no minimum word count.
        if [ "$word_count" -gt 5000 ]; then
            print_fail "SKILL.md is $word_count words — above 5,000 words Claude reports slow responses and degraded quality" \
                "Move detailed sections to references/ files and add pointers: 'For details, see references/X.md'"
        elif [ "$word_count" -gt 2000 ]; then
            print_warn "SKILL.md is $word_count words — consider moving detailed content to references/ to keep context efficient" \
                "Plugin-dev guideline: ideally under 2,000 words; use references/ for schemas, examples, and deep detail"
        else
            print_pass "SKILL.md is $word_count words — within limits"
        fi

        # Level 2: How It Works (200-400 words)
        if grep -q "## How It Works" "$skill_path/SKILL.md"; then
            print_pass "Level 2: '## How It Works' section found"
        else
            print_warn "Level 2: No '## How It Works' section" \
                "Add a 200–400 word section with 3–5 numbered steps showing the workflow (inputs, outputs, time per step)"
        fi

        # Level 3: check SKILL.md body AND references/ (lean design puts it there)
        local ref_md_count=0
        if [ -d "$skill_path/references" ]; then
            ref_md_count=$(find "$skill_path/references" -maxdepth 2 -name "*.md" | wc -l | tr -d ' ')
        fi
        if grep -qi "## Detailed\|## Complete\|## Comprehensive" "$skill_path/SKILL.md"; then
            print_pass "Level 3: Detailed section in SKILL.md body"
        elif [ "$ref_md_count" -gt 0 ]; then
            print_pass "Level 3: $ref_md_count reference file(s) in references/ (lean design — detail in references/)"
        else
            print_warn "Level 3: No detailed documentation (not in body, not in references/)" \
                "Add '## Detailed Workflow' to SKILL.md, or create references/ files and link to them"
        fi

        # Examples: check SKILL.md body AND references/examples/
        local examples_file_count=0
        if [ -d "$skill_path/references/examples" ]; then
            examples_file_count=$(find "$skill_path/references/examples" -type f | wc -l | tr -d ' ')
        fi
        if grep -q "## Examples\|### Example" "$skill_path/SKILL.md"; then
            print_pass "Examples section in SKILL.md body"
        elif [ "$examples_file_count" -gt 0 ]; then
            print_pass "Examples in references/examples/ ($examples_file_count file(s))"
        else
            print_warn "No examples found" \
                "Add '## Examples' in SKILL.md or create references/examples/ with working code/templates users can copy"
        fi
    fi

    # ──────────────────────────────────────────────────────────
    # 4. Content Quality
    # ──────────────────────────────────────────────────────────
    print_section "4. Content Quality"

    if [ -f "$skill_path/SKILL.md" ]; then
        # Invocation: body phrase OR frontmatter trigger phrases
        if grep -qi "invoke with:\|to invoke:\|trigger with:" "$skill_path/SKILL.md"; then
            print_pass "Invocation phrase documented in body"
        elif [ "$has_frontmatter" -eq 1 ] && echo "$frontmatter" | grep -q '"'; then
            print_pass "Invocation covered by trigger phrases in frontmatter description"
        else
            print_warn "No invocation guidance" \
                "Add '**Invoke with:** /skill-name or ask about [topic]' near the top of SKILL.md"
        fi

        # Quantitative outcomes
        if grep -qi "faster\|reduction\|improvement\|save.*time\|[0-9][0-9]%\|percent\|metric\|minutes\|hours" "$skill_path/SKILL.md"; then
            print_pass "Quantitative outcomes or time estimates mentioned"
        else
            print_warn "No quantitative outcomes or time estimates" \
                "Add specifics: '75% faster than manual', 'reduces X from 2 hours to 15 minutes per session'"
        fi

        # Second-person prose (skills teach Claude to write — use imperative form)
        local second_person_count
        second_person_count=$(grep -c "you'll\|you should\|you need to\|What you'll\|you will\b\|You'll\|You should\|You need" "$skill_path/SKILL.md" || true)
        if [ "$second_person_count" -gt 0 ]; then
            print_warn "$second_person_count line(s) use second-person prose ('you'll', 'you should')" \
                "Rewrite as imperative form — 'What you'll do:' → 'To do this:'. Skills teach Claude to write, so use the form Claude should follow."
        else
            print_pass "No second-person prose (uses imperative form)"
        fi

        # Filler intro phrases (these add words without adding information)
        local filler_count
        filler_count=$(grep -ci "to understand\|skill should\|in this step\|in this section\|this section covers\|this section explains\|as you can see\|it is important to note\|please note that\|it is worth noting" "$skill_path/SKILL.md" || true)
        if [ "$filler_count" -gt 0 ]; then
            print_warn "$filler_count line(s) contain filler phrases ('to understand', 'skill should', 'in this step', etc.)" \
                "Remove filler intros — the step header already states context. 'To understand what the skill does:' → delete the line; the list below it stands alone."
        else
            print_pass "No filler intro phrases"
        fi

        # DRY check: content duplication between body and references/
        if [ -f "$skill_path/references/refining-skills.md" ] && grep -q "Refining\|refining" "$skill_path/SKILL.md"; then
            local refine_lines_body
            refine_lines_body=$(grep -c "refin" "$skill_path/SKILL.md" || true)
            if [ "$refine_lines_body" -gt 10 ]; then
                print_warn "SKILL.md has $refine_lines_body lines about refining AND references/refining-skills.md exists" \
                    "Remove the duplicate body content and add: 'For the refinement workflow, see references/refining-skills.md'"
            fi
        fi
    fi

    # ──────────────────────────────────────────────────────────
    # 5. Supporting Files
    # ──────────────────────────────────────────────────────────
    print_section "5. Supporting Files"

    # Scripts
    if [ -d "$skill_path/scripts" ]; then
        local script_count
        script_count=$(find "$skill_path/scripts" -type f \( -name "*.sh" -o -name "*.py" -o -name "*.js" \) | wc -l | tr -d ' ')
        if [ "$script_count" -gt 0 ]; then
            print_pass "scripts/ — $script_count executable(s)"
        else
            print_info "scripts/ exists but is empty (optional)"
        fi
    else
        print_info "No scripts/ (optional — add automation utilities that run without loading into context)"
    fi

    # References
    if [ -d "$skill_path/references" ]; then
        local ref_file_count
        ref_file_count=$(find "$skill_path/references" -name "*.md" | wc -l | tr -d ' ')
        print_pass "references/ — $ref_file_count .md file(s)"

        # Check that SKILL.md links to them — otherwise Claude won't load them
        if grep -q "references/" "$skill_path/SKILL.md" 2>/dev/null; then
            print_pass "SKILL.md links to references/ files"
        else
            print_warn "SKILL.md doesn't mention references/ — Claude won't know to load them" \
                "Add an 'Additional Resources' section listing each references/*.md with a one-line description of what's in it"
        fi
    else
        print_info "No references/ (optional — add detailed docs that Claude loads as needed)"
    fi

    # Assets
    if [ -d "$skill_path/assets" ]; then
        local asset_count
        asset_count=$(find "$skill_path/assets" -type f | wc -l | tr -d ' ')
        print_pass "assets/ — $asset_count file(s)"
    else
        print_info "No assets/ (optional — add files the skill pastes into its output: images, templates, boilerplate)"
    fi

    # ──────────────────────────────────────────────────────────
    # 6. Common Issues
    # ──────────────────────────────────────────────────────────
    print_section "6. Common Issues"

    if [ -f "$skill_path/SKILL.md" ]; then
        # TODO markers
        local todo_count
        todo_count=$(grep -c "\[TODO\]\|TODO:" "$skill_path/SKILL.md" || true)
        if [ "$todo_count" -gt 0 ]; then
            print_warn "$todo_count TODO marker(s) in SKILL.md" \
                "Complete or remove TODOs before publishing — they signal incomplete work"
        else
            print_pass "No TODO markers"
        fi

        # Unfilled template placeholders — exclude content inside code fences (teaching examples)
        local outside_code_fences
        outside_code_fences=$(awk '/^```/{in_fence=!in_fence; next} !in_fence{print}' "$skill_path/SKILL.md")
        if echo "$outside_code_fences" | grep -qi "your-skill-name-here\|replace this\|fill in\|\[domain\]\|\[trigger phrase\]"; then
            print_warn "Unfilled template placeholders detected (outside code fences)" \
                "Replace all [placeholder] text with actual content before publishing"
        else
            print_pass "No unfilled placeholders (code-fence teaching examples correctly excluded)"
        fi

        # Underscore skill names — exclude table rows (^|) and ❌ examples (intentional wrong-example markers)
        local no_bad_examples
        no_bad_examples=$(grep -v "^|" "$skill_path/SKILL.md" | grep -v "❌" || true)
        if echo "$no_bad_examples" | grep -qE "my_skill|skill_name|test_skill"; then
            print_warn "Underscore-style skill names used outside teaching examples (my_skill, skill_name)" \
                "Update to kebab-case: my-skill, skill-name"
        else
            print_pass "No underscore-style skill names outside ❌ teaching examples"
        fi
    fi

    # ──────────────────────────────────────────────────────────
    # 7. Activation Quality (based on community benchmarks)
    # See: notes/2026_03_reliable_skill_usage_and_design.md
    # Research: 250 sandboxed evals show keyword matching >> semantic matching
    # ──────────────────────────────────────────────────────────
    print_section "7. Activation Quality Hints"

    if [ -f "$skill_path/SKILL.md" ] && [ "$has_frontmatter" -eq 1 ]; then
        # Check description uses "Use this when" / "Contains" / "For" template
        # Research: This structure maximizes relevance checks at the activation layer
        local has_use_when=0
        if echo "$frontmatter" | grep -qi "use this when\|use when\|should be used when"; then
            has_use_when=1
            print_pass "Description uses 'Use this when' pattern (improves activation matching)"
        else
            print_warn "Description lacks 'Use this when' / 'should be used when' pattern" \
                "Research shows descriptions starting with 'This skill should be used when user wants to \"X\", \"Y\"' improve activation rates. See notes/2026_03_reliable_skill_usage_and_design.md"
        fi

        # Check for specific technical trigger words in description
        # Research: Specific technical terms ($state, command(), *.ts) trigger 100% activation
        # Conceptual queries ("How do X work?") fail 60-80% of the time
        local trigger_word_count
        trigger_word_count=$(echo "$frontmatter" | grep -cE '\*\.[a-z]+|[A-Z][a-z]+[A-Z]|`[a-z_]+`|\$[a-z]|command\(\)|function\(\)' || true)
        if [ "$trigger_word_count" -gt 0 ]; then
            print_pass "Description contains specific technical identifiers ($trigger_word_count found)"
        else
            print_info "Consider adding specific technical terms to description (file patterns like *.py, function names, technical keywords). Research: specific terms trigger activation more reliably than conceptual language."
        fi
    fi

    # ──────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────
    print_section "Audit Summary"
    echo ""
    echo -e "  ${GREEN}Passed${NC}:   $PASSED"
    echo -e "  ${YELLOW}Warnings${NC}: $WARNINGS"
    echo -e "  ${RED}Failed${NC}:   $FAILED"
    echo ""

    local total=$((PASSED + WARNINGS + FAILED))
    local score=0
    if [ "$total" -gt 0 ]; then
        score=$((PASSED * 100 / total))
    fi

    # Visual score bar (20 chars wide)
    local bar_fill=$((score / 5))
    local bar_empty=$((20 - bar_fill))
    local bar_color="$GREEN"
    if [ "$score" -lt 70 ]; then bar_color="$RED"; elif [ "$score" -lt 90 ]; then bar_color="$YELLOW"; fi
    printf "  Score: %s%d%%%s  [%s" "$bar_color" "$score" "$NC" "$bar_color"
    local j=0
    while [ $j -lt $bar_fill ]; do printf "█"; j=$((j+1)); done
    printf "%s" "$NC"
    local k=0
    while [ $k -lt $bar_empty ]; do printf "░"; k=$((k+1)); done
    printf "]\n\n"

    if [ "$FAILED" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
        echo -e "  ${GREEN}✨ No structural problems detected.${NC}"
    elif [ "$FAILED" -eq 0 ] && [ "$score" -ge 90 ]; then
        echo -e "  ${GREEN}✅ No failures detected — minor warnings below.${NC}"
    elif [ "$FAILED" -eq 0 ]; then
        echo -e "  ${YELLOW}⚠️  No failures detected — review warnings below.${NC}"
    else
        echo -e "  ${RED}❌ Structural problems found — fix FAILs first (skill may not activate or work correctly).${NC}"
    fi
    echo -e "  ${CYAN}Note: This is a smoke test of skill structure, not a comprehensive quality review.${NC}"
    echo ""

    # Actionable fix list
    if [ "${#FAIL_ITEMS[@]}" -gt 0 ] || [ "${#WARN_ITEMS[@]}" -gt 0 ]; then
        print_section "Action Items"
        echo ""
        local idx=1
        for item in "${FAIL_ITEMS[@]}"; do
            local issue="${item%% —*}"
            local action="${item##* — }"
            echo -e "  ${RED}[$idx] FAIL${NC}: $issue"
            echo -e "       ${CYAN}→${NC} $action"
            echo ""
            idx=$((idx+1))
        done
        for item in "${WARN_ITEMS[@]}"; do
            local issue="${item%% —*}"
            local action="${item##* — }"
            echo -e "  ${YELLOW}[$idx] WARN${NC}: $issue"
            echo -e "       ${CYAN}→${NC} $action"
            echo ""
            idx=$((idx+1))
        done
    fi

    echo "  Re-run: bash $0 $skill_path"
    echo ""
}

audit_skill "$@"
