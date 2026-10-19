# Core Concepts

Forgetful organizes knowledge into three building blocks. Understanding when to use each one makes the difference between a cluttered knowledge dump and a useful, searchable knowledge graph.

---

## The Building Blocks

### Memories

**Atomic knowledge units** - one concept per note.

Memories are the core of Forgetful. Each memory captures a single fact, decision, preference, or insight. Think of them as the nodes in your knowledge graph.

**Important:** Memories are the only directly queryable type. Entities and Projects are reached through the memories they are linked to.

Good memories are:
- **Easily titled** - if you can't summarize it in ~10 words, it's probably not atomic
- **Self-contained** - understandable without needing to read other memories
- **Linkable** - small enough to connect precisely to related concepts

**Examples:**
- "Chose Stripe over PayPal for lower fees and better webhook support"
- "Use temperature 0.7 for creative tasks, 0.2 for factual queries"
- "Scott prefers explicit error handling over silent fallbacks"

**Limits:** 200 char title, ~300-400 words content

**Provenance tracking:** Memories can optionally track their origin for traceability:
- `source_repo` - Repository where the knowledge originated
- `source_files` - Specific files that informed the memory
- `source_url` - URL to original source material
- `confidence` - How confident the encoding agent was (0.0-1.0)
- `encoding_agent` - Which AI/process created this memory
- `encoding_version` - Version of the encoding process
- `agent_id` - Logical identity of the agent (e.g., 'CodeAgentUltra')
- `agent_version` - Version of the agent
- `agent_model` - LLM model used (e.g., 'claude-sonnet-4-6')

### Entities

**Real-world things** - people, organizations, devices, products.

Entities represent concrete nouns that exist in the world. They can have relationships with each other and link to memories. Use entities when you want to track WHO or WHAT is involved, not just the knowledge itself.

**Types:** Organization, Individual, Team, Device, System, Other (with `custom_type`)

**Examples:**
- Sarah Chen (Individual) - "Backend lead, payments team"
- TechFlow Systems (Organization) - "SaaS platform company"
- Production Server 01 (Device) - "Primary API server, us-east-1"
- GPT-4 (Product) - "OpenAI's flagship model"

**Relationships:** Entities connect to each other (e.g., "Sarah works_at TechFlow") and to memories (e.g., "Sarah" linked to "Hired Sarah for Stripe integration").

### Projects

**Organizational scope** - groups memories by context.

Projects help you filter queries to relevant knowledge. When working on the e-commerce platform, you don't need memories from the trading bot project cluttering your results.

**Examples:**
- "E-Commerce Platform Redesign" (status: active)
- "Q4 Hiring Initiative" (status: completed)
- "AI Agent Framework" (status: active)

---

## Provenance Tracking

Forgetful supports optional provenance fields on all object types so you can trace where knowledge came from and which agent encoded it. This is particularly useful when multiple agents or tools write to the same knowledge base.

### Supported Fields

| Field | Description | Example |
|-------|-------------|---------|
| `source_repo` | Repository or project the knowledge came from | `"owner/repo"` |
| `source_files` | List of file paths that informed this object | `["src/payments.py"]` |
| `source_url` | URL to the original source material | `"https://github.com/..."` |
| `confidence` | Encoding confidence score (0.0–1.0) | `0.9` |
| `encoding_agent` | Software running the agent | `"OpenCode"` |
| `encoding_version` | Version of the encoding software | `"1.3.13"` |
| `agent_id` | Logical identity of the agent | `"CodeAgentUltra"` |
| `agent_version` | Version of the agent | `"1.0"` |
| `agent_model` | LLM model the agent used | `"claude-sonnet-4-6"` |

### Coverage by Object Type

- **All 9 fields**: Memories, Projects, Entities
- **8 fields (no `confidence`)**: Entity relationships — these already carry their own relationship-level confidence
- Memories previously had 6 fields; `agent_id`, `agent_version`, and `agent_model` were added to bring them in line with all other types

### Environment-Level Defaults

Six environment variables let a server operator set provenance defaults that apply automatically to every create operation, without requiring individual agents to pass these values explicitly:

```
ENCODING_AGENT, ENCODING_VERSION, AGENT_ID, AGENT_VERSION, AGENT_MODEL, ENFORCE_ENV_OVERWRITE
```

The `apply_provenance_defaults()` utility fills in any missing provenance fields from these env values at create time.

### ENFORCE_ENV_OVERWRITE

When `ENFORCE_ENV_OVERWRITE=true`, environment defaults **override** any provenance values the calling agent provides. This lets a server operator enforce consistent provenance across a shared instance regardless of what individual agents pass in — useful for audit or compliance scenarios where you need to guarantee which tool and model encoded each object.

When `false` (the default), agent-provided values take precedence and env values only fill gaps.

---

## Decision Flow: What Goes Where?

| Question | Answer |
|----------|--------|
| Is it a person, org, device, or product? | **Entity** |
| Is it detailed analysis or a guide (>300 words)? | Several linked **Memories** |
| Is it a single fact, decision, or preference? | **Memory** |
| Does it help scope/filter other knowledge? | **Project** |

---

## The Litmus Test

### Memory vs Entity

**Entity** = a thing that EXISTS (noun you could point at)
**Memory** = knowledge ABOUT things (facts, decisions, preferences)

Ask: "Can I point at it?" If yes, probably an entity. If it's abstract knowledge, it's a memory.

- "Sarah Chen" - Entity (she exists)
- "Sarah is great at debugging async issues" - Memory (knowledge about Sarah)

### Long-form material

**Memory** = single concept, scannable, <400 words

Ask: "Is this ONE idea or MANY?" One idea = one memory. Many related ideas = several atomic memories, linked to each other.

- "Chose XTTS-v2 for voice cloning" - One memory
- "TTS Engine Evaluation comparing 5 engines with benchmarks" - One memory per engine and one for the verdict, linked together

### When to Use Projects

Create a project when you have:
- Multiple related memories that should be queried together
- Work context that you'll return to repeatedly
- A need to exclude unrelated knowledge from searches

---

## Worked Example 1: E-Commerce Project

You're building a payment system. Here's how to decompose the knowledge:

**Entities:**
- Stripe (Product) - "Payment processor API"
- PayPal (Product) - "Alternative payment processor"
- Jordan Taylor (Individual) - "Backend engineer, payments team"
- TechFlow Systems (Organization) - "The company"

**Project:**
- "E-Commerce Platform v2" (active)

**Memories (decisions and the evaluation of Stripe vs PayPal vs Square, split into atoms):**
- "Chose Stripe over PayPal: better webhooks, lower fees, superior fraud detection"
- "PCI compliance: use Stripe Elements to avoid handling raw card data"
- "Subscription billing requires Stripe Billing API, not one-time charges endpoint"
- "Jordan Taylor owns payment integration - hired specifically for Stripe experience"

**Links:**
- Memories link to → Stripe entity, PayPal entity, Jordan entity
- Memories link to → each other
- Everything scoped to → E-Commerce Platform v2 project

---

## Worked Example 2: AI Agent Project

You're developing a coding assistant agent. Here's the breakdown:

**Entities:**
- Claude Sonnet (Product) - "Anthropic's fast model"
- Claude Opus (Product) - "Anthropic's reasoning model"
- OpenAI (Organization) - "GPT provider"
- Production Agent Server (Device) - "Hosts the deployed agent"

**Project:**
- "AI Coding Assistant" (active)

**Memories (prompt engineering guidelines and decisions, split into atoms):**
- "Use Claude Sonnet for quick edits, Opus for complex refactoring"
- "Temperature 0.3 for code generation, 0.7 for explanations"
- "System prompt must include repo structure for accurate file references"
- "Context window overflow: summarize conversation history after 50k tokens"
- "Tool calls: prefer specific tools over generic bash when available"

**Links:**
- Model-related memories link to → Claude Sonnet, Claude Opus entities
- Infrastructure memories link to → Production Agent Server entity
- Everything scoped to → AI Coding Assistant project

---

## Quick Reference

| Type | Size | Contains | Links To |
|------|------|----------|----------|
| Memory | <400 words | One concept | Memories, Entities, Projects |
| Entity | N/A | Real-world thing | Other Entities, Memories, Projects |
| Project | N/A | Scope/context | Memories, Entities |

The knowledge graph emerges from these connections. Memories are the atoms; entities and projects provide structure and context.
