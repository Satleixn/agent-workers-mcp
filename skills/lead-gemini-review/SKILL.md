---
name: lead-gemini-review
description: Get a cross-model second opinion from Gemini (workers MCP `ask_gemini`) on a plan or a diff, or Gemini's own independent plan to compare with yours before choosing an approach; verify every finding and report what was adopted or rejected. Use when a non-trivial change needs review before or after implementation — multi-file or design changes (review the plan, then the diff), a non-trivial single-file change (review the diff) — when a design choice has several viable approaches, or when the user asks for a Gemini review or plan. Not for small or mechanical edits.
---

# Gemini review loop (lead only)

You are the lead: you decide, implement and verify. Gemini is a read-only reviewer whose findings are leads, not facts. This skill needs the `workers` MCP tools; worker agents never get it (names starting with `lead-` are kept out of their skill sets).

## When

| Change | Reviews |
|---|---|
| Design or architecture choice with several viable approaches, or the user asks for Gemini's own plan | Independent proposal (replaces the plan review), diff review after |
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

## Independent proposal

Two plans written without seeing each other, then merged: a review of your plan can only critique it, while an independent plan can surface a different approach altogether.

1. **Brief.** The requirement, constraints, material and what is out of scope: what you would give a colleague starting cold. Never your plan, your leanings or the options you have ruled out, and keep your plan file out of `files` and `repo`. Ask for every decision point with the alternatives weighed.
2. **Write your own plan first** (in the plan file), before calling Gemini, and keep your original choices as they were for the comparison, so neither plan anchors the other. The merged plan replaces it in step 5.
3. **Call** `ask_gemini` with `proposal: true` (it implies `structured`), `model: "gemini-4-neon-high"` passed explicitly (the tool's default is neon-medium), `repo` or `files` for the material, and suggested skills as in a review. Call it yourself: the `gemini-reviewer` subagent is written for findings, and you need the plan in your own context to merge it.
4. **Compare decision by decision** in a table: decision, your choice, Gemini's choice, adopted (yours, Gemini's or merged), reason. Before adopting anything of Gemini's, check its `evidence` and `assumptions` against the source. When both plans reached the same choice independently, say so: it is a stronger signal than either alone. Merge both lists of open questions.
5. **Write the merged plan into the plan file and hand it with the table to the user** to decide. A plan that went through this needs no separate plan review; review the diff after implementing as usual.

## Re-review

Only after substantive changes made for blocking or major findings. Each re-review is a new `ask_gemini` call carrying the original brief, the previous findings, your response to each (adopted, or rejected with the reason) and any objection still open, never a bare "check again". Stop when no blocking finding remains; ask the user before a third round.

## Report to the user

- Each finding with its verdict: adopted, rejected (why) or unverified. For an independent proposal, the decision-by-decision table instead.
- The skills Gemini read, rejected and suggested.
- The model and the header line from the tool result; repeat `NOT-ISOLATED`, `denied=N` or `JSON-INVALID` if the header shows them.
- What was not reviewed or not verified.
