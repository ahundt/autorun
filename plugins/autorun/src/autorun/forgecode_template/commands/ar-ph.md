---
name: ar-ph
description: Refresh the universal system design philosophy before designing or reviewing code
---

Before any non-trivial design or code review, internalise these principles:

1. **Automatic and correct.** Prefer code that does the right thing
   without manual intervention over code that requires the user to
   remember rules.
2. **Concrete communication.** Use precise file paths, line numbers,
   error messages, command names — never vague abstractions like
   "the issue".
3. **One problem, one solution.** Resist parallel solutions for the
   same problem. Consolidate.
4. **Lean implementation.** No speculative abstractions. No
   half-finished features. No fallbacks for impossible cases.
5. **Trust internal code.** Validate only at system boundaries.
6. **Verify, don't assume.** Always ground claims in observable
   evidence (file contents, command output, test results).

Apply these before planning, during implementation, and during review.
