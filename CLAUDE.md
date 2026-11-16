# Cutlery – notes for Claude Code

- The build brief is PROMPT.md. Follow it; it asks for a plan and my approval before any code.
- Each milestone in PROMPT.md names a model. At the start of a milestone, say which model it needs; if you are
  running on another one, tell me and wait. Keep progress in docs/progress.md so a fresh session can continue.
- Cutlery is open source (MIT) and meant for other self-hosters too: nothing may depend on my personal setup.
- Everything in the repository is English: identifiers, comments, docstrings, log and error messages, docs,
  commit messages. The only exception is UI text in translation files;
  English and German ship from the start. The product is called "Cutlery" in every language (never translated).
  Repo, image, package and CLI names are always `cutlery`.
- I write to you in German; answer me in German.
- GUI designs: docs/design/*.dc.html, drawn in German (template HTML: {{…}} holes, <sc-for>/<sc-if>, sample data
  in renderVals()). Take structure, wording and style from them, not the template syntax or the sample data.
- Cutlery depends on cutter (https://github.com/aaronblankenburg/cutter); never copy or patch cutter code here.
  Changes that belong in cutter go into a branch there.
- One user per instance. Auth to Claude: either CLAUDE_CODE_OAUTH_TOKEN (operator's personal use of their
  subscription) or ANTHROPIC_API_KEY – exactly one.
- Logo: assets/logo.svg – fork left, knife right, and in place of a plate a film reel (rotated 10°) with a strip of
  film running out of it, curving under the reel and snipped off at the end. favicon.svg is the same without the
  film strip (for 16–32 px); logo-mono.svg is single-colour (currentColor). docs/design/Logo.dc.html is the
  reference; docs/design/rejected-logos/ shows discarded ideas only. Wordmark: "Cut" + "lery" in the
  accent colour.
