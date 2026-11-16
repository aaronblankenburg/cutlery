# Progress

Milestone log for the build described in PROMPT.md. Claude Code updates this file at the end of every
milestone (and before any model switch), so a fresh session can continue without prior context.

| Milestone | Status | Notes |
|---|---|---|
| M0 – plan, cutter / Cutlery split, architecture | done (2026-11-16) | Approved plan in docs/plan.md. Decisions: runner service holds the Docker socket; egress allowlist incl. FMA, ccMixter, Wikimedia Commons, Internet Archive, Jamendo. |
| M1 – browser transcoding, sidecar, upload | next (Opus 5.5) | |
| M2 – analysis on 360p with sidecar metadata | not started | |
| M3 – export with correct paths, round-trip test | not started | |
| M4 – Claude Code jobs, non-interactive skill mode | not started | |
| M5 – review buttons, GUI, English and German UI | not started | |
| M6 – FCPXML, Resolve and FilmCraft export | not started | |
| M7 – deployment, hardening, docs for self-hosters | not started | |

## Open questions

- FilmCraft relink and FCPXML stabilisation support (M6).
- Face recognition at 360p (M2 measurement).
- Exact Jamendo/Internet Archive download hosts and Natural Earth host for the egress allowlist (M4).
