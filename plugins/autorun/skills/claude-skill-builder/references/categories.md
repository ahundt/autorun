# Skill Categories — In-Depth Guide

From Anthropic's "The Complete Guide to Building Skills for Claude" (January 2026), pages 8-9.

Three official skill categories determine the architecture and workflow of any skill. Choose the
category that best matches the primary input-to-output transformation.

---

## Category 1: Document & Asset Creation

**Core pattern**: Structured INPUT → Analysis → Generation → Structured OUTPUT

### When to Use

Use Category 1 when the skill takes data, specifications, or content as input and produces a
document, code file, diagram, or report as output. The key characteristic: the output is a
durable artifact the user keeps or deploys.

### Characteristics

- **Input**: Specs, requirements, data, raw content, templates
- **Process**: Parse input → extract structure → apply templates → validate → format
- **Output**: Document, code, diagram, report, test suite, deployment manifest
- **Time profile**: Predictable; scales with input size

### Examples

| Use Case | Input | Output |
|----------|-------|--------|
| API test generator | OpenAPI spec | Jest/Pytest test suite |
| Meeting notes summarizer | Audio transcript | Structured meeting notes + action items |
| PR description generator | Git diff | Pull request description |
| API documentation builder | Source code | Markdown documentation |
| Deployment manifest creator | Service config | Kubernetes YAML |
| Report generator | CSV data | Formatted PDF/Markdown report |

### Recommended Structure

```
my-doc-skill/
├── SKILL.md                    # Workflow overview
├── references/
│   ├── output-templates.md     # Template formats for outputs
│   ├── examples/
│   │   └── example-output.md   # Real example of expected output
│   └── validation-rules.md     # What makes output valid
└── scripts/
    └── validate-output.sh      # Structural validation of output
```

### Best Practices

1. **Define input format precisely**: List required fields, accepted file types, example inputs
2. **Provide output examples**: Show a complete, working example of the expected output
3. **Include validation steps**: How to verify the generated artifact is correct
4. **Support multiple output formats**: e.g., Jest vs Pytest, Markdown vs PDF
5. **Handle missing or partial inputs gracefully**: What to skip vs what to require

### SKILL.md Level 2 Template (Workflow)

```markdown
## How It Works

Generate [output type] from [input type] in 3 steps:

### Step 1: Parse Input (1-2 min)
- Read and validate the [input type]
- Extract [key fields] from the structure
- Identify [special conditions or options]

### Step 2: Generate [Output] (3-5 min)
- Create [output structure] from extracted data
- Apply [templates or patterns] to each [element]
- Add [supporting elements] (auth, error handling, etc.)

### Step 3: Validate and Format (1-2 min)
- Check output for [correctness criteria]
- Format to [output spec]
- Output to [location]

**Total Time**: ~5-10 minutes for [typical input size]
```

---

## Category 2: Workflow Automation

**Core pattern**: Task Parameters → Orchestration → Sequential/Parallel Execution → Status Report

### When to Use

Use Category 2 when the skill coordinates multiple steps, tools, or services to complete a
multi-phase process. The key characteristic: the skill manages state across steps and handles
failures at each stage.

### Characteristics

- **Input**: Task parameters, configuration, context about what to do
- **Process**: Pre-flight checks → execute phases → verify results → handle errors → report
- **Output**: Completed workflow + status report (what succeeded, what failed, next steps)
- **Time profile**: Variable; depends on external services and error recovery

### Examples

| Use Case | Input | Output |
|----------|-------|--------|
| Deploy to production | App name + version | Deployment confirmation + health check |
| Data migration pipeline | Source + destination config | Migration report with row counts |
| Code review workflow | PR number | Review comments + approval decision |
| Multi-service health check | Service list | Health dashboard + alert summary |
| Database schema migration | Migration file | Applied changes + rollback script |
| Release automation | Release config | Tagged release + changelog + notification |

### Recommended Structure

```
my-workflow-skill/
├── SKILL.md                    # Workflow phases + error handling
├── references/
│   ├── phases.md               # Detailed description of each phase
│   ├── error-recovery.md       # What to do when each phase fails
│   └── examples/
│       └── example-run.md      # Sample successful + failed run
└── scripts/
    ├── pre-flight-check.sh     # Validate preconditions
    ├── execute-phase-N.sh      # Phase execution scripts
    └── rollback.sh             # Undo changes if workflow fails
```

### Best Practices

1. **Define pre-flight checks explicitly**: What must be true before starting (credentials, config, etc.)
2. **Make each phase atomic**: Either fully succeeds or leaves system unchanged
3. **Add progress indicators**: Users need to know what's happening during long operations
4. **Always provide rollback capability**: Every destructive step needs an undo
5. **Structure error messages with next actions**: Not "failed" but "failed at step 3, run rollback.sh"

### SKILL.md Level 2 Template (Workflow)

```markdown
## How It Works

[Describe task] in [N] phases:

### Phase 1: Pre-flight Checks (1-2 min)
- Verify [credentials/config/dependencies]
- Check [required state] is correct
- Confirm [safety conditions] before proceeding

### Phase 2: [Primary Action] (X-Y min)
- [Main operation step 1]
- [Main operation step 2]
- Verify [intermediate result]

### Phase 3: Validate and Report (1-2 min)
- Check [success criteria]
- Generate status report
- If failure: provide rollback instructions

**On failure**: Run `scripts/rollback.sh` to restore previous state
**Total Time**: ~X-Y minutes (varies with [variable factor])
```

---

## Category 3: MCP Enhancement

**Core pattern**: MCP Tool Outputs → Composition Layer → AI Orchestration → Enhanced Results

### When to Use

Use Category 3 when the skill adds intelligence or coordination on top of existing MCP server
capabilities. The key characteristic: the skill makes MCP tools smarter by combining them,
adding caching, or applying domain knowledge that the raw tools lack.

### Characteristics

- **Input**: MCP tool configurations, user queries, tool outputs
- **Process**: Discover available tools → compose operations → apply intelligence → cache results
- **Output**: Richer results than any single MCP tool could produce alone
- **Time profile**: Varies; caching dramatically improves repeat queries

### Examples

| Use Case | Input | Output |
|----------|-------|--------|
| Smart file search | User query | Relevant files with semantic context |
| BigQuery assistant | Natural language question | SQL query + results + interpretation |
| Multi-database sync | Sync config | Sync report with conflict resolution |
| Composite API orchestrator | Business operation | Coordinated API calls + unified result |
| Caching layer for slow tools | Tool + query | Cached result with freshness indicator |
| Schema-aware query builder | Table name + intent | Validated SQL with schema checks |

### Recommended Structure

```
my-mcp-skill/
├── SKILL.md                    # MCP dependencies + orchestration logic
├── references/
│   ├── mcp-integration.md      # How to configure required MCP servers
│   ├── schema.md               # Domain schema (e.g., database tables)
│   └── examples/
│       └── example-queries.md  # Real queries and expected results
```

### Best Practices

1. **Document MCP dependencies explicitly**: List every MCP server the skill requires with setup instructions
2. **Handle MCP tool failures gracefully**: What to do if a required tool is unavailable
3. **Cache expensive operations**: Use filesystem or memory to avoid redundant MCP calls
4. **Document which tools are optional vs required**: Skill should degrade gracefully if optional tools are absent
5. **Test without real MCP servers**: Provide mock data in references/examples/ for development

### SKILL.md Level 2 Template (Workflow)

```markdown
## How It Works

Enhance [MCP capability] with [intelligence layer]:

### Required MCP Servers
- **[server-name]**: [What it provides] — Install: `[install command]`
- **[server-name]**: [What it provides] — Install: `[install command]`

### Step 1: Discover and Configure (auto)
- Detect available MCP tools from [server list]
- Load [schema/config] from `references/schema.md`
- Initialize cache if available

### Step 2: Orchestrate Query (1-5 sec)
- Decompose user query into [tool-specific operations]
- Execute [tool A] for [data type A]
- Combine with [tool B] for [data type B]

### Step 3: Apply Intelligence and Return (1-2 sec)
- Apply [domain knowledge] to interpret results
- Format response with [context and explanations]
- Cache result with [TTL] for repeat queries
```

---

## Choosing Between Categories

| Question | If YES → |
|----------|----------|
| Does output live in a file/repo after the skill runs? | Category 1 |
| Does the skill execute real actions (deploy, migrate, delete)? | Category 2 |
| Does the skill primarily coordinate or enhance MCP tools? | Category 3 |
| Does the skill call external APIs or services? | Usually Category 2 |
| Does the skill generate code the user will use? | Category 1 |
| Does the skill need rollback capabilities? | Category 2 |

**When in doubt**: Category 1 if the output is a document, Category 2 if the output is a completed action.

---

## Source

Anthropic, "The Complete Guide to Building Skills for Claude," January 2026, pages 8-9.
