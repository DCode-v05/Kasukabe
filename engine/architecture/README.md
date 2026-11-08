# September Engine — Architecture Atlas

A complete, deep architecture map of the engine, built from a section-by-section
read of all ~61K LOC across the 13 subsystems plus the integration layer.

## View it

Open **`architecture/index.html`** in any browser. It is a single self-contained
file (sidebar nav, 35 Mermaid diagrams, a diagram index, dozens of tables, cross-linked
chapters). Diagrams render client-side via Mermaid + Marked from a CDN, so an internet
connection is needed the first time.

```sh
open architecture/index.html        # macOS
```

## Source

`index.html` is the rendered, self-contained atlas. The per-chapter source
content lives as Markdown in `architecture/sections/*.md` (numerically ordered,
one file per chapter) — read or edit those directly. All 35 Mermaid diagrams in
the doc were rendered headlessly (35/35 SVGs, zero errors) with the real Mermaid engine.

## Map

- **Foundations** — Overview, System Context, Request Walkthrough, Data Model, Block Wire Contract
- **Integration** — Server / Bootstrap / Auth
- **engine_core** — Agent Loop, Context & Compaction, Hooks, Sub-Agents/Tools/Blocks
- **Capability Plane** — Sandbox, Asset Directory & Channels, Utility/Activity/Infra
- **Intelligence Plane** — Herald (LLM gateway), Memory, Learning/Security/Raven
- **Cross-cutting** — Security & Deployment, Boundaries & Refactoring Map, Glossary

Start with **Overview** for the subsystem map, then **Boundaries & Refactoring Map**
for the prioritized plan for the next round of work.
