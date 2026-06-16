# AgentOS agent ensemble

Specialist subagents for AgentOS — the agentic desktop substrate that reacts and
personalizes to the user over time, while keeping the user in complete control (every
UI change diffable and revertible, ADR-0005).

Four groups + a mediator, plus a runnable **design-council** workflow that pipelines them.

## How to use

- Call any agent by name: *"Run the `responsible-ai-privacy-skeptic` and `reversibility-tx-reviewer` on this diff."*
- Run a group: *"Get the design team to propose directions for the diff/revert panel."*
- Reconcile voices: *"Have `design-discourse-mediator` synthesize the design team's debate."*
- Score it: *"Run the rating panel and `rating-aggregator` on this and tell me the path to 10/10."*
- Or run the whole pipeline: `Workflow({name:'design-council', args:{description:'…', paths:['…']}})`.

Shared conventions: reviewers are **advisory, read-only** (no Edit/Write) and use one
severity scale (Blocker · High · Medium · Low · Nit); the design team is **generative**
(propose **and** critique); raters score **1–10** with an explicit **delta-to-10**. The
project **non-negotiables** (reversible-by-default · model-proposes/code-disposes ·
don't-reinvent Hermes/Ollama · local-first/consent · fail-open-supervised · calm & honest
ambient mapping · accessible · performant/yield-aware · every behavior change an ADR) are
the shared rubric backbone.

## 1. Reviewers (advisory, read-only · enriched)

| Agent | Lens |
|---|---|
| `ux-reviewer` | Flows, agency, undo/diff discoverability |
| `ui-accessibility-reviewer` | UI craft + accessibility (WCAG, motion, contrast, keyboard) + mobile |
| `ambient-embodiment-reviewer` | "Agent as environment" — wallpaper-mood, tray-fact, swaync |
| `ai-product-reviewer` | Vision alignment, scope/MVP, build-vs-reuse |
| `ai-generation-reviewer` | Where/how to invoke models at indeterminate junctures |
| `personalization-loop-reviewer` | The learn-you-over-time loop: drift, cold-start, correction |
| `responsible-ai-privacy-skeptic` | Distrusts the personalization story — capture, egress, consent |
| `security-reviewer` | Local attack surface: proxy, D-Bus, secrets, supply chain |
| `channels-integration-reviewer` | Third-party channels (Telegram, Matrix, webhooks) via Hermes |
| `rust-performance-reviewer` | Idiomatic + fast Rust, GPU effects, UI generation |
| `reversibility-tx-reviewer` | The headline feature — ADR-0005 apply/rollback tx |
| `resource-safety-reviewer` | The substrate core — ADR-0003/0004 VRAM coordinator, no OOM |
| `wayland-computeruse-reviewer` | Plasma 6 / Wayland limits + computer-use sandboxing |
| `determinism-safety-reviewer` | "Model proposes, code disposes" enforcement |

The don't-reinvent / ADR discipline is a shared non-negotiable, not a standalone reviewer.

## 2. Design team (generative — propose **and** critique)

| Agent | Craft |
|---|---|
| `art-director` | Visual direction; scouts best-in-class web references and justifies them technically |
| `motion-designer` | Motion language incl. GPU-driven effects, against the frame/VRAM budget |
| `visual-systems-designer` | Tokens, color, type, theming that stays coherent under personalization |
| `interaction-designer` | The diff/revert/control interaction model and how adaptation is surfaced |
| `design-technologist` | Creative coder — prototypes shaders/QML in `spikes/`, proves feasibility |
| `generative-artist` | Procedural, `agent.json`-driven ambient visuals (the Hills/Flow lineage) |
| `sound-designer` | Ambient audio / multisensory; the sensory side of swaync |
| `brand-identity-designer` | The personality/identity of the agent-as-environment |
| `content-voice-designer` | Microcopy & voice for changes, consent, and explanations |
| `design-researcher` | Evidence, precedent, reference synthesis feeding the team |

## 3. Mediator

| Agent | Role |
|---|---|
| `design-discourse-mediator` | Neutral facilitator across the **whole ensemble**; maps agreement/tension, adjudicates by ownership + the non-negotiables, drives to a decision, records dissent, drafts the brief/ADR |

## 4. Rating panel (1–10 with delta-to-10)

| Agent | Dimension |
|---|---|
| `rater-craft` | Execution craft & polish |
| `rater-vision-fit` | Vision alignment + the non-negotiables (a violation caps the score) |
| `rater-feasibility` | Technical feasibility/risk vs Plasma/Wayland + the codebase reality |
| `rater-experience` | UX + accessibility + desirability |
| `rater-market-fit` | Differentiation/defensibility vs the market |
| `rating-aggregator` | Weighted merge + dispersion + the consolidated 10/10 gap plan + SHIP/ITERATE/RECONSIDER |

## 5. Market team (sourced research → positioning)

| Agent | Role |
|---|---|
| `market-landscape-analyst` | Maps the field/adjacencies with sourced evidence |
| `market-differentiation-strategist` | How AgentOS becomes *significantly* better — edges, moats, the wedge |
| `market-positioning-synthesizer` | Triangulates into one position + messaging; feeds market-fit back |

## 6. Wrap-up

| Agent | Role |
|---|---|
| `delight-differentiation-designer` | Runs *after* the work clears the 10/10 bar — takes correct-and-excellent to delightful and unmistakably AgentOS: signature moments, earned microdelight, and turning differentiators into *felt* experience, all within the non-negotiables |

## The `design-council` workflow

`Workflow({name:'design-council', args:{description, paths, target_score, max_rounds}})`
pipelines the ensemble over a piece of work:

1. **Design** — the 10 design agents propose/critique it.
2. **Mediate** — `design-discourse-mediator` synthesizes one direction.
3. **Rate** — the panel scores → `rating-aggregator` → if below target, the mediator
   remediates the top gaps and the panel re-rates (bounded loop, default 2 rounds).
4. **Market** — landscape → differentiation → positioning triangulation.
5. **Delight** — wrap-up: `delight-differentiation-designer` elevates the cleared work with signature/differentiating moments.
6. **Synthesize** — a final brief (decision + 10/10 plan + positioning + delight + dissent), ADR stub if warranted.
