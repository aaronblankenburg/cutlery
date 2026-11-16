# SchnippSchnapp – notes for Claude Code

- The build brief is PROMPT.md. Follow it; it asks for a plan and my OK before any code.
- GUI designs: docs/design/*.dc.html (template HTML: {{…}} holes, <sc-for>/<sc-if>, sample data in renderVals()).
  Take structure and style from them, not the template syntax or the sample data.
- SchnippSchnapp depends on cutter (https://github.com/aaronblankenburg/cutter); never copy or patch cutter code here.
  Changes that belong in cutter go into a branch there.
- Code, comments and docs in English; UI text in German.
- Single user, private home server. Claude Code runs on my subscription (CLAUDE_CODE_OAUTH_TOKEN), never with
  ANTHROPIC_API_KEY.
- Logo: assets/logo.svg (variant C "film strip", chosen; docs/design/LogoVarianten.dc.html shows the rejected
  alternatives A–H for reference only). Wordmark: "Schnipp" in text colour, "Schnapp" in the accent colour.
