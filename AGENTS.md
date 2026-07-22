# AGENTS.md

## Default Behavior

Make the smallest correct change for the current requirement.

Before editing code:

1. Understand the relevant code path from the repository.
2. Search existing implementations and similar usages before using an API.
3. Confirm ambiguous behavior instead of guessing.

Prefer:

* Existing code over new code.
* Standard library or platform capability over custom implementation.
* Direct implementation over abstraction.
* Deletion over addition.
* Small scoped diffs over broad refactors.

Do not prepare for hypothetical future requirements.

## Workspace Documents

Do not create documentation files in the workspace unless the file is:

* `AGENTS.md` itself.
* A project deliverable explicitly requested by the user.

Keep plans, designs, investigation notes, and other process documentation in the conversation instead of writing them into the workspace.

## Requirement Handling

Do not execute unclear requirements as if they were clear.

Ask the user for confirmation when any of the following is uncertain:

* Target behavior
* Business rule
* Scope of change
* Data contract
* API source
* Affected module or page

Do not invent business logic. If the repository does not prove the behavior, state the uncertainty and confirm it.

If grep/glob searches return no useful result for two consecutive rounds, stop and ask the user for the specific file, module, or feature location. Do not keep trying random keywords.

## Code Investigation

Do not guess APIs.

Before using an interface, check:

* Existing project usages
* SDK declarations
* Package source code
* Similar modules or components

For bug fixes:

* Identify the shared root cause before editing.
* Check whether sibling callers use the same path.
* Fix the smallest common cause instead of patching only the visible symptom.

Avoid broad blind edits. Modify only files required by the current task.

## Implementation Style

Write only the code needed for the current requirement.

Follow the surrounding project style, including:

* Module boundaries
* Naming conventions
* State-management patterns
* Formatting style

Keep code simple, direct, and easy to review.

Do not add new interfaces, helpers, configs, abstractions, wrappers, or extension points unless the current requirement genuinely requires them.

Code, variable names, function names, and technical comments should follow the existing project style. Explanations to the developer may be written in Chinese.

## Verification

Do not skip verification.

Run tests to confirm changes:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q agent_watch_notify tests
```

Match verification to risk:

* Small text or config changes: run the closest practical static check.
* Logic changes: run relevant tests.
* Core flow or state changes: run the full test suite.
* High-impact changes: run the full test suite and review edge cases.

Report the actual result.

If verification is blocked, clearly state:

* What was attempted
* Why it was blocked
* What remains unverified

Do not claim the change is complete, fixed, or passing unless verification was actually performed.

## Communication

When reporting back:

* Summarize what changed.
* List verification performed and the actual result.
* State any blocked verification.
* Mention uncertainty instead of hiding it.
* Keep the explanation concise and focused on review-relevant facts.

## Priority

When instructions conflict, follow this order:

1. User's explicit request
2. Repository code conventions
3. Existing architecture and patterns
4. This AGENTS.md
5. General best practices
