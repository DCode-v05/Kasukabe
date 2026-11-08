# Research notes — what the literature says about Kasukabe's design

**What this is:** notes from reading the papers this project is standing on, and what each one implies for Kasukabe specifically. Every number below is quoted from the paper, not paraphrased from memory.

---

## 1. Park et al. 2024 — *Generative Agent Simulations of 1,000 People*

Joon Sung Park, Carolyn Q. Zou, Aaron Shaw, Benjamin Mako Hill, Carrie Cai, Meredith Ringel Morris, Robb Willer, Percy Liang, Michael S. Bernstein. Stanford / Northwestern / UW / Google DeepMind.

### What they did

Recruited a stratified sample of **1,052 US adults**, ran each through a **two-hour voice-to-voice interview** with an AI interviewer using the American Voices Project protocol — average transcript **6,491 words** (std 2,541). The transcript then *becomes* the agent's memory: it is injected whole into the model prompt, which is instructed to imitate that person. Model throughout is **GPT-4o**.

They then had both the real people and their agents complete the same battery: **177 GSS core items**, the **44-item Big Five (BFI-44)**, **five economic games** (dictator, trust ×2, public goods, prisoner's dilemma), and **five replication experiments**.

The measurement idea worth stealing: people are not self-consistent, so a raw accuracy number is meaningless on its own. They had every participant **retake the whole battery two weeks later**, and report **normalized accuracy** = agent accuracy ÷ that person's own retest accuracy. **1.0 means the agent predicts you as well as you predict yourself.**

### The headline result — and the problem for us

| Agent built from | GSS normalized accuracy | Big Five normalized correlation |
|---|---|---|
| **Two-hour interview** | **0.85** (std 0.11) | **0.80** |
| Demographic attributes | 0.71 (std 0.11) | 0.55 |
| **Persona paragraph** | **0.70** (std 0.11) | 0.75 |
| Random baseline | 0.27 raw | — |

Raw numbers behind the top row: agents 68.85% vs participants' own two-week replication 81.25%.

**This is the finding that matters for Kasukabe.** DESIGN.md §75 builds every resident from a written persona description — internal tension, formative history, values, fears, ambitions, and so on. That is exactly the paper's **persona-based** condition, and it is the *worst-performing* of the three at 0.70. A generated persona paragraph, however rich, carried **15 normalized points less** than an interview.

The obvious objection is that Kasukabe's agents don't represent real people, so there is no ground truth to be accurate against — which is true, and means the accuracy number doesn't transfer directly. But the mechanism does: the paper's ablations show *why* interviews win, and those reasons apply to any persona-conditioned agent.

### The ablations — why interviews win

- Cut **80% of the interview at random** (96 of 120 minutes) and interview-agents still scored **0.79**, beating the composite baseline.
- Convert the transcript to **bullet-point summaries**, stripping all linguistic style, and they scored **0.83**.
- A **composite agent** given the participant's own GSS + Big Five + economic-game answers — semantically much closer to the evaluation than an interview is — reached only **0.76 / 0.64 / 0.31**.

So the advantage is **specific idiosyncratic content**, not prose style and not volume. A persona that states concrete, particular facts about a person's life will beat an eloquent one that stays general. That is directly actionable for `soul_seed.py`.

### Expert reflection — the architectural contribution

Prompting with the raw transcript in a single chain of thought makes the model **miss latent information never stated outright**. So they add a reflection pass that adopts four social-science expert personas — **psychologist, behavioral economist, political scientist, demographer** — and asks each for **5–20 observations** about the person. Generated once, stored in memory. At question time an LLM classifier picks **which single expert** best fits the question, and only that expert's reflections are retrieved and appended to the transcript.

**Implemented in Kasukabe** as of this note — see §"What we changed" below.

### Bias — the result that bears on §24 / B8

Demographic Parity Difference (best-performing minus worst-performing subgroup):

| Subgroup axis | Task | Demographic-based | Interview-based |
|---|---|---|---|
| Political ideology | GSS | 12.35% | **7.85%** |
| Political ideology | Big Five | 0.165 | **0.063** |
| Political ideology | Econ games | 0.50 | **0.19** |
| Race | GSS | 3.33% | **2.08%** |
| Race | Big Five | 0.17 | **0.11** |
| Gender | all | already low | ~unchanged |

Building an agent from a demographic label makes it a **stereotype of that label**, and the error lands unevenly across groups. Interviews cut that roughly in half on the ideology axis.

This is a direct warning for **B8 (`app/research/demographics.py`)**. Kasukabe's genesis assigns occupation, age, sex, and household and generates personas from them — a demographic-conditioned pipeline. If we then measure "do miners vote differently from traders", part of any difference we find will be **stereotype baked in at genesis, not behaviour that emerged**. B8 needs a control for that, not just a cross-tab.

### Replication results

Human participants replicated **4 of 5** studies; the agents replicated **the same 4 and failed the same 1**. Agent effect sizes correlated with human effect sizes at **r = 0.98** (participants' own internal consistency: 0.99 → normalized 0.99). Note the agents consistently **overestimated effect sizes** (e.g. Schilke et al.: humans 0.33, interview-agents 2.97, demographic-agents 5.52) — the direction is right, the magnitude is inflated, and inflated most in the demographic condition.

### Agent bank access model

Two-pronged, worth copying if Kasukabe ever publishes agents: **open access to aggregated responses on fixed tasks**, **restricted access to individual responses on open tasks** behind a review process. Safeguards: usage audit log, participant withdrawal, non-commercial use agreements. Explicitly modelled on genome banks. Code: `github.com/joonspk-research/generative_agent`.

---

## 2. What this changes for Kasukabe

### Done (this pass)

**Expert reflection is implemented** in `app/game/static/js/township/build_characters.py` and visible in the township viewer's **Experts** tab. All four expert personas, **4,090 reflections across 250 residents** (~16.4 each), plus the question-time routing step — type a question and watch which expert gets retrieved.

Two honest limits, stated in the generated JSON itself and on the page:
- The paper generates these with **GPT-4o over a two-hour interview**. Kasukabe has no interviews and this build calls no model, so reflections are **rule-derived** from the persona fields genesis.json already carries. This demonstrates the architecture and gives retrieval something real to route over. It does not reproduce the accuracy result.
- The routing classifier is **keyword matching**, not the paper's LLM classifier.

### Recommended, not yet done

1. **Reconsider the persona-only foundation.** Kasukabe sits in the 0.70 condition. Two ways out, in order of cost:
   - *Cheap:* push `soul_seed.py` toward **specific incidents** over general disposition. The ablations say concrete idiosyncratic content is what carries the signal — "she has not spoken to her brother since the roof money" beats "she is proud and holds grudges."
   - *Expensive but faithful:* generate each resident by having a model **interview** a seed persona for N turns, and seed the agent with that transcript rather than the paragraph. This is the paper's actual method, and it is reproducible without any human subjects.

2. **Adopt normalized accuracy as the measurement discipline.** ROADMAP.md §537 wants to measure drift between an evolved soul and its genesis persona. Right now there's no denominator. The paper's move — measure the *subject's own* self-consistency and divide by it — applies: run a resident's own battery twice at the same tick with different sampling seeds, and normalize drift against that. Otherwise model sampling noise reads as personality change.

3. **Give B8 a stereotype control.** Before reporting any demographic finding, check whether the same split is already predictable from the genesis persona text alone. If it is, the "finding" is the generator's prior, not emergent behaviour.

4. **Watch for effect-size inflation.** If Kasukabe ever replicates a social-science result, expect direction right, magnitude several times too large.

5. **Adopt the two-tier access model** if the 250 souls are ever published.

---

## 3. Papers this project also cites

- **Park et al., UIST '23 — *Generative Agents: Interactive Simulacra of Human Behavior*** (doi 10.1145/3586183.3606763). Source of the memory-stream architecture (observation / reflection / plan, importance scoring, retrieval by recency × importance × relevance) that `data/characters/*.json` uses — 5,174 seeded entries across the town. Also the origin of the Smallville map Kasukabe vendors.
- **EMNLP 2025 main.1530** — not yet read; noted for a later pass.

---

*September · internal research · read and summarised 2026-08-20*
