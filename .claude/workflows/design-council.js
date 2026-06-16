export const meta = {
  name: 'design-council',
  description: 'Run the AgentOS design council over a piece of work: design propose/critique → mediate → rate to 10/10 (bounded loop) → market triangulation → final brief',
  whenToUse: 'When you have a design or implementation to take to a decision + a 10/10 bar + a market-beating position. Pass args:{description, paths?, target_score?, max_rounds?}.',
  phases: [
    { title: 'Design',     detail: '10 design agents propose + critique the work' },
    { title: 'Mediate',    detail: 'mediator synthesizes one recommended direction' },
    { title: 'Rate',       detail: 'rating panel → aggregator → remediate → re-rate (bounded)' },
    { title: 'Market',     detail: 'landscape → differentiation → positioning triangulation' },
    { title: 'Delight',    detail: 'wrap-up: delight & differentiation pass once the work clears the bar' },
    { title: 'Synthesize', detail: 'final brief: decision + 10/10 plan + positioning + delight + dissent' },
  ],
}

// ---- inputs ----
const work = (typeof args === 'string' && args.trim()) ? args
  : (args && (args.description || args.target || args.work)) || 'the current working tree / latest changes in this repo'
const paths = (args && args.paths) ? `\nRelevant paths: ${[].concat(args.paths).join(', ')}` : ''
const TARGET = (args && args.target_score) || 9.0
const MAX_ROUNDS = (args && args.max_rounds) || 2

const CTX = `WORK UNDER REVIEW: ${work}${paths}

AgentOS = a reactive, personalizing KDE Plasma 6 desktop on a Rust safety substrate (agentosd); the user keeps complete control (diff/revert every change, ADR-0005); local-first (Ollama) over the Hermes orchestrator. Non-negotiables (the rubric backbone): reversible-by-default · model-proposes/code-disposes · don't-reinvent Hermes/Ollama · local-first/consent · fail-open-supervised · calm & honest ambient mapping · accessible · performant/yield-aware · every behavior change an ADR. ADRs in docs/adr/.`

const digest = (arr, key) => arr.map(o => `### ${o[key]}\n${o.text}`).join('\n\n')

// ---- Phase 1: Design ----
phase('Design')
const DESIGN = ['art-director','motion-designer','visual-systems-designer','interaction-designer','design-technologist','generative-artist','sound-designer','brand-identity-designer','content-voice-designer','design-researcher']
const designOut = (await parallel(DESIGN.map(a => () =>
  agent(`${CTX}\n\nAs the ${a}, give your best concrete proposal(s) AND a critique for this work, in your file's structured format. Cite real precedent where you can.`,
    { agentType: a, label: `design:${a}`, phase: 'Design' }).then(t => t && ({ agent: a, text: t }))
))).filter(Boolean)

// ---- Phase 2: Mediate ----
phase('Mediate')
let current = await agent(
  `${CTX}\n\nThe design team weighed in below. Manage the discourse: map agreements and tensions, adjudicate by ownership and the non-negotiables, and produce ONE recommended design direction with accepted tradeoffs and recorded dissent (your 'Discourse synthesis' format).\n\n${digest(designOut,'agent')}`,
  { agentType: 'design-discourse-mediator', label: 'mediate:design', phase: 'Mediate' })

// ---- Phase 3: Rate → remediate → re-rate (bounded) ----
phase('Rate')
const RATERS = ['rater-craft','rater-vision-fit','rater-feasibility','rater-experience','rater-market-fit']
let round = 0, lastAgg = null
while (round < MAX_ROUNDS) {
  round++
  const rated = (await parallel(RATERS.map(r => () =>
    agent(`${CTX}\n\nRate this recommended direction (round ${round}) per your rubric, with an explicit delta-to-10.\n\nDIRECTION:\n${current}`,
      { agentType: r, label: `rate:${r}#${round}`, phase: 'Rate' }).then(t => t && ({ rater: r, text: t }))
  ))).filter(Boolean)
  lastAgg = await agent(
    `${CTX}\n\nAggregate the rating panel (round ${round}): weighted overall (show math + caps), dispersion analysis, a prioritized & owned 10/10 gap plan, and a SHIP/ITERATE/RECONSIDER verdict (target ${TARGET}).\n\nPANEL:\n${digest(rated,'rater')}\n\nDIRECTION RATED:\n${current}`,
    { agentType: 'rating-aggregator', label: `aggregate#${round}`, phase: 'Rate' })
  if (/\bSHIP\b/.test(lastAgg)) { log(`Round ${round}: SHIP`); break }
  if (round >= MAX_ROUNDS) { log(`Round ${round}: hit max rounds without SHIP`); break }
  current = await agent(
    `${CTX}\n\nThe rating aggregator returned this verdict + 10/10 gap plan. Revise the recommended direction to close the top gaps while preserving what scored well; output the improved direction in your synthesis format.\n\nAGGREGATOR:\n${lastAgg}\n\nCURRENT DIRECTION:\n${current}`,
    { agentType: 'design-discourse-mediator', label: `remediate#${round}`, phase: 'Rate' })
}

// ---- Phase 4: Market (sequential chain) ----
phase('Market')
const landscape = await agent(`${CTX}\n\nMap the market landscape and adjacencies relevant to AgentOS, sourced (links on every claim).`,
  { agentType: 'market-landscape-analyst', label: 'market:landscape', phase: 'Market' })
const diff = await agent(`${CTX}\n\nLandscape research:\n${landscape}\n\nDefine how AgentOS becomes SIGNIFICANTLY better — edges (real/defensible/copyable), gaps to attack, the wedge.`,
  { agentType: 'market-differentiation-strategist', label: 'market:differentiation', phase: 'Market' })
const positioning = await agent(`${CTX}\n\nLandscape:\n${landscape}\n\nDifferentiation:\n${diff}\n\nTriangulate into ONE positioning brief + crisp market-fit feedback for the rating panel.`,
  { agentType: 'market-positioning-synthesizer', label: 'market:positioning', phase: 'Market' })

// ---- Phase 5: Delight & differentiation (wrap-up, runs once the work is at the bar) ----
phase('Delight')
const shipped = lastAgg ? /\bSHIP\b/.test(lastAgg) : false
const delight = await agent(
  `${CTX}\n\nThe work is ${shipped ? 'at the 10/10 bar' : 'at/near the bar (assume its 10/10 gap plan is addressed)'}, with a market position set. Run the wrap-up Delight & Differentiation pass: elevate it from correct-and-excellent to delightful and unmistakably AgentOS — signature moments, earned microdelight, and turning our differentiation into *felt* experience — every proposal passing the restraint check (calm · accessible · reversible · honest · within frame/VRAM budget). Use your 'Delight & Differentiation pass' format.\n\nDECIDED DIRECTION:\n${current}\n\nLATEST RATING:\n${lastAgg}\n\nMARKET POSITIONING:\n${positioning}`,
  { agentType: 'delight-differentiation-designer', label: 'wrap:delight', phase: 'Delight' })

// ---- Phase 6: Final synthesis ----
phase('Synthesize')
const brief = await agent(
  `${CTX}\n\nProduce the FINAL design-council brief, combining (a) the decided design direction, (b) the latest rating verdict + 10/10 gap plan, (c) the market positioning, (d) the delight & differentiation wrap-up. Record dissent, give the prioritized next actions to reach 10/10, a significantly-better-than-market position, AND the signature delight moves to land. Draft an ADR stub if a behavior change is implied. Write it to docs/design/ if possible.\n\nDECIDED DIRECTION:\n${current}\n\nLATEST RATING:\n${lastAgg}\n\nMARKET POSITIONING:\n${positioning}\n\nDELIGHT & DIFFERENTIATION:\n${delight}`,
  { agentType: 'design-discourse-mediator', label: 'final:brief', phase: 'Synthesize' })

return {
  rounds: round,
  shipped,
  design_participants: designOut.map(o => o.agent),
  final_rating_excerpt: lastAgg ? lastAgg.slice(0, 500) : null,
  delight_excerpt: delight ? delight.slice(0, 500) : null,
  brief_excerpt: brief ? brief.slice(0, 700) : null,
}
