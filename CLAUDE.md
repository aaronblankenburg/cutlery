# Cutlery – notes for Claude Code

- The build brief is PROMPT.md. Follow it; it asks for a plan and my approval before any code.
- Each milestone in PROMPT.md names a model. At the start of a milestone, say which model it needs; if you are
  running on another one, tell me and wait. Keep progress in docs/progress.md so a fresh session can continue.
- Cutlery is open source (MIT) and meant for other self-hosters too: nothing may depend on my personal setup.
- Everything in the repository is English (code, comments, docs, commits). UI text lives in translation files;
  English and German ship from the start. The product is called "Cutlery" in English and "SchnippSchnapp" in
  German. Repo, image, package and CLI names are always `cutlery`.
- I write to you in German; answer me in German.
- GUI designs: docs/design/*.dc.html, drawn in German (template HTML: {{…}} holes, <sc-for>/<sc-if>, sample data
  in renderVals()). Take structure, wording and style from them, not the template syntax or the sample data.
- Cutlery depends on cutter (https://github.com/aaronblankenburg/cutter); never copy or patch cutter code here.
  Changes that belong in cutter go into a branch there.
- One user per instance. Auth to Claude: either CLAUDE_CODE_OAUTH_TOKEN (operator's personal use of their
  subscription) or ANTHROPIC_API_KEY – exactly one.
- Logo: assets/logo.svg (variant C "film strip", chosen; docs/design/LogoVarianten.dc.html shows the rejected
  alternatives A–H for reference only). Wordmarks: "Cut" + "lery" in the accent colour; German
  "Schnipp" + "Schnapp" in the accent colour.
