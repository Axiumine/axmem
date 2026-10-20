# Knowledge Base Bootstrap Protocol

## Purpose
A systematic approach to populate the Forgetful knowledge base with comprehensive project context. This protocol helps you transform an undocumented or lightly-documented codebase into a rich, searchable knowledge repository.

**Use this when:**
- Starting to use a knowledge base system for an existing project
- Onboarding a new project into your memory system
- Preparing a project for AI agent collaboration
- Creating institutional knowledge for team members


---

## Prerequisites

Before starting, ensure:
- ✅ Knowledge base system is configured and accessible
- ✅ You have read access to the codebase
- ✅ You understand the project's basic purpose (run Rapid Scan if not)
- ✅ You can run the project locally (optional but helpful)

---

## Bootstrap Workflow

### Phase 0: Discovery & Assessment (ALWAYS START HERE)

**Before creating any memories, discover what already exists:**

#### Step 1: Check for Existing Project Memories
A project is a scope label on memories, not a record: use "owner/repo" as the `project` value of every memory in this run.
```
1. Use get_recent_memories or query_memory with project="[PROJECT_NAME]" to see what the scope already holds
2. If memories exist:
   - Review them; this run is a refresh
3. If none exist:
   - This is a first encode; nothing needs to be created first, the scope appears when the first memory uses it
```

#### Step 2: Assess Project Size & Scope
```
Determine project characteristics to adjust memory targets:

**Project Size Indicators:**
- Lines of code (LOC): Small <5K, Medium 5K-50K, Large >50K
- File count: Small <50, Medium 50-500, Large >500
- Contributors: Small 1-3, Medium 4-10, Large >10
- Dependencies: Small <20, Medium 20-100, Large >100

**Project Type Indicators:**
- Simple app/script: Single responsibility, minimal architecture
- Standard web app: Multi-layered, database, API
- Library/SDK: Public API, multiple consumers, versioning concerns
- Microservice: Part of larger system, integration-heavy
- Monorepo: Multiple projects/packages, shared infrastructure
- Integration/ETL: Data transformation focus, extensive mappings

**Architectural Complexity:**
- Simple: Single-layer, straightforward flow
- Moderate: Multi-layered, standard patterns
- Complex: Multiple subsystems, advanced patterns, distributed
```

#### Step 3: Assess Current KB Coverage
```
Query the knowledge base for existing content:
1. query_memory: "[project-name] architecture" with the project filter
2. query_memory: "[project-name] overview" with include_links=true
3. query_memory: "[project-name] patterns" to check pattern coverage
4. get_recent_memories: Filter by project to count total memories (total_count)
5. Note: Check auto-linking quality (similarity ≥0.7 by default)
```

#### Step 4: Analyze Current Codebase State
```
Explore the actual codebase (current state, not KB assumptions):
1. Read README.md - current features and setup
2. Read pyproject.toml/package.json - current dependencies
3. List directory structure - current organization
4. Check main entry point - current architecture
5. Scan key files - identify what exists NOW
6. Look for mapping/integration documentation:
   - docs/mappings, docs/integration folders
   - Field mapping tables (Excel, CSV, Markdown)
   - AutoMapper profiles, transformation configs
   - API contract specs, schema definitions
```

#### Step 5: Gap Analysis
```
Compare KB vs. Codebase:
- ✅ What's documented and current?
- ⚠️ What's documented but outdated?
- ❌ What's missing from KB?
- 🗑️ What memories reference non-existent code?

Create a gap analysis report:
- Memories to CREATE (missing coverage)
- Memories to UPDATE (outdated info)
- Memories to MARK OBSOLETE (code removed/changed)
- Areas well-covered (skip in bootstrap)
```

#### Step 6: Adjust Memory Targets Based on Project Profile

**Memory Count Targets by Project Profile:**

| Profile | Phase 1 | Phase 2 | Phase 3 | Phase 3.5 | Phase 4 | Phase 5 | Phase 6 | Total |
|---------|---------|---------|---------|-----------|---------|---------|---------|-------|
| **Small Simple** (CLI tool, script, simple app) | 3-5 | 3-5 | 3-5 | 0-1 | 2-4 | 2-4 | 2-3 | 15-27 |
| **Small Complex** (Library, SDK, framework) | 5-7 | 5-8 | 5-8 | 0-2 | 4-6 | 4-6 | 3-5 | 26-42 |
| **Medium Standard** (Web app, API service) | 5-10 | 10-15 | 8-12 | 0-3 | 1-2/feat | 5-10 | 5-8 | 35-60 |
| **Medium Complex** (Microservice with integrations) | 7-10 | 12-18 | 10-15 | 3-8 | 1-2/feat | 8-12 | 6-10 | 50-85 |
| **Large Monolith** (Enterprise app, single repo) | 8-12 | 15-20 | 12-18 | 2-5 | 1-2/feat | 10-15 | 8-12 | 60-100 |
| **Monorepo** (Multiple packages/projects) | 10-15 | 20-30 | 15-25 | 5-15 | 1-2/pkg | 12-20 | 10-15 | 75-150 |
| **Integration/ETL** (Data transformation focused) | 5-8 | 8-12 | 8-12 | 10-20 | 1-2/flow | 6-10 | 6-10 | 50-90 |

**Phase-Specific Adjustments:**

**Phase 1 (Foundation):** Always complete all 5 core memories, scale up for:
- Monorepos: Add memories per major package/project
- Complex domains: Add domain model overview memories
- Integration-heavy: Add systems integration overview

**Phase 2 (Architecture):** Scale based on architectural complexity:
- Simple (3-5): Just core layers (API → Logic → Data)
- Moderate (10-15): Full layered architecture memories
- Complex (15-20): Add cross-cutting concerns, subsystems
- Monorepo (20-30): Document per-package architecture + shared infrastructure

**Phase 3 (Patterns):** Scale based on code sophistication:
- Simple (3-5): Basic patterns only (error handling, validation)
- Moderate (8-12): Standard patterns suite
- Complex (12-18): Advanced patterns, custom implementations
- Library/SDK (10-15): Add API design patterns, versioning patterns

**Phase 3.5 (Mappings):** Scale based on integration scope:
- Skip entirely: Pure libraries, simple apps with no external systems
- Light (1-2): Single external API integration
- Moderate (3-8): Multiple integrations or complex transformations
- Heavy (10-20): Integration platform, ETL system, multi-system adapter
- Monorepo (5-15): Shared mapping infrastructure + per-service mappings

**Phase 4 (Features):** Scale based on feature count:
- Calculate: 1-2 memories per major feature/capability
- Simple app: 2-4 features total
- Standard app: 5-10 features
- Complex app: 10-20+ features
- Monorepo: Consider features per package

**Phase 5 (Decisions):** Scale based on architectural maturity:
- Simple (2-4): Only critical decisions (framework choice, deployment)
- Moderate (5-10): Standard decision set
- Complex (10-15): Extensive decision documentation
- Legacy (8-12): Include historical context, evolution

**Phase 6 (Configuration):** Scale based on operational complexity:
- Simple (2-3): Basic env vars, single deployment
- Moderate (5-8): Multiple environments, external services
- Complex (8-12): Feature flags, multi-region, advanced config
- Monorepo (10-15): Shared config + per-service config

**Coverage Goals (Adjusted):**
- **Small projects**: 15-30 memories
- **Medium projects**: 30-70 memories
- **Large projects**: 60-120 memories
- **Monorepos**: 75-150+ memories

**Quality > Quantity:** Better to have fewer high-quality, well-linked memories than many superficial ones.

#### Step 7: Create Bootstrap Plan
```
Based on gap analysis and project profile, prioritize:
1. Critical gaps (Phase 1 foundation missing)
2. Outdated core memories (architecture changed)
3. Missing layers/patterns (Phase 2-3)
4. Feature documentation gaps (Phase 4)
5. Nice-to-have completions (Phase 5-6)

**Output**: 
"Project Profile: [Size] [Type] with [Complexity] architecture
Target Memories: Phase 1 ([X]), Phase 2 ([Y]), Phase 3 ([Z]), Phase 3.5 ([W]), etc.
Rationale: [gap analysis summary and why these targets]"
```

**Why Phase 0 matters:**
- Prevents duplicate memories with slightly different titles
- Identifies outdated information that needs updating, not duplication
- Respects the existing project scope and memory IDs
- Ensures codebase exploration reflects CURRENT state, not KB assumptions
- Focuses effort on actual gaps, not redundant work

---

### Phase 1: Project Foundation (Create 5-10 memories)

**Project scope**

There is no project record to create. Use the label `"owner/repo-name"` as the `project` value of every memory below. The operational details a project record used to hold (architecture overview, how to run, test and debug, dependency management, key configuration, development workflow, important files) belong in the foundation memories that follow.

---

Then create foundational memories:

#### Memory 1: Project Overview
```
Title: "[Project Name] - Project Overview and Purpose"
Content: 
- What problem does this project solve?
- Who are the primary users/stakeholders?
- What makes this project unique or valuable?
- Current status (production, beta, experimental)
- Key success metrics or goals

Context: "Foundational memory providing high-level understanding of the project"
Keywords: [project-name, overview, purpose, users]
Tags: [foundation, overview]
Importance: 10
```

#### Memory 2: Technology Stack
```
Title: "[Project Name] - Technology Stack and Dependencies"
Content:
- Primary language and version
- Main framework(s) and why chosen
- Database technology and rationale
- Critical dependencies (top 5-7) with purposes
- Infrastructure/deployment approach
- Development tools (testing, linting, etc.)

Context: "Core technology decisions that shape development approach"
Keywords: [tech-stack, dependencies, framework, language]
Tags: [technology, foundation]
Importance: 9
```

#### Memory 3: Architecture Pattern
```
Title: "[Project Name] - High-Level Architecture Pattern"
Content:
- Overall architectural style (layered, hexagonal, microservices, etc.)
- Major components and their responsibilities
- Communication patterns between components
- Data flow overview
- Why this architecture was chosen

Context: "Architectural foundation that guides all implementation decisions"
Keywords: [architecture, design, structure, components]
Tags: [architecture, foundation]
Importance: 10
```

#### Memory 4: Development Setup
```
Title: "[Project Name] - Development Environment Setup"
Content:
- Prerequisites (language version, tools, etc.)
- Clone and install steps
- Configuration requirements (.env setup, etc.)
- How to run locally (commands)
- Common setup issues and solutions

Context: "Essential for onboarding new developers or setting up in new environment"
Keywords: [setup, installation, development, environment]
Tags: [development, setup]
Importance: 8
```

#### Memory 5: Testing Strategy
```
Title: "[Project Name] - Testing Approach and Strategy"
Content:
- Testing frameworks used
- Test categories (unit, integration, e2e)
- How to run tests (commands)
- Test organization structure
- Coverage expectations/goals

Context: "Guides test writing and ensures quality standards"
Keywords: [testing, quality, test-framework]
Tags: [testing, development]
Importance: 8
```

---

### Phase 2: Architectural Deep Dive (Create 10-15 memories)

One memory per major architectural layer or component.

#### Template: Layer Memory
```
Title: "[Project Name] - [Layer Name] Layer Implementation"
Content:
- Purpose of this layer
- Location in codebase (directories/files)
- Key patterns used (repository, service, etc.)
- Interaction with other layers
- Notable design decisions specific to this layer
- Extension points

Context: "Understanding this layer is essential for [specific development tasks]"
Keywords: [layer-name, pattern-name, directory-path]
Tags: [architecture, layer, implementation]
Importance: 8-9 (core layers), 7 (supporting layers)
```

**Example layers to document:**
- Routes/Controllers layer
- Service/Business Logic layer
- Repository/Data Access layer
- Models/Domain Entities
- Middleware/Interceptors
- Authentication/Authorization
- Configuration Management
- Logging and Monitoring

---

### Phase 3: Key Patterns & Practices (Create 8-12 memories)

Document recurring patterns and conventions.

#### Pattern Memory Template
```
Title: "[Project Name] - [Pattern Name] Pattern"
Content:
- What this pattern is used for
- Where it appears in the codebase (examples)
- How to implement it (step-by-step if complex)
- Why this pattern was chosen
- Common pitfalls or gotchas

Context: "This pattern is used throughout [area] for [purpose]"
Keywords: [pattern-name, design-pattern, implementation]
Tags: [pattern, best-practice]
Importance: 7-8
```

**Common patterns to document:**
- Dependency injection approach
- Error handling strategy
- Database transaction management
- Async/await patterns
- Validation approach
- Request/response transformation
- Background job processing
- Event handling
- Caching strategy

---

### Phase 3.5: Data Mappings & Transformations (Create as applicable)

**For integration projects, data transformation/ETL systems, or multi-system adapters:**

Capture field-level mappings between systems (critical for troubleshooting data issues).

#### Data Mapping Memory Template
```
Title: "[Project Name] - [Source] to [Target] Field Mappings"
Content:
- Purpose of this mapping (what systems/formats being integrated)
- Mapping methodology (manual mapping, AutoMapper, transformation functions)
- Key field mappings (organized by category):
  * Identity fields (IDs, reference numbers, external keys)
  * Demographic fields (name, DOB, contact info)
  * Financial fields (salary, allowances, deductions)
  * Dates (hire date, termination, effective dates)
  * Lookup/reference data (codes, enums, display vs store values)
- Transformation rules (format conversions, calculations, derivations)
- Special handling (nullable fields, default values, conditional mappings)
- Location of mapping definitions (code files, config, documentation)

Context: "Field mappings between [source system] and [target system] for [data type]"
Keywords: [field-mapping, data-transformation, source-system, target-system, integration]
Tags: [data-mapping, integration, transformation]
Importance: 8-9 (for integration projects), 7 (for internal mappings)
```

#### What to Document:
- **API-to-API mappings**: External API → Internal models
- **Database-to-DTO mappings**: Database schema → API response/request DTOs
- **System integration mappings**: Source system format → Target system format
- **Import/Export mappings**: File formats (CSV, XML, JSON) → Internal entities
- **Message queue mappings**: Event schema → Domain model

#### Mapping Documentation Formats:
1. **Create memories** for high-level mapping categories (e.g., "PersonHistory to PersonalDetails")
2. **Split large field mapping tables** into one memory per field group (identity, contact, compensation...)
3. **Point at the mapping code** (AutoMapper profiles, transformation functions) with `source_files`
4. **Link them together**: Use `link_memories` to connect the category memory to its field-group memories

#### Example Mapping Categories:
- Contact information mappings (email, phone, mobile, addresses)
- Employment data mappings (job title, department, manager, start/end dates)
- Compensation mappings (salary, currency, frequency, allowances, deductions)
- Personal data mappings (name, DOB, NI/SSN, gender, marital status)
- Work schedule mappings (hours, patterns, shifts, time tracking)

#### Special Considerations:
- **Naming variations**: Document if source uses `MobileTelephoneWork` but target uses `Mobile` or `WorkPhone`
- **Format transformations**: Date formats (ISO 8601 vs dd/MM/yyyy), number formats (decimal separators)
- **Value mappings**: Lookup codes ("M"/"F" vs "Male"/"Female"), enum translations
- **Null handling**: Undefined vs null vs empty string semantics
- **Validation rules**: Length limits, required fields, format patterns
- **Audit fields**: Created date, modified date, created by, modified by mappings

**When mappings exist in documentation:**
- Search codebase for mapping docs (Excel, CSV, Markdown tables, JSON config)
- Split the full mapping table into 3-5 memories, one per field group
- Reference the code that implements the mappings with `source_files`
- Link the memories to each other with `link_memories`

---

### Phase 4: Critical Features (Create 1-2 memories per major feature)

#### Feature Implementation Memory
```
Title: "[Project Name] - [Feature Name] Implementation"
Content:
- What this feature does (user perspective)
- How it's implemented (technical perspective)
- Key files and components involved
- Data flow for this feature
- Configuration options
- Known limitations or edge cases

Context: "Critical feature that [business value]"
Keywords: [feature-name, implementation, components]
Tags: [feature, implementation]
Importance: 8-9 (core features), 7 (secondary features)
```

**Create a memory for:**
- Each major user-facing feature
- Each significant API capability
- Each complex integration
- Each critical background process

---

### Phase 5: Design Decisions (Create 5-10 memories)

Capture the "why" behind important decisions.

#### Decision Memory Template
```
Title: "[Project Name] - Decision: [Topic]"
Content:
- What decision was made
- What alternatives were considered
- Why this choice was made
- What trade-offs were accepted
- When this might need to be revisited

Context: "This decision impacts [area] and was made because [constraint/requirement]"
Keywords: [decision, architecture, design, topic]
Tags: [decision, rationale, architecture-decision]
Importance: 9-10 (foundational), 8 (significant), 7 (tactical)
```

**Common decision areas:**
- Database choice
- Framework selection
- Authentication approach
- Deployment strategy
- API design (REST vs GraphQL vs gRPC)
- Monolith vs microservices
- Synchronous vs asynchronous
- Testing approach
- Monitoring/observability

---

### Phase 6: Configuration & Operations (Create 5-8 memories)

#### Configuration Memory Template
```
Title: "[Project Name] - Configuration: [Subsystem]"
Content:
- What can be configured
- Where configuration is defined (.env, config files, etc.)
- Key settings and their purposes
- Default values and recommended production values
- Configuration precedence/override rules

Context: "Configuration options for [subsystem/feature]"
Keywords: [configuration, settings, environment, subsystem]
Tags: [configuration, operations]
Importance: 7-8
```

**Document:**
- Environment variable configuration
- Database connection configuration
- External service integrations
- Feature flags/toggles
- Performance tuning options
- Deployment configuration
- Monitoring and logging configuration

---

### Phase 7: Link Everything Together

Go back and connect memories to each other using `link_memories`:

#### Linking Strategy:
```
Pattern memories → link to the feature and architecture memories that use them
Setup/deployment memories → link to the configuration memories they depend on
Architecture memories → link to the decision memories that explain them
Feature memories → link to the architecture and pattern memories they rely on
Decision memories → link to the memories they superseded or constrain
```

#### Example Updates:
```
link_memories({
  "memory_id": <memory_id>,
  "related_ids": [<related_memory_id_1>, <related_memory_id_2>]
})
```

#### Auto-Linking:
Forgetful automatically creates semantic links between memories (similarity ≥0.7 by default).
Verify link quality during validation.

---

## Memory Creation Best Practices

### Atomic Memory Principles
✅ **Do**:
- One concept per memory
- 200-400 words ideal
- Self-contained and understandable alone
- Include context field explaining relevance
- Use importance scoring honestly

❌ **Don't**:
- Combine multiple unrelated concepts
- Exceed 2000 characters (split into several atomic memories instead)
- Assume reader has other context
- Inflate importance scores

### Importance Scoring Guidelines
- **10**: Foundational architectural decisions
- **9**: Core design patterns and critical features
- **8**: Important implementations and integrations
- **7**: Standard features and patterns (default)
- **6**: Helper utilities and supporting code
- **≤5**: Rarely use (boilerplate only)

### Keyword Strategy
Choose keywords that:
- Appear in semantic searches
- Connect related concepts
- Include technical terms
- Mix specific and general terms
- Avoid stop words

### Tag Strategy
Use tags for:
- Categorization (architecture, feature, pattern)
- Status (experimental, deprecated, stable)
- Area (frontend, backend, database)
- Type (decision, implementation, guide)

---

## Execution Guidelines

After completing Phase 0 gap analysis:

- Execute phases in order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
- Skip phases where Phase 0 identified good existing coverage
- Focus effort on phases with identified gaps
- Update outdated memories as you discover them
- Mark obsolete memories that reference removed code
- Link new memories to existing related memories

---

## Validation

After completing bootstrap phases, validate your work using `query_memory`:

**Test Semantic Searches:**
```
query_memory({
  "query": "How do I add a new API endpoint?",
  "include_links": true,
  "project": "owner/repo-name"
})

query_memory({
  "query": "How does authentication work?",
  "include_links": true,
  "project": "owner/repo-name"
})

query_memory({
  "query": "How do I deploy this?",
  "query_context": "Testing deployment documentation coverage",
  "project": "owner/repo-name"
})

query_memory({
  "query": "What database patterns are used?",
  "include_links": true,
  "project": "owner/repo-name"
})

query_memory({
  "query": "How are errors handled?",
  "project": "owner/repo-name"
})
```

**If searches return poor results:** Create additional memories in those areas.

**Verify Link Quality:**
- Foundational memories should have many incoming auto-links (high similarity to other memories)
- Feature memories should link to architectural memories
- Pattern memories should link to the feature memories that use them
- Setup/deployment memories should link to the configuration memories they depend on

**Coverage Goals:**
- See Step 6 for project-specific targets (15-150 memories depending on size/type)
- Quality > Quantity: Better fewer well-linked memories than many superficial ones
- Auto-linking creates knowledge graph (verify with include_links=true)



---

## Adaptations

### By Project Size

**Small Projects (<5K LOC, <50 files):**
- **Reduce phases**: Skip or combine Phase 2 (architecture), merge Phases 3-4
- **Focus**: Foundation (Phase 1), key patterns (Phase 3), setup (Phase 6)
- **Target**: 15-30 memories total
- **Example**: CLI tool, simple script, utility library

**Medium Projects (5K-50K LOC, 50-500 files):**
- **Standard approach**: Follow all phases at suggested memory counts
- **Focus**: Full architectural coverage, pattern documentation
- **Target**: 30-70 memories total
- **Example**: Web app, API service, standard microservice

**Large Projects (>50K LOC, >500 files):**
- **Expand phases**: Increase Phase 2 (subsystems), Phase 4 (features)
- **Focus**: Deep architectural understanding, subsystem interactions
- **Target**: 60-120 memories total
- **Example**: Enterprise application, large monolith

### By Project Type

**Monorepos:**
- **Strategy**: Bootstrap in layers (shared → packages → integration)
- **Create multiple projects**: One for monorepo, one per major package
- **Phase 2 adjustment**: 20-30 memories (shared + per-package architecture)
- **Phase 3.5 adjustment**: 5-15 memories (cross-package interactions)
- **Use tags**: Add package-specific tags for filtering (e.g., `@payments`, `@auth`)
- **Target**: 75-150+ memories total
- **Example**: Nx workspace, Lerna project, multi-service repository

**Integration/ETL Projects:**
- **Emphasize Phase 3.5**: 10-20 memories for data mappings
- **Split field mapping tables and transformation specs** into one memory per field group
- **Target**: 50-90 memories total
- **Example**: SD Connect adapter, data sync service, API aggregator

**Libraries/SDKs:**
- **Emphasize Phase 3**: 10-15 memories for API design patterns
- **Phase 4 focus**: Public API surface, usage patterns
- **Phase 5 importance**: Design decisions for API stability
- **Target**: 25-45 memories total
- **Example**: npm package, Python library, framework extension

**Microservices:**
- **Phase 3.5 importance**: Service integration patterns
- **Phase 6 emphasis**: Configuration, service discovery
- **Target**: 40-70 memories per service
- **Example**: REST API service, event-driven service, background worker

**Legacy Projects:**
- **Document current state**: Not ideal architecture, include workarounds
- **Phase 5 critical**: Explain historical decisions, technical debt
- **Add "legacy" tags**: Mark known issues, planned refactorings
- **Target**: Varies by size, add 20% for legacy context
- **Example**: Inherited codebase, undocumented system

**Well-Documented Projects:**
- **Leverage existing docs**: Convert README/docs into memories
- **Extract from ADRs**: Phase 5 decisions already documented
- **Supplement gaps**: Focus on undocumented patterns, operational knowledge
- **Target**: Reduce by 30-40%, focus on tacit knowledge
- **Example**: Open-source project with good docs, mature product

### Scaling Examples

**Example 1: Small CLI Tool (15-27 memories)**
- Phase 1: 3-5 (foundation)
- Phase 2: 3-5 (simple architecture)
- Phase 3: 3-5 (core patterns)
- Phase 4: 2-4 (commands/features)
- Phase 5: 2-4 (key decisions)
- Phase 6: 2-3 (config/deployment)

**Example 2: Medium Web App (35-60 memories)**
- Phase 1: 5-10 (foundation)
- Phase 2: 10-15 (full layered architecture)
- Phase 3: 8-12 (patterns & practices)
- Phase 3.5: 2-5 (API integrations)
- Phase 4: 1-2 per major feature (10-20 total)
- Phase 5: 5-10 (design decisions)
- Phase 6: 5-8 (config & operations)

**Example 3: Large Monorepo (75-150 memories)**
- Phase 1: 10-15 (monorepo + packages)
- Phase 2: 20-30 (shared + per-package)
- Phase 3: 15-25 (shared patterns + package-specific)
- Phase 3.5: 5-15 (cross-package interactions)
- Phase 4: 1-2 per package major feature (20-40 total)
- Phase 5: 12-20 (monorepo + package decisions)
- Phase 6: 10-15 (shared + per-package config)

**Example 4: Integration/ETL System (50-90 memories)**
- Phase 1: 5-8 (foundation)
- Phase 2: 8-12 (pipeline architecture)
- Phase 3: 8-12 (transformation patterns)
- Phase 3.5: 10-20 (field mappings - this is the focus)
- Phase 4: 1-2 per data flow (10-20 total)
- Phase 5: 6-10 (integration decisions)
- Phase 6: 6-10 (connector config)

### Monorepo-Specific Guidance

For monorepos, consider bootstrapping in layers:

1. **Shared Infrastructure First** (Phase 1-3):
   - Create a project for the monorepo as a whole
   - Document shared utilities, build system, CI/CD
   - Document cross-package patterns and conventions
   
2. **Per-Package/Project Second** (Phase 1-4 per package):
   - Create a project for each significant package
   - Document package-specific architecture and features
   - Link to shared infrastructure memories
   - Use package-specific tags for filtering

3. **Integration Layer Third** (Phase 3.5):
   - Document how packages interact
   - Document shared data contracts/APIs
   - Document cross-package workflows





---

## Usage

Invoke this protocol with:

```
Bootstrap the [PROJECT_NAME] knowledge base using Forgetful MCP.

Project: [owner/repo-name]
Type: [web app / library / CLI / service / etc.]
Size: [small / medium / large]

Execute Phase 0 first to assess existing coverage.
Report your gap analysis before creating memories.
Then proceed with phases addressing identified gaps.

Goal: Create rich, interconnected knowledge graph where:
- Memories capture atomic concepts with automatic semantic linking
- The project ties everything together with operational notes
- Everything links creating navigable knowledge graph for natural language queries
```

---

## Critical Reminders

**Avoid:**
- ❌ Mega-memories (multiple concepts in one)
- ❌ Code-only without explaining why/when
- ❌ Over-linking everything to everything
- ❌ Missing context field explaining relevance
- ❌ Importance score inflation

**Ensure:**
- ✅ Atomic memories (one concept each, 200-400 words)
- ✅ Balanced content (what + why + when)
- ✅ Strategic linking (connect related concepts)
- ✅ Clear context (explain relevance)
- ✅ Honest importance scoring (70% should be 7-8)
