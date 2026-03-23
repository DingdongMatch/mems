# Vibe Coding Handbook

> "Vibe Coding" is a programming philosophy proposed by Andrej Karpathy: let AI handle 80% of the code work, while developers focus on 20% core logic and creativity.

---

## 1. Core Principles of Vibe Coding

### 1.1 Not "AI Writes Code, You Copy"

```
❌ Wrong approach:
AI generates complete code → Copy-paste → Don't understand → Can't debug

✅ Correct approach:
AI generates → Understand each line → Modify and adapt → Debug successfully → Master
```

### 1.2 Human-AI Collaboration Mode

```
┌─────────────────────────────────────────────────────────────┐
│                       Your Role                              │
│  - Requirement definition (tell AI what to do)              │
│  - Architecture decisions (choose tech stack)               │
│  - Boundary judgment (decide what works/doesn't)            │
│  - Quality control (review AI output)                       │
│  - Debug and fix (handle edge cases)                        │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ Commands/Feedback
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        AI's Role                             │
│  - Boilerplate code generation (reduce repetitive work)     │
│  - Pattern matching (implement common patterns quickly)     │
│  - Documentation generation (explain code intent)           │
│  - Test suggestions (add edge cases)                        │
│  - Refactoring and optimization (improve code structure)    │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Mems Project Vibe Coding Workflow

### 2.1 Phase 1: Requirements to Prototype

**Goal**: Quickly validate ideas

**Your tasks**:
1. Write 3-5 sentence requirements
2. Choose tech stack combination
3. Confirm core data flow

**AI assistance**:
- Generate project scaffolding
- Create basic file structure
- Implement simplest Hello World

**Mems Example**:
```
You: "I need a four-layer memory system, using SQLite for relational data,
      Qdrant for vectors, support memory ingestion and retrieval.
      Help me set up the basic structure"

AI generates:
├── pyproject.toml
├── docker-compose.yml
├── src/mems/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   └── database.py
└── requirements.txt (basic version)
```

### 2.2 Phase 2: Incremental Implementation

**Goal**: Add features incrementally

**Your tasks**:
1. Implement only one feature at a time
2. Clearly describe input and output
3. Verify previous feature works

**AI assistance**:
- Implement single API route
- Write Service layer logic
- Generate test cases

**Mems Example**:
```
Round 1:
You: "Implement /ingest endpoint, write content to both SQLite and JSONL"
AI: Generates ingest.py + modifies main.py

Round 2:
You: "Implement vector storage, use HTTP to call Qdrant REST API"
AI: Generates vector_service.py (REST API version)

Round 3:
You: "Implement /search endpoint, call embedding service and vector search"
AI: Modifies search.py
```

### 2.3 Phase 3: Debug and Optimize

**Goal**: Fix issues, improve quality

**Your tasks**:
1. Provide specific error messages
2. Describe expected behavior
3. Narrow down problem scope gradually

**AI assistance**:
- Analyze error reasons
- Provide fix suggestions
- Explain why something doesn't work

**Mems Debug Example**:
```
You: "Qdrant query returns 404, but collection exists"
AI: "This is because qdrant-client 1.17.1's query_points API 
     has a known bug in certain versions. Solution: bypass SDK,
     call REST API directly with httpx..."

You: "Vector dimension mismatch"
AI: "BAAI/bge-small-zh-v1.5 outputs 512 dimensions, not 384.
     Need to dynamically get dimension in create_collection..."
```

---

## 3. Effective Prompting Tips

### 3.1 Good Prompt Template

```markdown
## Background
(Briefly describe what project you're working on)

## Requirements
(What you want)

## What I've tried
(What you've already done)

## Problem
(Specific error / unexpected behavior)

## Environment
(Key config: Python version, OS, relevant package versions)
```

### 3.2 Mems Project Examples

**❌ Bad prompt**:
```
Help me implement a memory system
```

**✅ Good prompt**:
```
## Background
I'm developing Mems layered memory system using FastAPI + SQLModel

## Requirements
Implement /search endpoint, need to:
1. Convert query to vector using embedding service
2. Call Qdrant to search similar content
3. Also query L2 semantic knowledge
4. Merge results and sort by score

## What I've tried
Wrote a version but it returns empty results

## Problem
Qdrant returns payload as null, but I know data was written successfully

## Environment
- Python 3.12
- qdrant-client 1.17.1
- Qdrant 1.7.4 (Docker)
```

---

## 4. Code Review Checklist

For AI-generated code, confirm these points:

### 4.1 Functional Correctness
- [ ] Input/output as expected
- [ ] Error handling is reasonable
- [ ] Edge cases considered

### 4.2 Security
- [ ] No hardcoded keys
- [ ] Input validation
- [ ] No SQL injection risk

### 4.3 Performance
- [ ] Async/sync choice correct
- [ ] No obvious performance issues
- [ ] Resources properly released

### 4.4 Maintainability
- [ ] Clear naming
- [ ] Appropriate comments
- [ ] Follows project conventions

---

## 5. Mems Project Debugging Cases

### Case 1: Qdrant REST vs SDK

**Problem**: qdrant-client's `query_points` method always returns 404

**Debug process**:
```
1. Test REST API directly with curl → Success
2. Confirm collection exists → Yes
3. Confirm vector dimension correct → 512 dims
4. Confirm SDK version → 1.17.1 (known bug)
5. Decision: Bypass SDK, use httpx to call REST API directly
```

**Lesson**: Don't blindly trust SDK, verify with REST API first

### Case 2: Vector Dimension Mismatch

**Problem**: Created collection with 384 dims, but bge-small-zh outputs 512 dims

**Debug process**:
```
1. Received "expected dim: 384, got 512" error
2. Checked embedding model docs → BAAI/bge-small-zh is 512 dims
3. Modified code: dynamically get dimension in upsert
```

**Lesson**: Different embedding models have different dimensions, don't hardcode

---

## 6. Recommended Workflow

### 6.1 Daily Flow

```
Morning:
1. Define today's goal (1-2 features)
2. Run project to ensure base is working
3. Write down specific requirements list

Afternoon:
4. Implement one feature → Test
5. Implement second feature → Test
6. Code review → Commit

Evening:
7. Record problems and solutions
8. Plan tomorrow's work
```

### 6.2 Iteration Speed

| Phase | Expected Time | Verification |
|-------|---------------|--------------|
| New feature | 30-60 min | API test |
| Bug fix | 15-30 min | Reproduce problem |
| Refactor | 60+ min | Full test suite |

---

## 7. Common Pitfalls

### 7.1 Over-reliance on AI

```
❌ Symptom: Ask AI for every line of code, can't write simple if/else
✅ Solution: Master basics, use AI for complex patterns and repetitive work

❌ Symptom: Copy AI code without understanding
✅ Solution: At least read through, mark unclear parts and ask AI to explain

❌ Symptom: Fully trust AI, skip code review
✅ Solution: Use section 4 checklist to verify each item
```

### 7.2 Unclear Requirements

```
❌ Symptom: "Implement search feature" → AI guesses wrong
✅ Solution: Be specific about input/output and boundaries

❌ Symptom: Ask 10 things at once → None done well
✅ Solution: Ask 1-2 things at a time, verify before continuing
```

### 7.3 Not Verifying Before Proceeding

```
❌ Symptom: Implement 5 features, then test, found first one broken
✅ Solution: Test immediately after implementing each feature
```

---

## 8. Recommended Tool Setup

### 8.1 VS Code Settings

```json
{
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "charliermarsh.ruff",
  "python.linting.ruffEnabled": true,
  "python.analysis.typeCheckingMode": "basic"
}
```

### 8.2 Useful Shortcuts

| Action | Mac | Windows |
|--------|-----|---------|
| Quick Fix | Cmd + . | Alt + Enter |
| Go to Definition | Cmd + Click | Ctrl + Click |
| Find References | Shift + F12 | Shift + F12 |
| Terminal | Ctrl + ` | Ctrl + ` |

### 8.3 Debugging Tips

```python
# Quick print debug
import debugpy; debugpy.breakpoint()

# Or simpler
print(f"DEBUG: {variable=}")
```

---

## 9.进阶: Become a Better Vibe Coder

### 9.1 Basic Knowledge Requirements

Even with AI writing code, you still need to know:
- [ ] Python basics (syntax, data structures, async)
- [ ] HTTP protocol (REST API)
- [ ] Database concepts (SQL, indexes)
- [ ] Vector/Embedding basic concepts

### 9.2 Experience Accumulation

After completing each feature:
1. Record encountered problems
2. Summarize solutions
3. Extract reusable patterns

### 9.3 Continuous Learning

- Follow AI tool updates
- Learn new frameworks/paradigms
- Understand AI limitations

---

## 10. Summary

Vibe Coding is not letting AI replace you:

```
AI (80% boilerplate) + You (20% core decisions) = Better productivity
```

**Core formula**:
```
Good results = Clear requirements + Timely verification + Active understanding + Continuous improvement
```

---

*Document Version: 0.1.0*
*Updated: 2026-03-20*