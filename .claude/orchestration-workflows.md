# Orchestration Workflows

## Task Routing Decision Tree

```
                    ┌─────────────────┐
                    │  New Task/Request│
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ What type of    │
                    │ task is this?   │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
    ┌────▼────┐        ┌────▼────┐        ┌────▼────┐
    │  Infra  │        │  GPU/   │        │ General │
    │Docker/CI│        │ RunPod  │        │  Other  │
    └────┬────┘        └────┬────┘        └────┬────┘
         │                  │                  │
    ┌────▼────┐        ┌────▼────┐             │
    │ EMILIA  │        │  LUNA   │             │
    │  Infra  │        │ RunPod  │             │
    │Supervisor│       │Supervisor│            │
    └─────────┘        └─────────┘             │
                                               │
         ┌───────────────────┬─────────────────┤
         │                   │                 │
    ┌────▼────┐        ┌────▼────┐       ┌────▼────┐
    │  Bug?   │        │ Design? │       │  Docs?  │
    │ Debug?  │        │ Complex?│       │  Only   │
    └────┬────┘        └────┬────┘       └────┬────┘
         │                  │                 │
    ┌────▼────┐        ┌────▼────┐       ┌────▼────┐
    │  VERA   │        │  ADA    │       │  PENNY  │
    │Detective│        │Architect│       │ Scribe  │
    └─────────┘        └─────────┘       └─────────┘
```

## Supervisor 9-Step Workflow

Every supervisor follows this workflow:

```
┌─────────────────────────────────────────────────────────────┐
│                    SUPERVISOR WORKFLOW                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. GET TASK                                                │
│     └─► mcp__vibe_kanban__get_task(task_id)                │
│                                                             │
│  2. UPDATE STATUS                                           │
│     └─► mcp__vibe_kanban__update_task(status: "inprogress")│
│                                                             │
│  3. SCOUT                                                   │
│     └─► Task(subagent: "ivy-scout", prompt: "...")         │
│         Wait for scout report                               │
│                                                             │
│  4. DESIGN (if complex)                                     │
│     └─► Task(subagent: "ada-architect", prompt: "...")     │
│         Wait for design document                            │
│                                                             │
│  5. PLAN                                                    │
│     └─► Create implementation steps                         │
│         Break into worker-sized chunks                      │
│                                                             │
│  6. DELEGATE                                                │
│     └─► Task(subagent: "bree-worker", prompt: "...")       │
│         Include files, requirements, patterns               │
│                                                             │
│  7. REVIEW                                                  │
│     └─► Examine worker's changes                            │
│         Check against requirements                          │
│                                                             │
│  8. VERIFY                                                  │
│     └─► Run tests/checks                                    │
│         Validate changes work                               │
│                                                             │
│  9. COMPLETE                                                │
│     └─► mcp__vibe_kanban__update_task(status: "done")      │
│         Report back to orchestrator                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Escalation Paths

### Supervisor → Detective

When a task reveals unexpected bugs:

```
Supervisor detects issue
    │
    ▼
Task(subagent: "vera-detective", prompt: "
  ISSUE: [Description]
  CONTEXT: [What was being worked on]
  SYMPTOMS: [What went wrong]

  Please investigate and provide root cause analysis.
")
    │
    ▼
Wait for investigation report
    │
    ▼
Continue with fix based on findings
```

### Supervisor → Architect

When a task is more complex than expected:

```
Supervisor needs design guidance
    │
    ▼
Task(subagent: "ada-architect", prompt: "
  TASK: [Task description]
  CONTEXT: [Scout report summary]
  COMPLEXITY: [What makes this complex]

  Please provide technical design.
")
    │
    ▼
Wait for design document
    │
    ▼
Follow design in worker delegation
```

## Common Dispatch Templates

### To Emilia (Infrastructure)

```
Task(
  subagent_type: "emilia-infra-supervisor",
  prompt: "
    TASK: [Task title]
    KANBAN_ID: [UUID]

    TYPE: [docker|cicd|deployment|scripts]

    CONTEXT:
    [Background information]

    REQUIREMENTS:
    - [Requirement 1]
    - [Requirement 2]

    FILES LIKELY INVOLVED:
    - Dockerfile
    - docker-compose.yml
    - .circleci/config.yml
    - start.sh

    DELIVERABLES:
    - [Expected outcome]
  "
)
```

### To Luna (RunPod/GPU)

```
Task(
  subagent_type: "luna-runpod-supervisor",
  prompt: "
    TASK: [Task title]
    KANBAN_ID: [UUID]

    TYPE: [workflow|model|gpu|runpod]

    CONTEXT:
    [Background information]

    REQUIREMENTS:
    - [Requirement 1]
    - [Requirement 2]

    FILES LIKELY INVOLVED:
    - workflows/*.json
    - Environment variables
    - Model configurations

    DELIVERABLES:
    - [Expected outcome]
  "
)
```

### To Vera (Detective)

```
Task(
  subagent_type: "vera-detective",
  prompt: "
    ISSUE: [What's wrong]

    SYMPTOMS:
    - [Symptom 1]
    - [Symptom 2]

    REPRODUCTION STEPS:
    1. [Step 1]
    2. [Step 2]

    CONTEXT:
    [What was happening when issue occurred]

    SUSPECTED AREAS:
    - [File or component 1]
    - [File or component 2]
  "
)
```

### To Ada (Architect)

```
Task(
  subagent_type: "ada-architect",
  prompt: "
    DESIGN REQUEST: [What needs to be designed]

    CONTEXT:
    [Background and constraints]

    REQUIREMENTS:
    - [Requirement 1]
    - [Requirement 2]

    CONSTRAINTS:
    - [Constraint 1]
    - [Constraint 2]

    QUESTIONS:
    - [Specific question 1]
    - [Specific question 2]
  "
)
```

### To Ivy (Scout)

```
Task(
  subagent_type: "ivy-scout",
  prompt: "
    RECONNAISSANCE: [What to investigate]

    FOCUS AREAS:
    - [Area 1]
    - [Area 2]

    QUESTIONS TO ANSWER:
    - [Question 1]
    - [Question 2]

    RETURN FORMAT:
    - File locations with line numbers
    - Existing patterns
    - Dependencies
    - Potential issues
  "
)
```

## Agent Model Configuration

| Agent | Model | Use Case |
|-------|-------|----------|
| Ivy (Scout) | `haiku` | Fast reconnaissance |
| Vera (Detective) | `opus` | Deep analysis |
| Ada (Architect) | `opus` | Complex design |
| Emilia (Infra) | `opus` | Critical decisions |
| Luna (RunPod) | `opus` | Critical decisions |
| Bree (Worker) | `sonnet` | Implementation |
| Penny (Scribe) | `haiku` | Documentation |

## Kanban Task States

| State | Meaning |
|-------|---------|
| `todo` | Not started |
| `inprogress` | Being worked on |
| `inreview` | Awaiting review |
| `done` | Completed |
| `cancelled` | Abandoned |
