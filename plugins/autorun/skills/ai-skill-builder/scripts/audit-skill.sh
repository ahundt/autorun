#!/bin/bash
##############################################################################
# AI Skill Auditor
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
# Release gate: zero FAILs. The printed percentage is a decided-check score —
# the share of mechanically decidable checks that passed — and it is deliberately
# not a quality verdict. A skill can score 100% and still carry contradictory
# guidance, invented numbers, and untested compatibility claims; that is what the
# "Needs a reader" section at the end of the run exists to say.
#
# Requires: bash, awk, grep, find. The code-fence check also needs python3 and
# reports itself as skipped when python3 is absent.
##############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Three classes of check, deliberately counted apart.
#
#   DECIDED  the script observed the property itself (a filename, a character
#            count, a parsed field, a path that does or does not exist). Only
#            these move the score, because only these are decidable.
#   PROXY    the script observed a stand-in for the property. A heading named
#            "## How It Works" is not a workflow, and a workflow can carry a
#            different heading. Reported, never scored.
#   REVIEW   not mechanically checkable at all. Listed so its absence from the
#            output is not mistaken for a pass.
PASSED=0
FAILED=0
WARNINGS=0
PROXY_OK=0
PROXY_MISS=0

# Collect action items for summary
FAIL_ITEMS=()
WARN_ITEMS=()
PROXY_ITEMS=()

print_pass() {
    echo -e "  ${GREEN}✅ PASS${NC}: $1"
    PASSED=$((PASSED+1))
}

# print_proxy_ok "what the stand-in showed"
print_proxy_ok() {
    echo -e "  ${BLUE}~ PROXY${NC}: $1"
    PROXY_OK=$((PROXY_OK+1))
}

# print_proxy_miss "what the stand-in did not find" ["suggestion"]
print_proxy_miss() {
    echo -e "  ${BLUE}~ PROXY${NC}: $1"
    if [ -n "$2" ]; then
        echo -e "     ${CYAN}→ Consider${NC}: $2"
        PROXY_ITEMS+=("$1 — $2")
    fi
    PROXY_MISS=$((PROXY_MISS+1))
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

    # Resolve to an absolute path before deriving the name. A relative path such as
    # '.' or './my-skill/' makes basename return '.' or a trailing-slash artifact,
    # which then fails the kebab-case and name-match checks on a valid skill.
    skill_path=$(cd "$skill_path" && pwd -P)
    # A plugin skill can document repository-owned notes alongside its package.
    # Resolve those from the Git root before calling a real pointer missing;
    # private notes outside the repository remain intentionally out of scope.
    local repo_root
    repo_root=$(git -C "$skill_path" rev-parse --show-toplevel 2>/dev/null || echo "")

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
        print_fail "SKILL.md not found — Agent Skills hosts load this exact filename" "$fix_rename"
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
            print_warn "README.md in skill folder — Agent Skills hosts do not load it as instructions" \
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
                "Anthropic reserves the 'claude' and 'anthropic' name prefixes for their own official skills — rename before public distribution (e.g., 'claude-helper' → 'agent-helper')."
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
                    print_pass "description has quoted trigger phrases (supports agent auto-activation)"
                else
                    print_fail "description has no quoted trigger phrases — agents cannot reliably auto-activate it" \
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

            # Agent Skills reserves top-level frontmatter keys. Version belongs
            # under metadata rather than at the top level.
            if echo "$frontmatter" | grep -q "^version:"; then
                local version_value
                version_value=$(echo "$frontmatter" | grep "^version:" | cut -d: -f2- | tr -d ' ')
                print_fail "top-level version field '$version_value' is not portable Agent Skills frontmatter — move it under metadata" \
                    "Move it under metadata, for example: metadata: { version: '$version_value' }"
            else
                print_pass "No unsupported top-level version field"
            fi

            # metadata.version. Optional in the specification, but a versioned skill
            # that records its version only in prose has nothing a tool can read.
            if echo "$frontmatter" | grep -qE "^\s+version:"; then
                local meta_version
                meta_version=$(echo "$frontmatter" | grep -E "^\s+version:" | head -1 | cut -d: -f2- | tr -d ' ')
                print_pass "metadata.version: $meta_version"
            elif grep -qiE "^#{1,3} +(Version History|Changelog)" "$skill_path/SKILL.md"; then
                print_fail "SKILL.md documents versions in prose but frontmatter has no metadata.version" \
                    "Add it so tools can read the version: metadata:\\n  version: 1.0.0"
            else
                print_info "No metadata.version (optional; add one when the skill starts carrying a version)"
            fi

        else
            print_fail "YAML frontmatter missing — file must start with ---" \
                "Insert at line 1:
     ---
     name: $skill_name
     description: This skill should be used when user wants to \"build a skill\", \"create a skill\".
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

        # Level 2 workflow section. PROXY, not DECIDED: a heading is not a workflow.
        # A skill can do this job under '## Workflow' or '## Process', and a skill can
        # carry the exact heading with nothing useful beneath it. Accept the common
        # heading names or any h2 followed by a numbered list of 3+ steps.
        local level2_heading level2_numbered
        level2_heading=$(grep -icE "^## (How It Works|How this works|Workflow|Process|How To Use)" "$skill_path/SKILL.md" || true)
        level2_numbered=$(grep -cE "^[0-9]+\. |^### Step [0-9]|^### Phase [0-9]" "$skill_path/SKILL.md" || true)
        if [ "$level2_heading" -gt 0 ]; then
            print_proxy_ok "Level 2: a workflow-style heading is present (heading text only; content not assessed)"
        elif [ "$level2_numbered" -ge 3 ]; then
            print_proxy_ok "Level 2: $level2_numbered numbered steps found under other headings (structure only; content not assessed)"
        else
            print_proxy_miss "Level 2: no workflow-style heading and fewer than 3 numbered steps" \
                "If the workflow lives elsewhere this is a false alarm; otherwise add 3–5 numbered steps with inputs, outputs, and time per step"
        fi

        # Level 3: check SKILL.md body AND references/ (lean design puts it there)
        local ref_md_count=0
        if [ -d "$skill_path/references" ]; then
            ref_md_count=$(find "$skill_path/references" -maxdepth 2 -name "*.md" | wc -l | tr -d ' ')
        fi
        if grep -qi "## Detailed\|## Complete\|## Comprehensive" "$skill_path/SKILL.md"; then
            # Heading text only — same limitation as the Level 2 check above.
            print_proxy_ok "Level 3: a detail-section heading is present in SKILL.md (heading text only)"
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
            print_proxy_ok "Examples heading found in SKILL.md body (heading only; content not assessed)"
        elif [ "$examples_file_count" -gt 0 ]; then
            print_pass "Examples in references/examples/ ($examples_file_count file(s))"
        else
            print_proxy_miss "No examples heading and no references/examples/ files" \
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
            print_proxy_ok "Invocation phrase documented in body (phrase match only)"
        elif [ "$has_frontmatter" -eq 1 ] && echo "$frontmatter" | grep -q '"'; then
            print_proxy_ok "Invocation covered by trigger phrases in frontmatter description"
        else
            print_proxy_miss "No invocation guidance found by phrase match" \
                "Add '**Invoke with:** /skill-name or ask about [topic]' near the top of SKILL.md"
        fi

        # Unsourced wall-clock and percentage claims. This check used to reward them,
        # passing a skill for containing "minutes" or "NN%". Wall-clock estimates depend
        # on who or what runs the workflow and are invented more often than measured, and
        # a bare percentage with no baseline is not checkable. Flag them for review instead.
        local timeclaim_count
        timeclaim_count=$(grep -cE "[0-9]+ ?(min|mins|minutes|hours|hrs)\b|[0-9]+% (faster|fewer|less|more)" "$skill_path/SKILL.md" || true)
        if [ "$timeclaim_count" -gt 0 ]; then
            print_proxy_miss "$timeclaim_count line(s) carry a wall-clock or percentage claim" \
                "Each needs a baseline, a workload, and how it was measured, or it should state a completion condition instead. Pattern-matched only; verify each by hand."
        else
            print_proxy_ok "No bare wall-clock or percentage claims found (pattern match only)"
        fi

        # Second-person prose (skills teach Claude to write — use imperative form)
        local second_person_count
        second_person_count=$(grep -c "you'll\|you should\|you need to\|What you'll\|you will\b\|You'll\|You should\|You need" "$skill_path/SKILL.md" || true)
        if [ "$second_person_count" -gt 0 ]; then
            print_proxy_miss "$second_person_count line(s) use second-person prose ('you'll', 'you should')" \
                "Rewrite as imperative form — 'What you'll do:' → 'To do this:'. Skills teach Claude to write, so use the form Claude should follow."
        else
            print_proxy_ok "No second-person prose matched (pattern match only)"
        fi

        # Filler intro phrases (these add words without adding information)
        local filler_count
        filler_count=$(grep -ci "to understand\|skill should\|in this step\|in this section\|this section covers\|this section explains\|as you can see\|it is important to note\|please note that\|it is worth noting" "$skill_path/SKILL.md" || true)
        if [ "$filler_count" -gt 0 ]; then
            print_proxy_miss "$filler_count line(s) contain filler phrases ('to understand', 'skill should', 'in this step', etc.)" \
                "Remove filler intros — the step header already states context. 'To understand what the skill does:' → delete the line; the list below it stands alone."
        else
            print_proxy_ok "No filler intro phrases matched (pattern match only)"
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

    # Internal links.
    #
    # From SKILL.md this is DECIDED: every path SKILL.md names is a pointer the
    # agent is told to follow, so a missing file is a defect with no ambiguity.
    #
    # From a reference file it is a PROXY. Reference files carry teaching examples
    # that name files of the skill *being built* (`references/policy.md`), and a
    # changelog names paths that were deliberately removed. No script can tell
    # those from a real broken pointer, so they are reported and never scored.
    collect_broken_links() {
        local f=$1 dir target out=""
        dir=$(dirname "$f")
        while IFS= read -r target; do
            [ -n "$target" ] || continue
            case "$target" in /*|~*|*" "*) continue ;; esac
            if [ ! -e "$dir/$target" ] && [ ! -e "$skill_path/$target" ] && \
               { [ -z "$repo_root" ] || [ ! -e "$repo_root/$target" ]; }; then
                out="${out}${f#"$skill_path"/} → $target; "
            fi
        done < <(awk '/^```/{fence=!fence; next} !fence' "$f" \
                 | grep -oE '`(\.\./)?(references|scripts|assets|notes|templates)/[^`]*\.(md|sh|py|pdf)`' \
                 | tr -d '`' | sort -u)
        printf '%s' "$out"
    }

    if [ -f "$skill_path/SKILL.md" ]; then
        local skill_broken ref_broken=""
        skill_broken=$(collect_broken_links "$skill_path/SKILL.md")
        if [ -n "$skill_broken" ]; then
            print_fail "SKILL.md names files that do not exist" "$skill_broken"
        else
            print_pass "Every path SKILL.md names resolves on disk"
        fi

        while IFS= read -r mdfile; do
            [ -f "$mdfile" ] || continue
            ref_broken="${ref_broken}$(collect_broken_links "$mdfile")"
        done < <(find "$skill_path/references" -name "*.md" 2>/dev/null)

        if [ -n "$ref_broken" ]; then
            print_proxy_miss "Unresolved paths named in reference files: $ref_broken" \
                "Some of these are teaching examples naming files of the skill being built, or historical paths in a changelog. Check each by hand."
        else
            print_proxy_ok "No unresolved paths named in reference files"
        fi

        # Destructive commands in shell examples. A skill's examples get run.
        if awk '/^```(bash|sh)/{f=1; next} /^```/{f=0} f' "$skill_path/SKILL.md" \
             $(find "$skill_path/references" -name "*.md" 2>/dev/null) 2>/dev/null \
             | grep -qE '(^|[^a-zA-Z_-])rm (-[rRfi]+ )?[~/$]'; then
            print_fail "A shell example runs 'rm' on a real path" \
                "Use 'trash' or move to a backup. 'rm' destroys the only copy and the record of when it went."
        else
            print_pass "No 'rm' on real paths in shell examples"
        fi

        # Code-fence structure. Both failures below are decidable from the text
        # and both silently corrupt rendering, so a reader sees instructions as
        # code or code as prose without any error anywhere.
        #
        #   Unbalanced: an odd number of fence lines leaves the tail of the file
        #   inside a code block.
        #   Nested: CommonMark closes a fence at the first fence of the same
        #   length, so ```bash written inside ```markdown ends the outer block
        #   early. Nesting requires a longer outer fence (````).
        local fence_report
        if ! command -v python3 >/dev/null 2>&1; then
            print_info "Code-fence check skipped — python3 not found on PATH"
            fence_report="__skipped__"
        else
        fence_report=$(python3 - "$skill_path" <<'PY' 2>/dev/null || true
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
files = [root / "SKILL.md"] + sorted(root.glob("references/**/*.md"))
for f in files:
    if not f.is_file():
        continue
    rel = f.relative_to(root)
    # CommonMark: a fenced block ends at the first fence line that has no info
    # string and is at least as long as its opener. A fence line carrying an
    # info string is literal text inside a block, never a closer.
    inside, opener = False, None
    for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
        m = re.match(r"^(`{3,})(\s*\S+)?\s*$", line)
        if not m:
            continue
        ticks, info = len(m.group(1)), (m.group(2) or "").strip()
        if not inside:
            inside, opener = True, (i, ticks, info)
        elif not info and ticks >= opener[1]:
            inside = False
        elif info and ticks == opener[1]:
            # Same-length nesting. The inner fence is literal, and the next bare
            # fence of this length ends the OUTER block early.
            print(f"{rel}:{i} ```{info} nested inside the same-length ```"
                  f"{opener[2] or 'plain'} block opened at line {opener[0]}")
    if inside:
        print(f"{rel}: block opened at line {opener[0]} (```{opener[2] or 'plain'}) is never closed")
PY
)
        fi
        if [ "$fence_report" = "__skipped__" ]; then
            :
        elif [ -n "$fence_report" ]; then
            print_fail "Code fences do not nest or balance: $(echo "$fence_report" | tr '\n' '; ')" \
                "Close every fence, and widen an outer fence that contains another to four backticks (\`\`\`\`)."
        else
            print_pass "Code fences balance and none nests inside a same-length fence"
        fi

        # open() on a tilde path in python examples: Python does not expand ~.
        if awk '/^```python/{f=1; next} /^```/{f=0} f' "$skill_path/SKILL.md" \
             $(find "$skill_path/references" -name "*.md" 2>/dev/null) 2>/dev/null \
             | grep -qE "open\(['\"]~"; then
            print_fail "A Python example calls open() on a '~' path, which always raises FileNotFoundError" \
                "Python does not expand '~'. Pass the path as an argument, or use os.path.expanduser()."
        else
            print_pass "No Python open() calls on unexpanded '~' paths"
        fi
    fi

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
    # 7. Activation shape
    #
    # Both checks here are PROXY. They match the shape of a description, and
    # nothing in this script observes whether a host actually activated the
    # skill. Only a triggering test on the target host does that.
    #
    # Local validation receipts and personal notes are intentionally outside the
    # distributable skill. This section checks only activation-shape heuristics;
    # it must not turn private measurements into packaged claims.
    # ──────────────────────────────────────────────────────────
    print_section "7. Activation shape (proxy checks)"

    if [ -f "$skill_path/SKILL.md" ] && [ "$has_frontmatter" -eq 1 ]; then
        # "Use when" phrasing states the activation condition in the field the
        # host matches against. Both documented description formats include it.
        if echo "$frontmatter" | grep -qi "use this when\|use when\|should be used when"; then
            print_proxy_ok "Description states an activation condition ('use when' phrasing matched)"
        else
            print_proxy_miss "Description has no 'use when' / 'should be used when' phrasing" \
                "State the condition explicitly, then quote the phrases: 'Generates X from Y. Use when user asks to \"A\", \"B\".' See references/best-practices.md, section 'Description Writing Formula'."
        fi

        # Concrete identifiers (*.py, camelCase, `backticked`, $vars) give a host
        # something literal to match. Counted by line, shape only.
        local trigger_word_count
        trigger_word_count=$(echo "$frontmatter" | grep -cE '\*\.[a-z]+|[A-Z][a-z]+[A-Z]|`[a-z_]+`|\$[a-z]|command\(\)|function\(\)' || true)
        if [ "$trigger_word_count" -gt 0 ]; then
            print_proxy_ok "Description carries concrete identifiers on $trigger_word_count line(s) (pattern match only)"
        else
            print_proxy_miss "Description carries no concrete identifiers (file patterns, function names, flags)" \
                "Add the literal terms a user would type — '*.py', 'SKILL.md', 'pytest' — alongside the natural-language phrases."
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

    # Score counts DECIDED checks only. Proxies are heuristics; folding them in
    # produced a number that read as a quality verdict while measuring headings.
    local total=$((PASSED + WARNINGS + FAILED))
    local score=0
    if [ "$total" -gt 0 ]; then
        score=$((PASSED * 100 / total))
    fi

    # Visual score bar (20 chars wide). The colour variables hold literal '\033'
    # text, so they must sit in printf's FORMAT string, where printf interprets
    # the escape. Passing them as %s arguments prints the escape verbatim.
    local bar_fill=$((score / 5))
    local bar_empty=$((20 - bar_fill))
    local bar_color="$GREEN"
    if [ "$score" -lt 70 ]; then bar_color="$RED"; elif [ "$score" -lt 90 ]; then bar_color="$YELLOW"; fi
    printf "  Decided-check score: ${bar_color}%d%%${NC}  [${bar_color}" "$score"
    local j=0
    while [ $j -lt $bar_fill ]; do printf "█"; j=$((j+1)); done
    printf "${NC}"
    local k=0
    while [ $k -lt $bar_empty ]; do printf "░"; k=$((k+1)); done
    printf "]\n"
    echo -e "  ${BLUE}Proxy checks${NC}: $PROXY_OK matched, $PROXY_MISS did not (heuristics, not scored)"
    echo ""

    if [ "$FAILED" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
        echo -e "  ${GREEN}✨ No structural problems detected.${NC}"
    elif [ "$FAILED" -eq 0 ] && [ "$score" -ge 90 ]; then
        echo -e "  ${GREEN}✅ No failures detected — minor warnings below.${NC}"
    elif [ "$FAILED" -eq 0 ]; then
        echo -e "  ${YELLOW}⚠️  No failures detected — review warnings below.${NC}"
    else
        echo -e "  ${RED}❌ Structural problems found — fix FAILs first (skill may not activate or work correctly).${NC}"
    fi
    echo -e "  ${CYAN}This is a structural smoke test. A clean run means the files are shaped${NC}"
    echo -e "  ${CYAN}correctly, not that the guidance inside them is correct or consistent.${NC}"
    echo ""

    # ──────────────────────────────────────────────────────────
    # What this script cannot check
    # ──────────────────────────────────────────────────────────
    print_section "Needs a reader (not checkable by this script)"
    echo ""
    echo "  Every defect below has been found by hand in a shipped skill, including in"
    echo "  ai-skill-builder itself. None is visible to any check above. Read for them."
    echo ""
    echo "  1. Guidance that contradicts other guidance in the same package — one"
    echo "     reference prescribing what another calls a defect."
    echo "  2. Guidance that contradicts this script. Where they disagree, the"
    echo "     script is checkable and the prose is not; fix the prose."
    echo "  3. Numbers presented as measurements with no baseline, workload, or"
    echo "     source. A percentage with nothing behind it is not checkable."
    echo "  4. Compatibility claimed for hosts nobody tested."
    echo "  5. Whether a reference is actually loaded under the condition its"
    echo "     pointer names, and whether reading it changes the outcome."
    echo "  6. Whether the workflow is correct for the skill's actual domain."
    echo "  7. What a script under scripts/ writes. This run reads the skill you"
    echo "     pointed it at, not the output of anything in it. If a script"
    echo "     generates skill content, run it and audit the result — that is how"
    echo "     ai-skill-builder found its own scaffolder emitting a skill this"
    echo "     script fails."
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

case "${1:-}" in
    -h|--help)
        echo "Usage: bash audit-skill.sh <skill-path>"
        echo ""
        echo "Audits one skill directory against the portable Agent Skills structure."
        echo ""
        echo "Exit codes:"
        echo "  0  zero structural FAILs (warnings and proxy misses do not change it)"
        echo "  1  no path given, path is not a directory, or one or more FAILs"
        echo ""
        echo "A zero exit means the files are shaped correctly. It does not mean the"
        echo "guidance inside them is correct — see 'Needs a reader' in a full run."
        exit 0
        ;;
esac

# `set -e` is on, so a nonzero return from audit_skill would exit before this.
# The FAIL count is the release gate: a structural failure that exits 0 reads to
# CI and to a human as a clean run.
audit_skill "$@"
if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
