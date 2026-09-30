# AGENTS.md

## 1. General Rules

You are working on a team-based hackathon project.

The primary goal is to build a working, maintainable, demonstrable MVP — not an over-engineered enterprise system.

Before modifying code:

1. Inspect the existing project structure.
2. Read the relevant files.
3. Check existing dependencies, configuration, tests, and documentation.
4. Understand the existing implementation before changing it.
5. Do not invent requirements or behavior.
6. Do not rewrite existing code without a concrete reason.

If a requirement is ambiguous, inspect the project and available documentation first. If it is still ambiguous and the decision could affect architecture or public behavior, ask the user.

---

## 2. MCPs and External Tools

Before starting a complex task, check whether the required MCPs/tools are available.

### Context7

Use Context7 when working with libraries, frameworks, SDKs, or APIs.

Especially use it for:

- FastAPI
- SQLAlchemy
- Alembic
- Pydantic
- Redis
- pytest
- Docker-related APIs
- external SDKs
- any library whose API may have changed

Prefer current official documentation over memory.

Do not assume an API works a certain way when current documentation can be checked.

### GitHub

When GitHub MCP is available, use it for repository-related operations such as:

- issues;
- pull requests;
- branches;
- repository metadata;
- code review;
- GitHub Actions.

Do not duplicate information manually when it is available through GitHub.

### Missing MCPs

If a required MCP is missing, check whether it can be made available through the existing tooling.

Do not block the entire task solely because an MCP is unavailable if the task can be safely completed without it.

---

## 3. Development Philosophy

### KISS

Keep It Simple.

Do not introduce abstractions unless they solve a real problem.

### YAGNI

You Aren't Gonna Need It.

Do not implement functionality only because it might be useful later.

### DRY

Avoid unnecessary duplication of business logic.

However, do not create abstractions solely to eliminate a few repeated lines.

### Explicit over Clever

Prefer straightforward, readable code over clever code, magic, or unnecessary design patterns.

Another developer should be able to understand the code without asking the author for an explanation.

### MVP First

Priorities:

1. Working functionality.
2. Correctness.
3. Testability.
4. Maintainability.
5. Performance.
6. Additional optimization.

Do not sacrifice a working MVP for architectural perfection.

---

## 4. Architecture

Follow the existing project architecture.

Do not introduce new architectural layers unless they are actually necessary.

Prefer the following separation:

```text
api/
    HTTP/API layer

schemas/
    request/response structures

services/
    business logic

repositories/
    persistence/data access

models/
    database models

core/
    configuration, security, shared infrastructure

db/
    database infrastructure and connections
```

Business logic should not live inside:

- HTTP handlers;
- Pydantic schemas;
- database models.

Dependencies should be explicit.

Avoid unnecessary global state.

---

## 5. API Contract Stability

**Existing API contracts are considered stable and must not be changed without explicit authorization.**

Do not modify, rename, remove, or change the behavior of existing:

- endpoints;
- HTTP methods;
- URL paths;
- request schemas;
- response schemas;
- field names;
- field types;
- validation rules;
- status codes;
- error formats;
- authentication behavior.

Do not make API-breaking changes as part of:

- refactoring;
- cleanup;
- bug fixing;
- formatting;
- architectural changes;
- performance improvements.

### Adding API endpoints

Do not create new endpoints unless the task explicitly requests API functionality.

Examples of explicit requests:

```text
"Add an endpoint for appointments."

"Create POST /api/v1/appointments."

"Expose this service through an API endpoint."
```

If the user asks to implement internal functionality without mentioning an API, do not automatically expose it through HTTP.

### API changes

If a requested change requires modifying an existing API contract:

1. Clearly identify the breaking change.
2. Explain which contract is affected.
3. Do not silently apply the change.
4. Ask for confirmation unless the user explicitly requested that exact API change.

When possible, prefer backwards-compatible additions over breaking changes.

---

## 6. Dependencies

Before adding a dependency:

1. Check whether the project already has a dependency that solves the problem.
2. Verify that the new dependency is actually necessary.
3. Check current documentation through Context7 or official documentation.
4. Avoid adding large libraries for trivial functionality.

After adding a dependency, update the project's dependency/lock configuration as appropriate.

Do not add dependencies speculatively.

---

## 7. Python Code

Use Python 3.13+.

All production code should use type hints.

Code must be compatible with strict mypy.

Prefer:

- explicit types;
- small functions;
- clear naming;
- dependency injection where appropriate;
- async I/O for the async stack.

Avoid unnecessary `Any`.

Do not use `# type: ignore` unless there is a documented and unavoidable reason.

Do not hide errors with constructs such as:

```python
try:
    ...
except Exception:
    pass
```

Do not leave:

- debugging `print()` calls;
- dead code;
- commented-out old implementations;
- unexplained TODOs;
- temporary hacks.

---

## 8. Testing

Every meaningful piece of functionality should have tests.

Cover, where applicable:

- happy paths;
- expected failures;
- validation;
- important edge cases.

Before considering a task complete, run:

```bash
ruff check .
ruff format --check .
mypy .
pytest
```

If the project provides a Makefile or other standard commands, follow those commands as well.

Never consider a task complete while required checks are failing.

Do not disable lint/type-check rules merely to make CI pass.

---

## 9. Git

Keep changes small and logically grouped.

Do not mix unrelated changes such as:

- new functionality;
- mass formatting;
- large-scale renaming;
- unrelated refactoring;
- dependency upgrades.

Before committing, inspect:

```bash
git status
git diff
```

Never commit:

- `.env` files containing secrets;
- API keys;
- passwords;
- access tokens;
- private keys;
- local databases;
- build artifacts;
- unnecessary IDE files.

Use conventional commit-style messages:

```text
feat: add appointment service
fix: handle unavailable slots
test: add appointment service tests
refactor: simplify repository interface
docs: update setup instructions
chore: update dependencies
```

---

## 10. Before Push / Pull Request

Before pushing changes or creating a pull request, always run:

```bash
ruff check .
ruff format --check .
mypy .
pytest
```

Then inspect:

```bash
git status
git diff
```

Make sure there are no:

- secrets;
- debug files;
- accidental changes;
- unrelated modifications;
- broken tests.

Never bypass CI checks just to make CI green.

---

## 11. GitHub Workflow

When working on a task:

1. Understand the task.
2. Inspect the existing implementation.
3. Make the smallest reasonable change.
4. Run tests and static checks.
5. Review the diff.
6. Commit only the intended changes.
7. Push/create a PR when requested or appropriate.

Do not close or modify other people's issues/PRs without a clear reason.

Do not rewrite another contributor's work without understanding why it exists.

---

## 12. AI / LLM Components

LLMs are components of the system, not sources of truth.

Do not place critical business logic exclusively inside prompts.

When model output affects system behavior:

1. Use structured output where possible.
2. Validate model output.
3. Restrict allowed values.
4. Handle malformed or unexpected responses.
5. Never give an LLM unrestricted control over critical system actions.

External actions should go through explicit services/tools/APIs.

Use deterministic application logic for validation and important business rules.

---

## 13. Security and Privacy

Never commit:

- API keys;
- passwords;
- access tokens;
- private keys;
- real personal data;
- real medical/patient data.

Use environment variables for secrets.

Use mock/demo data during development.

Do not log sensitive information.

For the medical domain, assume that patient information is sensitive by default.

---

## 14. Documentation

Update documentation when changes affect:

- architecture;
- API;
- configuration;
- setup;
- deployment;
- infrastructure;
- developer workflow.

The README should contain enough information for a new team member to run the project without requiring a verbal explanation.

---

## 15. Scope Control

Do not do more than the task requires.

If the task is:

> "Add an endpoint."

Do not automatically:

- redesign the repository layer;
- rewrite the service layer;
- reformat the entire project;
- upgrade unrelated dependencies;
- change existing API contracts.

If you discover an unrelated problem:

1. Do not automatically fix it if the change is substantial.
2. Mention it to the user.
3. Fix it automatically only if the change is trivial, safe, and directly relevant.

Avoid scope creep.

---

## 16. Definition of Done

A task is complete when:

- the requested functionality is implemented;
- the existing architecture is respected;
- necessary tests are added;
- Ruff passes;
- formatting checks pass;
- mypy passes;
- pytest passes;
- no secrets or debug code are present;
- documentation is updated when necessary;
- the Git diff has been reviewed;
- no unintended API contract changes were introduced.

After completing a task, briefly report:

- what was changed;
- which files were affected;
- which checks were run;
- the result of those checks;
- anything that remains to be done.
