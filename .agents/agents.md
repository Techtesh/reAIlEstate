# Codex Guidelines (Karpathy Rules)

## 1. Think Before Coding
- State your assumptions explicitly before implementing. If uncertain, ask.
- If multiple interpretations exist, present them—don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First
- Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

## 3. Surgical Changes
- Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it—don't delete it.
- Remove imports/variables/functions that YOUR changes made unused.

## 4. Goal-Driven Execution
- Define success criteria and loop until verified.
- For multi-step tasks, state a brief plan with verification steps:
  1. [Step] → verify: [check]
  2. [Step] → verify: [check]