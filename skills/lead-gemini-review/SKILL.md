---
name: lead-gemini-review
description: Get a cross-model second opinion from Gemini (workers MCP `ask_gemini`) on a plan or a diff, verify every finding, and report what was adopted or rejected. Use when a non-trivial change needs review before or after implementation — multi-file or design changes (review the plan, then the diff), a non-trivial single-file change (review the diff) — or when the user asks for a Gemini review. Not for small or mechanical edits.
---

# Gemini review loop (lead only)

You are the lead: you decide, implement and verify. Gemini is a read-only reviewer whose findings are leads, not facts. This skill needs the `workers` MCP tools; worker agents never get it (names starting with `lead-` are kept out of their skill sets).

## When

| Change | Reviews |
|---|---|
| Multi-file, design or architecture change | Plan review before implementing, diff review after |
| Non-trivial single-file change | Diff review after implementing |
| Small, mechanical or easily reverted edit | None; do not call |
| User asks for a Gemini review | What they asked for |

## One review round

1. **Brief.** State the goal, constraints, what to look for, and what is out of scope. Ask for concrete findings with locations and evidence, and say not to propose rewrites of unrelated code.
2. **Material.** A plan review gets the plan file plus the code it changes. A diff review gets the diff written to a file plus the changed files, or `repo` when the change is spread out. Never send secrets; ask the user before sending a private repository their instructions have not cleared.
3. **Model.** Use the tier table in the `ask_gemini` description: `gemini-4-neon-high` for multi-file or design reviews, `gemini-4-neon-medium` for one module. If quota refuses, take the model the error suggests, or do the review yourself and say so.
4. **Skills.** Suggest the zero to three shared skills that fit the domain in `skills` (for example `review-animations` for motion code). Gemini sees every shared skill and decides for itself; its choices are part of the cross-check.
5. **Call** `ask_gemini` with `structured: true`, everything in one call. Delegating the call to the `gemini-reviewer` subagent is fine when you want to keep your own context small.
6. **Verify every finding** against the source: read the cited lines, and run code or tests where it is cheap. Classify each as adopted, rejected (with the reason) or unverified. Treat `skills_used`, `rejected_suggestions` and `suggested_skills` the same way: weigh them, and tell the user about any skill Gemini suggests that the library lacks.
7. **Fix** adopted findings. Fix small ones yourself; they never justify another review.

## Re-review

Only after substantive changes made for blocking or major findings. Each re-review is a new `ask_gemini` call carrying the original brief, the previous findings, your response to each (adopted, or rejected with the reason) and any objection still open, never a bare "check again". Stop when no blocking finding remains; ask the user before a third round.

## Report to the user

- Each finding with its verdict: adopted, rejected (why) or unverified.
- The skills Gemini read, rejected and suggested.
- The model and the header line from the tool result; repeat `NOT-ISOLATED`, `denied=N` or `JSON-INVALID` if the header shows them.
- What was not reviewed or not verified.
