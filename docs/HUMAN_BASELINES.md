# What people actually do, and what Kasukabe does instead

**What this is.** The measured behaviour of real humans in the four
situations this town puts residents in, taken from published research, with
the numbers and the citations. It exists because the run reports keep
saying the town "is not behaving like humans" without anywhere to say what
humans *do* — so every judgement was against somebody's intuition.

**What it is for.** It is the spec the behavioural evals in
`app/world/evals/` are written against. Every threshold in those evals
traces to a row in this document, and every row here traces to a source.

**The discipline it inherits.** Build Spec v2 §10.4 forbids moving a
threshold after seeing a result. These thresholds come from research
published years before this project existed, which is the strongest form
of "fixed in advance" available: they cannot have been chosen to flatter a
run, because they were not chosen by us at all.

**A limit, stated up front.** Every figure below is from ordinary human
conversation and exchange. Kasukabe residents are not humans, and this
document does not claim they should score identically. It claims something
narrower and checkable: where the town falls *outside* the human range, that
is a finding worth a look, and where our own design contradicts a human
universal, the design is probably wrong. Section 6 lists three places where
it already does.

---

## §1 Turn-taking: being asked, and answering

### 1.1 A vocative is not decoration. It is speaker selection.

The foundational result, from Sacks, Schegloff and Jefferson's *A Simplest
Systematics for the Organization of Turn-Taking for Conversation* (1974):

> If the turn-so-far is constructed as to involve the use of a "current
> speaker selects next" technique, then the party so selected **has rights,
> and is obliged, to take next turn to speak, and no others have such
> rights or obligations**, transfer occurring at that place.

And the mechanism by which selection is done:

> In its simplest form, **a vocative such as the name of the next speaker**
> is uttered to designate the next speaker.

**What this means for us.** When a resident says *"Girl. Two sim for
walking, you said?"*, naming Tova, that is not flavour text. It is the
selection of Tova as next speaker, it places an obligation on her that it
places on nobody else, and it is the single most load-bearing act in a
conversation. A world that renders it to her as ordinary overheard speech
has deleted the mechanism that makes conversation work.

### 1.2 How often a selected party actually answers

Stivers et al., *Universals and cultural variation in turn-taking in
conversation*, PNAS 106(26):10587–10592 (2009). 350 consecutive questions
per language, ten languages, five continents.

| | |
|---|---|
| Responses that are ANSWERS rather than non-answers | **64% – 87%** |
| Lowest (Korean) | 64% |
| Highest (Dutch, Yélî-Dnye) | 87% |
| Confirmations as a share of all answers | 70 – 89% |
| Median gap between turns | **+100 ms** |
| Mean gap | +208 ms |
| Mode | 0 ms |
| Cross-language spread of the mean | within ≈250 ms either side |
| Responses that overlap the question | ~17% |

**The floor is 64%, and it is the floor of the slowest-answering language
measured.** This is the number the reply-rate gate should be read against.

### 1.3 What Kasukabe measures

| Run | Reply rate |
|---|---|
| final1 | 32% |
| final2 | 43% |
| final3 | 32% |
| final4 | 37% |
| final5 (void) | 52% |

**Every run is below the human floor, and the best is twelve points under
the slowest human language.** The gate's 60% bar, chosen by this project
before any of this research was read, turns out to sit just below the
human minimum. It was a good guess and it stands.

---

## §2 How many people one conversation holds

### 2.1 The four-person ceiling

Dunbar, *Size and structure of freely forming conversational groups*, Human
Nature (1995); and Dunbar, *Why are conversations limited to about four
people?*, Evolution and Human Behavior (2018).

| | |
|---|---|
| Upper limit on people in one spontaneous conversation | **~4** |
| Observed mean, university refectory | **3.4** (one speaker + 2.4 listeners) |
| What happens at 5 | the conversation **fissions into two** |
| What happens at 6 | oscillates between 4/2 and 3/3 splits |
| Why | mentalizing capacity: a person can hold ~3 other minds at once |

> "Groups tended to partition into new conversational cliques at multiples
> of about four individuals."

### 2.2 This contradicts our own gate, and the contradiction explains a failure

**G3 rewards ≥3 distinct speakers in one conversation and sets no ceiling.**
The reports celebrate the results:

- final3: *"a 20-remark, **6-speaker** breakfast thread"*
- final5: *"an 11-remark, **6-speaker** breakfast"* — called "the ceiling of
  what the dynamic-chat system produces"
- run5 of the earlier series: **8 speakers** in one gathering, reported as
  the largest yet

A six- or eight-speaker single thread is not a human conversation. Humans
in that situation are having **two** conversations, and the research says so
in as many words.

And the consolidated report already noticed the consequence without
identifying the cause. Of final3 it says: *"wide but shallow, **exactly the
profile that clears G3 and fails G1/G2**."*

That is what over-large conversations produce. Nobody answers because
nobody is selected; everybody talks past everybody. **The system is being
graded for producing a structure humans do not produce, and is then marked
down for the shallowness that structure causes.**

**What follows.** Group size needs a ceiling and a fission rule, not just a
floor. §7 turns this into an eval.

---

## §3 Who is in a conversation, and who is merely near it

Goffman's participation framework (*Footing*, 1979), extended empirically by
Goodwin and Goodwin.

Goffman's whole point was that **the speaker/hearer pair is not enough** to
describe what happens when three or more people are present. His categories:

| Role | What it means | Obliged to reply? |
|---|---|---|
| **Addressed recipient** | selected, spoken to | **Yes** — SSJ's obligation |
| **Ratified unaddressed** | in the conversation, expected to listen, may self-select | No, but may speak next |
| **Bystander / overhearer** | present, not in the conversation | No |

> "Goffman emphasized the inadequacy of the dyadic model of communication,
> involving only speakers and hearers."

> Participation "is not a set of fixed roles but an ongoing, locally
> produced accomplishment."

**What Kasukabe has today.** Two states: `listener_agent_id` — exactly one
person — and everybody else. That is precisely the dyadic model Goffman
called inadequate, and the town is full of three-and-more-person rooms.

**Measured consequence.** A resident named by a vocative anywhere other than
the `listener_agent_id` parameter is rendered the same as a passer-by who
happened to be within earshot. Reproduced:

```
Anselm (named in the parameter)   involves_self=True    "... said to you: ..."
Tova   (named in the sentence)    involves_self=False   "... said: ..."
```

The world *knows* Tova was addressed — `addressed_agent_ids` holds her — and
tells her nothing.

---

## §4 Trade: how a price is settled

Sources: Program on Negotiation (Harvard Law School) on price anchoring;
Loschelder et al. (2017) on precise first offers; Springer, *Beyond the
First Offer* (2023) on opening moves and concession paths.

| Finding | Detail |
|---|---|
| The first number anchors the whole negotiation | it shapes every subsequent offer |
| Precise offers beat round ones | recipients make **smaller counteroffers** against a precise first offer |
| Ambitious first offers produce better outcomes | for the party making them |
| Concessions are a sequence, not one step | the **third offer** and the **average concession** mediate the outcome |
| Conservative openings shorten the haggle | fewer bargaining rounds |

**What this says about Kasukabe.** Run 19 recorded a price agreed out loud
— Corwin's *"Thirty-six sim for all three"*, Nell's *"Fair price. I'll take
them"* — and **zero** money moved, all run. Human bargaining ends in a
transfer; that is what distinguishes a bargain from a chat about a bargain.
The settlement gate (G4) is the right shape and its bar of one is, if
anything, generous.

Run 20 is the counter-example and the proof the fix worked: 21 sells, 17
buys, 16 sales worth 1,536 sim.

---

## §5 Help, favours, and debt

Sources: Gouldner, *The Norm of Reciprocity* (1960); reciprocity in
cultural anthropology (direct barter → delayed gift exchange).

| Finding | Detail |
|---|---|
| Almost every society endorses the reciprocity norm | help those who helped you; penalties are legitimate against those who do not |
| **Delayed** exchange creates two things | a *relationship* **and** an *obligation* — a debt |
| Failure to reciprocate is felt | indebtedness, guilt aversion, behavioural compensation |
| A gift's return may differ in kind | and be deferred indefinitely |
| Unrequested favours still oblige | recipients reciprocate even with no demand attached |

**Kasukabe already produces this, and it is the best material in the run
log.** Bram owes Marta 400 sim. Neither has said the word "repayment" out
loud. Ainsley has set 400 aside for it and Bram does not know. Marta refuses
to settle it behind his back: *"I'll not take what Bram doesn't know he's
giving."*

That is the reciprocity literature acted out — a delayed obligation, felt as
debt, complicated by face. **Nothing needs adding here. It needs
measuring**, so a run that produces it can be told from one that does not.

---

## §6 Where our design contradicts the research

Three, in order of how much they cost.

1. **Only one person can be addressed.** §3. The dyadic model, in a town of
   rooms with five people in them. Fix: task #17.
2. **Conversations have a floor and no ceiling.** §2. We reward the 6- and
   8-speaker threads that humans split, then mark down the shallowness that
   causes. Fix: a fission expectation, §7.3.
3. **A vocative carries no obligation.** §1.1. Being named is the strongest
   signal in conversation and reaches the named resident as ordinary
   ambient speech.

---

## §7 The behavioural evals these baselines license

Stated in words here; implemented in `app/world/evals/behavioural.py` and
pinned by deterministic tests. Every bar cites its row above.

| # | Eval | Bar | Source |
|---|---|---|---|
| B1 | Of remarks that SELECT a next speaker by vocative, the share the selected resident answers | ≥ 64% | §1.2, Stivers floor |
| B2 | A resident named in a remark is told they were addressed | 100% | §1.1, §3 — mechanism, not taste |
| B3 | Median conversation size, by distinct speakers in one thread | ≤ 4 | §2.1 |
| B4 | Threads exceeding 4 speakers that FISSION rather than continuing as one | majority | §2.1 |
| B5 | Agreed prices that end in a transfer | ≥ 1, and the share rising | §4 |
| B6 | Favours given that are returned, in kind or otherwise, within the run | reported, not barred | §5 — a rate, because the run is short |
| B7 | Debts named in speech and still unsettled at stop | reported | §5 — this is a *feature*, not a defect |

**B6 and B7 are deliberately reported rather than gated.** Reciprocity is
*delayed* by definition and a 72-tick run is one morning; a bar would be
measuring the run length. What is worth knowing is the number and its
direction across runs.

---

## §7.1 What the live judge found, and what it refuted

`world/evals/answered_or_deflected.py` asks a real model, one exchange at
a time, whether the reply ANSWERED the question — the thing no regular
expression can decide. Run against `final1`, 50 exchanges:

| Outcome | Count | Share |
|---|--:|--:|
| answered | 13 | 26% |
| deflected | **3** | **6%** |
| silent | **34** | **68%** |

**This refutes the explanation the consolidated report gave.** That report
argued, from the gap between the graded reply rate and a generous
"did they speak again at all" reconstruction:

> "The gap IS the finding: residents remain present and keep talking, but
> the next thing they say is frequently their OWN AGENDA rather than an
> answer to the question they were just asked."

That predicts a large deflection share. The judge measures deflection at
**three exchanges out of fifty**. The overwhelming failure is not a bad
answer, it is **no answer at all** — two thirds of the people selected
never spoke back to the asker.

**And the half nobody had looked at is healthy.** Of the sixteen who did
speak back, thirteen genuinely answered: **81%**, which sits inside the
human 64–87% band and near its top. A Kasukabe resident who replies
replies about as well as a person does.

So the problem is not conversational quality. It is reply INITIATION, and
that is a different thing to fix: the town is not evasive, it is
inattentive. Whether the addressee-blindness fix (§6.1) moves this is the
first question the next run should answer, and B1 plus this judge now
measure both halves separately.

---

## §8 What this document must not become

A list of behaviours to instruct the residents to perform. If the prompt
told a resident to answer when named, B1 would measure the prompt and
nothing else — the same trap `docs/runs/SERIES.md` §4 identifies and the same reason
the open-questions section had its "or let it go" line removed rather than
replaced with an order.

The mechanism changes in §6 are different in kind. Telling Tova she was
spoken to is not instructing her to reply; it is repairing a world that was
lying to her about what happened.

---

## Sources

- [Sacks, Schegloff & Jefferson (1974), *A Simplest Systematics for the Organization of Turn-Taking for Conversation*](https://webspace.science.uu.nl/~vrees101/code/turntaking/Turntaking_SSJ.pdf)
- [Stivers et al. (2009), *Universals and cultural variation in turn-taking in conversation*, PNAS 106(26)](https://pmc.ncbi.nlm.nih.gov/articles/PMC2705608/)
- [Dunbar et al., *Size and structure of freely forming conversational groups*, Human Nature](https://link.springer.com/article/10.1007/BF02734136)
- [Dunbar (2018), *Why are conversations limited to about four people?*, Evolution and Human Behavior](https://www.sciencedirect.com/science/article/abs/pii/S1090513818301491)
- [Goffman's participation framework — addressed / unaddressed / bystander](https://compass.onlinelibrary.wiley.com/doi/10.1111/j.1749-818X.2011.00286.x)
- [Gaze, addressee selection and turn-taking in three-party interaction (InLiSt 60)](https://www.inlist.uni-bayreuth.de/issues/60/Inlist60.pdf)
- [Program on Negotiation, Harvard Law School — Price Anchoring 101](https://www.pon.harvard.edu/daily/negotiation-skills-daily/price-anchoring-101/)
- [*Beyond the First Offer: Decoding Negotiation Openings*, Group Decision and Negotiation (2023)](https://link.springer.com/article/10.1007/s10726-023-09813-5)
- [Reciprocity (cultural anthropology)](https://en.wikipedia.org/wiki/Reciprocity_(cultural_anthropology))
- [Norm of reciprocity — Gouldner (1960)](https://grokipedia.com/page/Norm_of_reciprocity)
