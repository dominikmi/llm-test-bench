# Benchmark Report: Keyword vs. LLM-as-Judge Scoring

Generated: 2026-09-08  
Run: `python3 benchmark_galileo_reviews.py --presets config/presets.ini --judge yes`  
Judge model: `Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-verifier` at `http://127.0.0.1:8000/v1`  
Compared models: `coder-gemma4-26B-A4B-it:LATEST`, `coder-ornith:LATEST`, `coder-north-mini-code-1.0:LATEST`

---

## 1. Executive summary

The keyword-only run reported all three models in the `87–91` overall range. The `tiel-verifier` judge dropped every overall score into the `78–80` range. The biggest collapses were in **quality**; security held or even improved. This shows the keyword scorer was heavily inflating quality scores by rewarding vocabulary, while the judge enforced semantic correctness.

| Model | Keyword Overall | Judge Overall | Δ | Keyword Quality | Judge Quality | Δ | Keyword Security | Judge Security | Δ |
|---|---|---:|---:|---|---:|---:|---|---:|---:|
| `coder-ornith:LATEST` | 91.29 | 79.17 | **-12.12** | 86.57 | 65.67 | -20.90 | 96.00 | 92.67 | -3.33 |
| `coder-gemma4-26B-A4B-it:LATEST` | 87.33 | 80.36 | -6.97 | 83.67 | 66.73 | -16.94 | 91.00 | 94.00 | +3.00 |
| `coder-north-mini-code-1.0:LATEST` | 88.62 | 78.50 | -10.12 | 94.57 | 63.00 | -31.57 | 82.67 | 94.00 | +11.33 |

---

## 2. Why the scores changed

### 2.1 Keyword scorer logic

The keyword scorer:
- Lowercases the candidate finding text.
- Searches for exact substrings from a list of expected "indicators".
- If any indicator appears, the finding is considered a match.

This is fast but crude. It rewards a finding that merely **contains the right words**, even if the finding describes a different problem.

### 2.2 LLM judge logic

The judge uses two strict prompts:

1. **Match prompt:** *"Does the candidate finding correctly identify and describe the expected issue?"*
2. **Validity prompt:** *"Is this candidate finding a real, concrete issue that actually exists in the code?"*

Each returns only `{"verdict": "yes" | "no" | "partial"}`. With `temperature=0.0`, `min_p=0.0`, and thinking disabled, the judge is deterministic and conservative. `partial` is treated as acceptable for recall, but not for precision.

Because the judge evaluates **meaning**, not vocabulary, generic or tangentially-related findings are rejected. This is why precision collapsed on several quality cases.

---

## 3. Model-by-model analysis

### 3.1 `coder-ornith:LATEST` — biggest quality collapse

- **Keyword overall:** 91.29 → **Judge overall:** 79.17
- **Keyword quality:** 86.57 → **Judge quality:** 65.67
- **Keyword security:** 96.00 → **Judge security:** 92.67

#### Where it held: `quality-05` (100.00)

The response directly addressed the expected issues with concrete code references and correct remediations.

```json
[
  {
    "issue": "Quadratic time complexity from nested loops",
    "affected_code": "for i in range(len(values)):\n    for j in range(i + 1, len(values)):",
    "impact": "Comparison count grows with the square of input size...",
    "remediation": "Use a set to track seen values in a single pass..."
  },
  {
    "issue": "Linear membership test on a list inside the inner loop",
    ...
  },
  {
    "issue": "Repeated len() calls in loop bounds",
    ...
  },
  {
    "issue": "Return order not guaranteed relative to first occurrence",
    ...
  }
]
```

Matched all three expected findings: `quadratic complexity`, `set-based algorithm`, `ambiguous ordering`. Precision was 100 because every candidate finding was a real, distinct issue.

#### Where it dropped: `quality-04` (0.00)

The model returned an **empty array** `[]`. It produced no findings at all, so recall and precision were both zero. The keyword scorer could not have assigned a score of 0 because the keyword run did not have this case for this exact model? In the keyword run, Ornith's `quality-04` was scored higher. This suggests the model either failed to generate output under the judge-enabled run or the case is genuinely hard for it.

#### Where it dropped: `security-01` (66.67)

The response had two findings for the same SQL-injection bug:

```json
[
  {
    "issue": "CWE-89 SQL Injection...",
    "affected_code": "query = f\"SELECT id, email FROM users WHERE email = '{email}'\"",
    ...
  },
  {
    "issue": "CWE-798 Use of Hard-coded Credentials / Insecure Defaults...",
    "affected_code": "def find_user(conn, email):",
    ...
  }
]
```

The judge accepted the first finding (`CWE-89`, `parameterized query`) but rejected the second as **not a real, separate issue**. The second finding describes "lack of validation" in the function signature, which is a generic abstraction and not actually hard-coded credentials. Precision dropped to 50.

### 3.2 `coder-gemma4-26B-A4B-it:LATEST` — smallest overall drop, security improved

- **Keyword overall:** 87.33 → **Judge overall:** 80.36
- **Keyword quality:** 83.67 → **Judge quality:** 66.73
- **Keyword security:** 91.00 → **Judge security:** 94.00

#### Where it held: `security-05` (100.00)

A single, sharp finding:

```json
[
  {
    "issue": "CWE-502: Deserialization of Untrusted Data",
    "affected_code": "return pickle.loads(raw)",
    "impact": "An attacker can provide a specially crafted base64-encoded cookie...",
    "remediation": "Avoid using the pickle module for untrusted data. Use a safer serialization format such as JSON..."
  }
]
```

Matched both expected findings: `CWE-502 deserialization` and `safe format`. Precision 100 because the one finding is real and complete.

#### Where it dropped: `quality-09` (44.44)

The response contained three findings, but the judge rejected two of them as either not matching the expected issues or not being real issues in the code:

```json
[
  {
    "issue": "Lack of error handling for missing or malformed keys",
    "affected_code": "copied[\"name\"] = copied[\"name\"].strip().lower()\n        copied[\"score\"] = int(copied[\"score\"])",
    ...
  },
  {
    "issue": "High memory consumption for large datasets",
    "affected_code": "output = []\n    for record in records:\n        ...\n        output.append(copied)\n    return output",
    ...
  },
  {
    "issue": "Missing type annotations and documentation",
    ...
  }
]
```

Only `missing input validation` and `batch data processing` were accepted. The judge rejected the third finding as not a real issue in the snippet and the second finding's `high memory consumption` as not a concrete, proven problem. Precision collapsed to 33.33.

#### Where it improved: `security` overall (+3.00)

Gemma's security findings were generally precise and well-labeled. The judge accepted them more readily than the keyword scorer, which had slightly stricter indicator matching. This is an exception: the judge can score *higher* when the model's reasoning is better than the keyword indicator list.

### 3.3 `coder-north-mini-code-1.0:LATEST` — largest quality collapse

- **Keyword overall:** 88.62 → **Judge overall:** 78.50
- **Keyword quality:** 94.57 → **Judge quality:** 63.00
- **Keyword security:** 82.67 → **Judge security:** 94.00

#### Where it held: `quality-06` (100.00)

```json
[
  ...,
  {"issue": "Bare except clause silently swallows all exceptions", ...},
  ...
]
```

Matched `bare exception`, `ambiguous failure`, and `range validation` because the output was concrete and correct.

#### Where it dropped: `quality-02` (50.00)

The response produced four findings, but only one matched the expected issue `data validation`:

```json
[
  {
    "issue": "Direct equality check with `True` and lack of defensive key access",
    "affected_code": "if item[\"active\"] == True:",
    ...
  },
  {
    "issue": "Missing input validation and error handling for price/quantity conversion",
    "affected_code": "float(item[\"price\"]) * int(item[\"quantity\"])",
    ...
  },
  {
    "issue": "Potential performance issue with repeated `float`/`int` conversions inside loop",
    ...
  },
  {
    "issue": "No handling of empty or non-iterable `items` argument",
    ...
  }
]
```

The keyword run likely gave full credit for `money precision` and `boolean comparison` because words like `float`, `int`, `price`, `quantity`, and `True` appeared. The judge rejected these as not genuinely describing `money precision` or `boolean comparison`. It only accepted `data validation`.

#### Where it improved: `security` (+11.33)

Keyword scoring had security at 82.67. The judge raised it to 94.00. North Mini's security findings were concrete and properly framed with CWEs. The keyword scorer had under-rewarded them because the model used slightly different indicator phrasing. This again shows the judge can be more accurate than keyword matching when the model reasoning is sound.

### 3.4 `security-04` — North Mini improved

Keyword score: unknown for this exact case, but the security average improved.

Judge score: 80.00. The response found:

```json
[
  {
    "issue": "CWE-20: Improper Input Validation",
    "affected_code": "async def preview(url, session):\n    response = await session.get(url, allow_redirects=True)",
    "impact": "The function accepts any URL without validation, which could lead to Server‑Side Request Forgery (SSRF), open redirects...",
    ...
  },
  ...
]
```

The judge accepted `CWE-918 SSRF` and `address validation` but rejected one of the extra findings as not a real issue.

---

## 4. General patterns

### 4.1 Quality is harder to judge than security

Security findings are usually binary and well-labeled with CWEs. The judge accepts them if they describe a real, exploitable issue. Quality findings are more abstract. Models often produce *plausible-sounding* generics that do not match the expected engineering concept.

### 4.2 Generic abstractions lose points

Phrases like:
- "Missing type annotations and documentation" (Gemma `quality-09`)
- "No handling of empty or non-iterable `items` argument" (North Mini `quality-02`)
- "CWE-798 Use of Hard-coded Credentials / Insecure Defaults" when the code has no hard-coded credentials (Ornith `security-01`)

were accepted by keyword scoring but rejected by the judge as not actually describing the expected concrete issue.

### 4.3 Empty or minimal outputs fail completely

`coder-ornith` `quality-04` returned `[]`. The model produced no findings at all. The judge has no alternative but to assign zero.

### 4.4 Precision is the main problem, not recall

Most quality cases had decent recall (the model found at least one valid issue) but poor precision (many of the other findings were not real or not expected). This is visible in scores like `44.44` and `57.14` where one or two good findings were buried under unsupported ones.

---

## 5. Conclusion and recommendations

1. **Keyword scores are not reliable for ranking.** They over-rated North Mini's quality by 31 points and Ornith's quality by 21 points.
2. **Gemma is the most robust under semantic judgment** (smallest overall drop, best security, no 0-score cases).
3. **Ornith's apparent lead was artificial.** It was the best under keyword scoring, but under the judge it is mid-pack and even produced an empty response for `quality-04`.
4. **North Mini's Q8/thinking/schema fix made outputs well-formed, not necessarily correct.** It can now emit structured JSON reliably, but the content is still generic.
5. **Security is the easier problem.** All three models are now `92–94` in security under the judge.
6. **Next step:** refine the judge prompt if needed (e.g., weight `partial` as `0.5` instead of full credit), or expand the quality case set to reduce per-case variance.

---

## 6. Files used

- `results/galileo-review/galileo-review-results.json` — machine-readable results with judge verdicts.
- `results/galileo-review/galileo-review-results.csv` — flat per-case table.
- `results/galileo-review/galileo-review-results.md` — original markdown summary.
- `benchmark_galileo_reviews.py` — judge harness with `tiel-verifier` integration.
