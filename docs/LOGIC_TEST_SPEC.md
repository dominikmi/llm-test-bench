# Advanced Logic Test Suite — Specification

Benchmark for **multi-step reasoning and logical discipline**: deduction,
constraint satisfaction, validity-vs-plausibility, underdetermination
recognition, causal intervention, self-reference, symbol manipulation, and
constrained planning.

Suite file: `test_definitions/logic.json` — 21 cases, 10 competencies ×
2-3 instances each.

Judge-free, tool-free: the model receives a puzzle prompt plus the same
structured-answer contract as the tool-use suite, and grading is typed
field matching (`answer.fields`). A logic runner is a degenerate case of
`benchmark_agent_tools.py`'s loop — `available_tools` empty, one turn —
and reuses `FieldSpec`, `_flatten_fields`, and `_grade_answer` unchanged.

## Design principles

### Anti-memorization

Every case is a **generated-format puzzle**, not a famous one. Invented
operators (`a ⊗ b = (a−b) mod 5`), parameterized swap chains, and synthetic
constraint sets cannot be recalled from training data — the model must
actually compute. The `notes` field on each case documents the verified
solution so fixture correctness is auditable.

### Difficulty tagging

Each case is tagged `floor` | `standard` | `hard`:

- **floor** — deliberately easy; a failure here indicates a fundamental
  problem (instruction-following, answer-format breakdown), not reasoning
  depth. Floor cases exist *because* hard cases alone can't distinguish
  "weak model" from "broken run."
- **standard** — the discriminating band for the 27-35B field.
- **hard** — paradox/edge cases (`selfref-08a`) where even strong models
  may legitimately struggle.

Current spread: ~10 floor, ~9 standard, 1 hard. If the field saturates,
difficulty knobs are built into every template (people count, swap chain
length, grid size, constraint count).

### The three discriminators

Cases `syllogism-02a`, `ordering-05a`, and `selfref-08a` target **epistemic
discipline** — the failure mode that matters most for agentic use:

- `syllogism-02a` is invalid but *plausible* — models answer by vibe unless
  they actually check entailment.
- `ordering-05a` is **partially underdetermined**: Carol=4th and Eve=5th are
  forced, but Alice/Bob/Dave are mutually free. A model that confidently
  assigns all five positions is overclaiming — directly analogous to the
  review suite's `unsupported_findings` failure.
- `selfref-08a` is a genuine paradox — the correct answer is "no consistent
  assignment exists," which requires the model to report non-existence
  rather than manufacture one.

## Grading schema additions

Two field types extend the tool-use contract:

| Type | Spec | Match rule |
|---|---|---|
| `set` | `{"values": [...]}` | normalized member-set equality (empty list valid) |
| `map` | `{"fields": {...}}` | flattens to dotted leaves; each leaf scores independently |

`enum` additionally accepts `any_of` for spelling variants
(`"b"`, `"box_b"`, `"box b"` all match `expect: "b"`).

`map` leaf-grading gives natural partial credit: a zebra grid scored as 9
leaf fields distinguishes "reasoned mostly right" (7/9) from "guessed" (~1/9).

## Addressed concerns (design constraints baked in)

| Concern | Mitigation |
|---|---|
| Single-instance variance | 2-3 verified instances per competency (21 cases); treat suite as a screen, extend via generator for statistical strength |
| Overlap with math bench | Arithmetic word problems excluded; `operator-*` covers calculation under invented semantics |
| Weak models flooring at 0 | `floor` difficulty tag per category; quality decomposition by difficulty isolates "can't reason" from "can't format" |
| Authorial solution errors | Every case `notes` field documents the worked solution; puzzles were verified for uniqueness/consistency before inclusion |
| Answer-format gaming | Same `required`-field contract; logic answers can't be guessed as easily as single booleans — most fields are enums/sets/maps with 3+ options |

## Known limitations

- **Instance count is still small.** 21 cases / 10 categories is a screen,
  not a ranking. A parameterized generator (seed → instance + answer) is
  the documented next step; the `notes` field records the reference solution
  pattern for each template.
- **Puzzles may be solvable by shortcut.** `ordering-05a` can be "solved" by
  confidently guessing — the `undetermined` field is what actually tests
  discipline, and it only scores if the model admits the free variables.
- **Prompt sensitivity.** Constraint phrasing affects solve rates; results
  compare models on identical prompts, not "ability in the abstract."
- **Thinking-mode asymmetry.** Reasoning models get value from budget on
  these cases; non-thinking instruct models answer immediately. Report
  `completion_tokens` alongside quality — a model solving `zebra-04a` in
  50 tokens vs. 3000 is a real efficiency difference, not noise.
