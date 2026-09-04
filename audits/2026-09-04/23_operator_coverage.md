# 23 — What the mutation operators cannot express

Scope: `src/py_ci_shared/mutation_teeth.py` (operator set) measured against the real swept consumer
`realtime_applications/` (`stage2_prompt_builder/user_prompt.py`, `prompt_safety.py`, `models.py`,
`letter_guards.py`, plus `truncation.py` / `word_count.py` / `formatting.py`, which are in the same
package and the same defect family).

Everything labelled DEMONSTRATED was produced by running `generate_mutants` on the real file, under
Python 3.14.3, and reading the mutant list. READ means I read both sides and reasoned, without
executing the tests.

## Baseline measurement (DEMONSTRATED)

`generate_mutants` over seven real files, no line filter:

| file | mutants | emptied-string | numeric +1 | everything else |
|---|---|---|---|---|
| letter_guards.py | 51 | 35 | 1 | 15 |
| prompt_safety.py | 241 | 200 | 3 | 38 |
| user_prompt.py | 119 | 54 | 13 | 52 |
| models.py | 547 | 331 | 56 | 160 |
| truncation.py | 67 | 16 | 18 | 33 |
| word_count.py | 34 | 0 | 12 | 22 |
| formatting.py | 115 | 26 | 26 | 63 |
| **total** | **1174** | **662 (56%)** | **129 (11%)** | **383 (33%)** |

Two facts to keep in mind while reading the findings below: **"empty a string" is more than half of
everything the harness does on this consumer**, and the operator set as shipped is wider than the
brief stated — `and`/`or`, `not`-drop, `is`/`is not`, `in`/`not in` and `True`/`False` all exist and
all fire on this code (verified in the letter_guards dump). Findings that the brief listed as
candidates but which are already covered are recorded under "Already covered" at the end, so nobody
re-proposes them.

---

## P0-1 — Everything inside an f-string is unmutable, and this consumer builds its prompts entirely out of f-strings

**DISPOSITION -- RESOLVED.** `FSTRING_MIDDLE` is now mutable. The objection that made f-strings be skipped entirely applies to emptying a whole f-string TOKEN, which breaks the interpolations with it; a SEGMENT is the literal run between interpolations and removing one leaves every `{...}` in place, which is pinned by a test that counts `FormattedValue` nodes in the mutant. Measured on the real consumer: the prompt template went from 8 mutants (3 of them from slots) to 30, and the first sweep with it found that the CLOSING tags of the question and attachment fences could be deleted with no test noticing. That is the boundary the whole fencing design rests on, and it was unasserted.

**Claim.** On Python 3.12+ f-strings tokenise as `FSTRING_START`/`FSTRING_MIDDLE`/`FSTRING_END`, the
string operator only fires on `tokenize.STRING`, so no literal text inside any f-string is ever
mutated — and in `user_prompt.py` the whole prompt template, including every nonce fence tag, is one
f-string.

**Where.** `stage2_prompt_builder/user_prompt.py:339-365` (the `prompt = f"""\ ... """` block).

**DEMONSTRATED.** `generate_mutants` produces **8 mutants across those 27 lines, 3 of them
emptied-strings — and all 3 are `', '` / `'Not specified'` tokens sitting inside `{...}`
interpolations**, i.e. ordinary `STRING` tokens that happen to be nested in the template. The
template's own text produces nothing. Specifically un-mutable:

- `## Word Count Target` / `Write {target_words} words (hard ceiling {ceiling_words})...` (L341-342)
- `<job_title_{title_nonce}>` and its closing `</job_title_{title_nonce}>` (L353)
- `<job_description_{desc_nonce}>` / `</job_description_{desc_nonce}>` (L362-364)
- `## Detected Language`, `## Relevant Skills for This Job`, `## Job Posting` section headers

Same at `user_prompt.py:300` (`<attachment_{attachment_nonce}>`), `:311-314` (the image fence added
by AUDIT 05.2), `:189` (`<client_info_{ci_nonce}>`), and `prompt_safety.py:390`
(`f"<{tag_name}_{nonce}>...</{tag_name}_{nonce}>"` — the single function every other fence delegates
to).

**Why it matters.** The brief says one of today's two real catches was *an emptied fence separator*.
In this consumer the fences are f-strings. The harness would be silent on the identical defect here.
A broken or asymmetric closing fence is not cosmetic: `sanitize_nonce_content` escapes closing tags
precisely because a forged `</job_description_...>` is the injection this module exists to stop, and
a mismatched fence pair means attacker content sits outside the frame the system prompt tells the
model to distrust. Nothing in the current operator set can produce that mutant.

**Also blocked by the same gap:** `rf"<{_INVISIBLE_RUN}(/)"` and its three siblings at
`prompt_safety.py:272-275` — the four markup-escape patterns from AUDIT 05.8 — are `rf` strings and
therefore get **zero** mutants, not even the emptying one. The one pattern in that block that is a
plain raw string, `_INVISIBLE_RUN` at `:268`, does get emptied, and emptying it reproduces the
pre-05.8 defect exactly. So the operator works on the constant and is blind on the four regexes that
use it.

**Remedy.** Mutate `FSTRING_MIDDLE` tokens. Verified mechanically: for
`f"""## Job Posting\n- Title: <job_title_{n}>{t}</job_title_{n}>\n"""` the tokeniser hands back four
`FSTRING_MIDDLE` spans with exact source offsets (`'## Job Posting\n- Title: <job_title_'`, `'>'`,
`'</job_title_'`, `'>\n'`), each independently replaceable. Emptying an `FSTRING_MIDDLE` is always
syntactically valid — it removes literal text only and cannot disturb a `{...}` interpolation, which
is what the current skip comment (`mutation_teeth.py:176-181`) was actually worried about. That
comment's reasoning is sound for `tokenize.STRING` f-strings under the *old* tokenisation; it does
not justify skipping `FSTRING_MIDDLE`, and `tests/test_mutation_teeth.py:162
test_f_strings_are_left_alone` currently pins the wrong behaviour.
Guard: skip an `FSTRING_MIDDLE` whose span is only whitespace/newline, or the diff is unreadable.

**Value: very high.** It is the largest single blind region in the swept file, and the blind region
is the security boundary.

---

## P1-2 — Regex internals cannot be perturbed; the only regex mutant is "delete the whole pattern"

**DISPOSITION -- DEFERRED.** Regex internals remain unmutable. The report is right that this consumer's guards are regexes and that the file documents a shipped bug from a missing trailing anchor. The reason for deferring is that a general regex perturbation produces mostly invalid or trivially-equivalent patterns, and the useful subset (drop a `\b`, widen a quantifier) needs a design decision about which perturbations are meaningful rather than noisy -- exactly the judgement that made the string operator produce 56% of all candidates. Waiting on: a proposal that names the perturbations and measures their yield on the real guard tables.

**Claim.** A regex literal is a string, so the sole mutant is `-> ""`, which usually either matches
everything or crashes; the defect this codebase actually ships is a quantifier or anchor that is one
notch too wide or too narrow, and no operator can produce it.

**Where.** `letter_guards.py:56-66`. The comment on L57-61 is the receipt:

> `\b` AFTER the alternation as well as before it. Without the trailing boundary `\bi` matched the
> "i" of "is", so "Your current dashboard is not the right fit for this data volume" ... read as the
> freelancer withdrawing.

That is a shipped, found, fixed regex-anchor bug in the exact file under sweep. Dropping that `\b`
again is undetectable by the harness. Same shape at `letter_guards.py:62-64`
(`\W{0,3}`, `\W{1,3}`, `(?:\w+\W{1,3}){0,3}?` — six independent bounds), `:89`
(`(?:ing|s|ed)?`, the widening the comment says was found *because* a mutation run showed the table
untested), `:175-177` (`[^.?!]{0,60}?`, `[^.?!]{0,30}?`, `([^"...]{1,60})`), `:226`
(`\[[A-Z]{1,2}\]` — the comment calls this deliberately narrow, so narrow/wide is the whole
decision), `prompt_safety.py:107` (`(?:\s+\w+){0,3}\s+`), `:517` (`_ZERO_WIDTH_RE` class members).

**DEMONSTRATED.** The full letter_guards mutant list contains 14 regex literals, each with exactly
one mutant, all `-> ""`. No mutant touches a quantifier, an anchor, an alternation branch, or a flag.

**And the one mutant that exists is weak.** `re.compile("")` matches at position 0 of every input, so
emptying a `_BANNED` pattern makes `find_self_elimination` flag *every* letter — killed by any
clean-letter test, which tells you nothing about that pattern. Emptying `_REQUIRED_TOKEN_RE`
(`:174`) makes `match.group(1)` raise `IndexError` — a crash-kill, which the harness already knows
is a free kill (`_killed_by_crash`). So nine of the nine `_BANNED` patterns are, in practice,
untested by the harness.

**A test would not catch any of it.** `find_self_elimination` returns labels; two patterns share a
label (`:52`/`:69`, `:76`/`:83`, `:96`/`:103`, `:110`/`:114`), and `:132` dedups by label. So a
per-pattern regression could be masked by its sibling and the label list is unchanged.

**Remedy.** A dedicated regex operator that parses the pattern (`re._parser` or a textual pass) and
emits a small set of targeted perturbations, each one edit: `{m,n}` -> `{m,n+1}` and `{m-1,n}`; `?`
on a group -> removed; a leading or trailing `\b` -> removed; `re.I` / `re.IGNORECASE` dropped from
the `re.compile` call; one alternation branch removed. Cap at ~3 per pattern (the sampling machinery
already exists) so a nine-pattern table does not explode.

**Value: very high** for this consumer specifically — `letter_guards.py` and `prompt_safety.py` are
~30% regex by line, and both modules' entire job is where the pattern boundary sits.

---

## P1-3 — The container sampler never fires on an ANNOTATED module constant, so `_BANNED` is exhausted, not sampled

**DISPOSITION -- RESOLVED.** Two defects in one finding, both fixed. `ast.AnnAssign` is matched as well as `ast.Assign`, so an annotated module constant is sampled; and the sampler records the element's SPAN instead of its start offset, tested by containment -- keying on the offset of the element's opening bracket suppressed nothing, because the mutable tokens sit inside it at their own offsets. Going further than the report: sampling is now skipped entirely for a table whose rows contain a CALL. A row that contains an expression which DOES something is behaviour, not data, and suppressing 14 of 17 `re.compile` guards would have hidden the substance of the module under a rule written to hide its noise -- with the content-hash seed keeping the same rows suppressed until the file changed. Measured: `letter_guards.py` stays at 53 mutants, `models.py`'s two country tables are sampled.

**Claim.** `_container_members` matches `ast.Assign` only; a module constant written
`X: tuple[...] = (...)` is an `ast.AnnAssign` and is never sampled — and nested elements are keyed on
the element's opening `(`, which is not a mutable token, so sampling would be a no-op even if the
node type matched.

**Where.** `mutation_teeth.py:317-348` (`_container_members`), applied to `letter_guards.py:50`
(`_BANNED: tuple[tuple[str, re.Pattern[str]], ...] = (` — nine elements, `_CONTAINER_SAMPLE` is 3)
and `prompt_safety.py:279` (`_MARKUP_ESCAPES`).

**DEMONSTRATED.** Two independent checks:

1. `_container_members(letter_guards_source)` returns `{}`. So does the map for all four briefed
   files — `generate_mutants(...)[2]` is `{}` on every one of them, i.e. the sampler has never once
   fired on this consumer.
2. Minimal repro: for `X: tuple[str,...] = ('a',...,'f')` and `Y = ('a',...,'f')` side by side,
   `_container_members` returns three offsets, **all of them in `Y`**. `X` is invisible.

**Why it matters.** Two ways, in opposite directions. (a) The noise control the module's own
docstring says was measured (two tables = 18% of lines, 64% of mutants) is silently inoperative
against annotated tables, which is how a typed codebase writes them; `_MARKUP_ESCAPES` and `_BANNED`
are both annotated or nested. (b) More subtly, if it *did* fire on `_BANNED`, the element start
offset is the inner tuple's `(`, an `OP` token not in `_OP_SWAP`, so the mutants that actually exist
for that table (the label strings and the regexes, at different offsets) would still not be marked
`noise:` — sampling would silently under-suppress rather than fail loudly. The `sampled_containers`
line in `MutationRun.summary()` would say "sampled 3 of 9" while all nine rows were still mutated.

**Remedy.** Handle `ast.AnnAssign` alongside `ast.Assign`; and mark the element's whole *span*
(`col_offset`..`end_col_offset`) as sampled-out rather than its start offset alone, so nested
literals inside a sampled row are actually suppressed.

**Value: high** — it is a correctness bug in an existing operator's noise control, and it makes the
run summary report a suppression that did not happen.

---

## P1-4 — Argument ORDER in a same-typed argument list is never transposed

**DISPOSITION -- DEFERRED.** Argument transposition is not implemented. The finding is real and the logging-format case is a genuine defect shape here. It is deferred rather than rejected because a correct implementation needs argument SPANS from the AST, and the byte-versus-character offset trap that produced this module's worst historical bug -- a whole statement destroyed and reported at an untouched line -- lives exactly there. Waiting on: doing it with the same care the token path got, which is more than the remaining budget of this round.

**Claim.** No operator reorders arguments, so every `%`-format log call and every positional
multi-value construction in this consumer is transposition-proof by luck, not by test.

**Where** (all real, all same-typed adjacent arguments):

- `stage2_prompt_builder/user_prompt.py:261-266` —
  `logger.warning("Job %s: %d attachments, keeping the first %d", job.uid, len(_attachments), _max_attachments)`.
  Swapping the last two produces `20 attachments, keeping the first 10` -> `10 attachments, keeping
  the first 20`, which is a *plausible-reading* log line that would mislead the operator the comment
  at `:250-255` says these logs exist for ("the first posting that hits one needs to be visible").
- `user_prompt.py:96-101` — `"Job %s description truncated: %d -> %d chars", job.uid, original_len, len(truncated_desc)`.
  Transposed, the log says the description GREW.
- `models.py:945` — `"adaptive_strategies exceeds safety cap: %d bytes > %d -- dropping", _adaptive_bytes, _MAX_ADAPTIVE_STRATEGIES_BYTES`.
- `models.py:951` — `"cover_letter_text exceeds safety cap: %d bytes > %d -- truncating", encoded_len, _MAX_COVER_LETTER_BYTES`.
- `stage2_prompt_builder/word_count.py:49` — `sd, sm, sx, dm, dx = _cl_thresholds()` against the
  five-int tuple returned at `:23-29`. Transposing `sm, sx` (short min / short max) or `dm, dx`
  inverts a floor and a ceiling with no type change and no crash; `compute_target_words` then returns
  `(target, ceiling)` with `ceiling < target`, and the prompt at `user_prompt.py:342` says
  "Write {target} words (hard ceiling {ceiling})". This is the highest-consequence instance.

**What a test would do.** The `caplog` tests the module docstring counts (152 references, 54
asserting record contents) would catch a transposition *only if* they assert the rendered message
with both numbers distinct. The word_count tuple transposition would be caught only by a test that
pins target and ceiling to different values across all three branches.

**Why it matters.** `models.py` documents two shipped bugs of exactly the "read the wrong one of two
interchangeable things" family: `:348` (`profile.firstName` vs `raw.firstName` — "the root read was
always None ... This name goes in the cover letter's sign-off") and `:371` (`nSS100BwScore` vs
`jobSuccessScore` — "0 of 2,000 rows ... the `min_jss` eligibility gate could never fire"). Those are
not argument transpositions, but they are the same defect: two same-shaped things in the wrong order
or the wrong slot, with no type error to catch it. The operator set has no member in this family.

**Remedy.** Two cheap operators, both AST-local:
(a) **argument transposition** — swap two ADJACENT positional args of a `Call` when both are "simple"
(Name, Attribute, `len(...)`, Constant of the same Python type), one swap per adjacent pair, capped
at 2 per call site;
(b) **unpack-target transposition** — swap two adjacent names in a tuple-assignment target
(`sd, sm, sx, dm, dx = ...`) or two adjacent elements of a returned tuple literal.
Both are syntactically safe by construction and produce a diff a reader instantly understands.

**Value: high.** (a) alone covers four sites in one file; (b) covers `word_count.py:49` and `:23-29`,
where the consequence is a ceiling below a floor in a shipped prompt.

---

## P1-5 — `continue` / `break` / bare `return` are not mutable, and the loops here are budget loops

**DISPOSITION -- RESOLVED.** `continue` and `break` swap in both directions. Measured on the consumer: three cap-enforcement loops in `user_prompt.py`, `prompt_safety.py` and `llm_cache.py` gained mutants, which is the same family as the off-by-one at a cap that this harness found earlier the same day.

**Claim.** No operator touches control-flow keywords, so "the loop stopped when it should have kept
going" — and its inverse — cannot be generated, in loops whose entire purpose is enforcing a cap.

**Where.**

- `stage2_prompt_builder/truncation.py:147-152`:
  ```
  if accum and word_count + sent_words > max_words:
      break
  accum.append(sent)
  word_count += sent_words
  if word_count >= max_words:
      break
  ```
  `break` -> `continue` on the first one means a long sentence is skipped but a later short one is
  still appended, so the function silently exceeds `max_words`. That is *the same defect class as
  today's headline catch* (an off-by-one at a cap), one keyword over. `break` -> `continue` on the
  second is subtler: it keeps consuming after the budget is met.
- `stage2_prompt_builder/user_prompt.py:271` — `continue` inside the attachment budget loop. Changing
  it to `break` drops every *later* attachment, including images and unreadable-file notes that cost
  no characters at all; the section then silently omits attachments the client sent, which is the
  precise failure the comment at `:242-247` argues against ("an attachment that vanishes makes the
  letter read as though it ignored the client").
- `prompt_safety.py:420` — `continue` in `job_injection_surface`'s field loop; `break` there
  truncates the injection SURFACE, so `check_dangerous_job` (`:443`) stops scanning fields. A
  security check that silently examines less.

**What a test would catch.** A truncation test with several sentences of unequal length would catch
the first; a single-sentence test would not. An attachment test with one over-budget file and one
image after it would catch the second; the more natural single-attachment test would not.

**Remedy.** `continue` <-> `break` swap (statement-level, trivially valid — both are legal wherever
the other is, inside a loop), and `return <expr>` -> `return` where the function's other returns
permit it. The `continue`/`break` swap alone is three real sites in three files.

**Value: high.** Cheap to implement, zero syntax risk, and it lands on cap-enforcement loops, which is
the class the harness has already proven it earns its keep on.

---

## P2-6 — String constants can only be EMPTIED, and emptying is often either a crash or a no-op; substitution is the operator that matches the real defects

**DISPOSITION -- DEFERRED.** String SUBSTITUTION is not implemented; emptying remains the only string operator. The examples given are convincing (`NFKD` to `NFKC`, `errors=replace` to `ignore`) and they are convincing precisely because they are domain-specific -- a generic substitution rule has no principled form, and an arbitrary one would add to the 56% of candidates that emptied strings already produce. Waiting on: a rule that says which strings deserve a substituted neighbour rather than an empty one.

**Claim.** Emptying is 56% of all mutants (662/1174, DEMONSTRATED) and for a whole family of literals
it is a strictly weaker test than substituting a *sibling* literal from the same file.

**Where each shape appears.**

- **Unicode normalisation form.** `prompt_safety.py:636,641`:
  `unicodedata.normalize("NFKD", text)` then `normalize("NFKC", text)`, with a comment stating the
  order is load-bearing ("Must run BEFORE NFKC (which would recompose the marks)"). Emptying `"NFKD"`
  raises `ValueError` — a crash-kill, free, tells you nothing. Substituting `"NFKD"` -> `"NFKC"`
  produces the *documented* defect and would be caught only by the Zalgo doctest
  (`check_taboo_words("f̀uck")`) — which is exactly what makes it a good mutant: it proves that
  doctest is load-bearing rather than decorative.
- **Codec error policy.** `models.py:953,958`:
  `.encode("utf-8", errors="replace")` for measuring, `errors="ignore"` for decoding after the byte
  cut, with a nine-line comment explaining why each is what it is. Emptying `"replace"` raises;
  swapping `"replace"` <-> `"ignore"` between the two call sites is a live, silent, byte-accounting
  defect.
- **The fullwidth escape characters.** `prompt_safety.py:280-284`, `_MARKUP_ESCAPES` maps to
  `"／"`, `"！"`, `"！"`, `"？"`. Emptying `"／"` sets `chars[m.start(1)] = ""`, which *deletes* the
  slash — the closing tag is still destroyed, so the mutant may well be equivalent for any test that
  only asserts "no `</` survives". Substituting `"／"` -> `"/"` is the real regression (the escape
  becomes a no-op) and is caught only by a test asserting the fullwidth character specifically. This
  is a case where the existing operator produces a mutant that is *weaker than useless*: it can
  survive while looking like a tested string.
- **Dict key literals.** `user_prompt.py:279,280,320` (`attachment["file_name"]`,
  `attachment.get("kind")`, `attachment.get("reason", "unreadable")`), `models.py:988-999`
  (twelve `data.get("...")` reads). Emptying a key is real coverage (it becomes "read nothing") and
  IS generated — credit where due. What is missing is key SUBSTITUTION, which is the shape of the two
  bugs `models.py:348` and `:371` document verbatim. Swapping `data.get("reasoning")` ->
  `data.get("detected_language")` yields a plausible, type-correct wrong answer.
- **Branch-discriminating literals.** `user_prompt.py:375,381`: `.get("status", "CLOSED")` then
  `if status == "ACTIVE"`. Substituting `"CLOSED"` -> `"ACTIVE"` in the default flips which paragraph
  an unknown-status returning client gets. (`==` -> `!=` at `:381` covers the branch inversion, so
  this one is partly covered — listed for completeness.)

**Remedy.** Keep emptying (it earned its place: it caught the fence separator, and it is genuinely
the right operator for `_INVISIBLE_RUN` at `prompt_safety.py:268` and for dict keys). ADD a
**sibling-substitution** operator: for a string literal, if another *distinct* string literal of the
same "kind" appears elsewhere in the same file — same enclosing call's argument position, or same
dict-key role, or drawn from a small hardcoded family (`{"NFC","NFD","NFKC","NFKD"}`,
`{"ignore","replace","strict","surrogateescape"}`) — emit one mutant substituting it. Deterministic
choice (first sibling in source order) so the baseline key is stable.
Suppress substitution for strings longer than ~40 chars (prose), where emptying is the right unit.

**Value: medium-high.** It converts a large, mostly-cheap operator into a discriminating one for
exactly the literals whose *value*, not existence, is the decision.

---

## P2-7 — `+=`, `//`, `%`, `**` and the other compound operators are not in `_OP_SWAP`

**DISPOSITION -- RESOLVED.** `//`, `%`, `**` and the compound assignments are in the table. `+=` becoming `=` on an accumulator is the attachment-budget defect exactly -- the running total stops running -- and it now has a regression test that says so.

**Claim.** `_OP_SWAP` (`mutation_teeth.py:216-227`) contains only `> >= < <= == != + - * /`.
`tokenize` emits `+=`, `-=`, `//`, `%`, `**`, `//=`, `>>`, `&`, `|` as their own single OP tokens, so
none of them are ever mutated.

**DEMONSTRATED.** Tokenised `y += 1`, `z = a // 6`, `w = a % 2` — `'+='`, `'//'`, `'%'` each come back
as one `OP` token, none of which is a `_OP_SWAP` key.

**Where it costs something.**

- `stage2_prompt_builder/truncation.py:150` — `word_count += sent_words`. `+=` -> `=` (accumulator
  reset to the last sentence's length) is a textbook loop bug and means the budget never binds;
  `+=` -> `-=` means it binds immediately. Same at `formatting.py:119`.
- `stage2_prompt_builder/user_prompt.py:298` — `_chars_used += len(_raw_text)`, the attachment-text
  budget accumulator added by AUDIT 05.5 to bound attacker-controlled prompt input. `+=` -> `=` means
  the 60,000-char cap is applied per-file instead of to the SET, restoring the exact defect that
  audit fixed ("twenty 40k documents was ~800k characters of attacker-controlled prompt").
- `user_prompt.py:368,382,389,407` — `prompt += (...)`. `+=` -> `=` REPLACES the whole prompt with
  the returning-client block. Catastrophic and, notably, likely to survive a test that only asserts
  `"RETURNING CLIENT" in prompt`.
- `truncation.py:94,102` — `if m.start() > len(truncated) // 2:`. `//` -> `%` changes the
  "second-half sentence boundary" rule into nonsense.
- `user_prompt.py:95` — `max_words=max(1, max_desc_chars // 6)`.

**Remedy.** Add `"+=": "-=", "-=": "+=", "*=": "/=", "//": "%", "%": "//", "**": "*"` to `_OP_SWAP`,
and — separately, because it is the highest-value one — an **augmented-assignment-to-plain**
operator (`x += e` -> `x = e`) applied at statement level, which is where the accumulator bugs live.
Watch out: `+=` on a list is `extend`, `=` is a rebind, still a valid and interesting mutant.

**Value: medium-high**, driven almost entirely by `+=` -> `=` on the two budget accumulators.

---

## P2-8 — `max`/`min` cannot be swapped, and the clamps here document what happens when they invert

**DISPOSITION -- RESOLVED.** `max`/`min` and `any`/`all` swap. Measured: clamps in `user_prompt.py` and `llm_cache.py` gained mutants.

**Claim.** No operator renames a called builtin, so `max(a, b)` -> `min(a, b)` — one identifier, a
classic real bug — is not generatable. The constant operator fires on the literal *inside* the clamp
instead, which is usually the near-equivalent half.

**Where.**

- `stage2_prompt_builder/truncation.py:79` — `body_limit = max(0, max_words - signoff_words)`, with
  a five-line comment saying what inversion costs: "body_limit goes negative and slicing the body
  would take from the WRONG end (dropping the tail of the body instead of taking the first N)".
  DEMONSTRATED: the harness generates `0 -> 1` here, which is near-equivalent noise; the mutant that
  matches the documented failure is `max -> min`, which it cannot make.
- `models.py:964` — `conf = max(0.0, min(1.0, conf))`, the confidence clamp whose comment says it
  is also the ±Inf guard. Swapping either builtin turns the Inf guard off silently.
- `stage2_prompt_builder/word_count.py:67,68,79,80` — `max(50, ...)` and `max(target + 20, ...)`, the
  floor that keeps the returning-client reduction from collapsing the letter.

**Remedy.** A tiny **builtin-swap** operator over a fixed table: `max`<->`min`, `any`<->`all`,
`sorted(x)` -> `sorted(x, reverse=True)`, `str`<->`repr`. Match on a `Call` whose `func` is a bare
`Name` in the table (so a method or an attribute access is never touched). `any`/`all` has a real
site too: `prompt_safety.py:443` `return any(check_dangerous_content(segment) for segment in ...)` —
`any` -> `all` means a job with one dangerous field and one clean field is declared safe.

**Value: medium-high.** Four sites in three files, each one identifier wide, each with a documented
consequence.

---

## P3-9 — Slice bounds and direction are unmutable when the bound is a name

**DISPOSITION -- DEFERRED.** Slice bounds are unmutable when the bound is a name. The constant case is already covered by the numeric operator; the name case needs the same AST span work as P1-4 and is deferred with it.

**Claim.** The constant operator reaches `x[:3]` but not `x[:limit]`, and nothing reverses a slice or
moves a bound across the colon.

**Where.**

- `user_prompt.py:297` — `_raw_text = _raw_text[:_remaining] + "\n[truncated: ...]"`.
  `[:_remaining]` -> `[_remaining:]` keeps the *tail* of an over-budget attachment, which is where an
  attacker would put the payload. `_remaining` is a name; DEMONSTRATED, the mutant list for line 297
  contains only `+ -> -` and the emptied suffix string.
- `user_prompt.py:236` — `_attachments = _attachments[:_max_attachments]` (AUDIT 05.5's file-count
  cap). Same shape.
- `models.py:958` — `.encode(...)[:_MAX_COVER_LETTER_BYTES].decode(...)`, the byte-boundary cut whose
  comment explains at length why it must be a byte slice.
- `truncation.py:96,103` — `truncated[: match.start() + 1]`. Here the constant operator DOES fire
  (`1 -> 2`), which is the off-by-one; direction is still unreachable.
- `models.py:380` — `str(stats.get("memberSince") or "")[:4]  # just the year`. `4 -> 5` is
  generated and is a genuine off-by-one; `[4:]` is not.

**Remedy.** A **slice-direction** operator: for `x[:e]` emit `x[e:]` and vice versa; for `x[a:b]`
emit `x[b:a]`. Purely syntactic, always valid.

**Value: medium.** Three of the five sites are security-relevant truncations of attacker-controlled
text; the shape is not otherwise reachable when the bound is a name, which is the normal case.

---

## P3-10 — The exception TYPE and the swallow are unmutable

**DISPOSITION -- WON'T FIX.** The exception TYPE and the swallow stay unmutable. Widening a caught type produces a mutant that behaves identically unless the wider exception actually occurs, which makes it an equivalent mutant most of the time; narrowing it turns a handled path into a crash, which the crash-kill machinery already reports as a kill that proves nothing about assertions. Neither direction produces the signal the finding wants, which is 'does a test notice that this handler is here' -- and that question is better answered by a test that raises the exception deliberately.

**Claim.** `except (TypeError, ValueError):` cannot be narrowed to `except TypeError:`, and
`except X: <handler>` cannot become `except X: pass`. The statement-call operator turns a
*logging* handler into `pass`, which is close but not the same thing.

**Where.**

- `models.py:960-962` and `:967-969` — `except (TypeError, ValueError)` twice around the confidence
  parse. Narrowing to just `ValueError` means a `None` confidence propagates a `TypeError` out of
  `from_llm_json` instead of defaulting to 0.0.
- `models.py:945-947` — `except (TypeError, ValueError): _adaptive_bytes = _MAX_ADAPTIVE_STRATEGIES_BYTES + 1`.
  Note this one is *also* an equivalent-mutant source: the constant operator emits `+1 -> +2`, and
  both values are `> _MAX`, so the mutant is provably equivalent. See P3-12.
- `prompt_safety.py:148-149` — `except Exception as _metrics_err: logger.debug(...)`, with a `noqa`
  comment saying "metrics must never fail redaction". Narrowing to `except ImportError` reintroduces
  "a metrics failure breaks redaction", which is the invariant the comment names.
- `user_prompt.py:181-182` — `except Exception as e: logger.debug("Few-shot past-wins fetch failed
  (non-fatal): %s", e)` with `noqa: BLE001 — prompt enrichment must never break eval`.

**Remedy.** Two operators: (a) **handler-body deletion** — replace an `except` handler's body with
`pass` (distinct from deleting one call inside it); (b) **exception narrowing** — for a tuple of
types, drop the last element. Both are one-node AST edits.

**Value: medium.** Every one of these sites carries a hand-written comment asserting the very
invariant the mutant would break, which is the strongest available evidence that the codebase
considers this class real.

---

## P3-11 — Truthiness guards with no `not` and no comparison cannot be inverted

**DISPOSITION -- RESOLVED.** `if flag:` becomes `if not flag:`. A guard with no comparison and no `not` was the one shape with nothing to swap, so the most ordinary logic bug there is could not be expressed at all.

**Claim.** `if not x:` is mutable (drop the `not`) and `if a == b:` is mutable (`==` -> `!=`), but
`if x:` has no mutant at all, so a plain truthiness gate is never challenged.

**Where.** `user_prompt.py:141` (`if _exp_raw.strip():`), `:186` (`if client_info_raw:`), `:224`
(`if job.questions:`), `:233` (`if job.attachments:`), `:322` (`if _budget_events:`), `:374`
(`if returning_client_info:`), `:406` (`if style_hint:`), `truncation.py:62` (`if signoff:`).
DEMONSTRATED: the letter_guards mutant list shows mutants for every `if not ...` at `:128`, `:191`,
`:210`, `:243` and nothing for the bare-truthiness guards.

**Why it matters, modestly.** Inverting `if job.attachments:` produces an obviously-broken run that
almost any test kills, so most of these mutants would be low-information. The one that is not is
`user_prompt.py:322` `if _budget_events:` — inverting it emits the budget WARNING only when no budget
event occurred, which is silent, plausible, and defeats the log the AUDIT 05.5 comment says is "what
turns these policy bounds into measurable ones".

**Remedy.** Wrap a bare `if <expr>:` test in `not (...)`. Cheap, but expect a low information rate;
worth gating behind the same kind of measured-precision argument the module already applies to the
statement-call operator.

**Value: low-medium.** Listed because it is a real hole, not because it is a priority.

---

## P3-12 — Waste in the EXISTING operators: the "+1 on an opaque length" family, and what a safe filter looks like

**DISPOSITION -- WON'T FIX.** The opaque-length filter is not applied. The finding measures the family at nine mutants repo-wide, and the same round accepted exactly two of them into the baseline with reasoning -- a cost of two sentences, once. Against that, this harness's single most valuable finding to date was a constant off-by-one at a real cap, and any filter over 'lengths' is a filter over the neighbourhood of that finding. The report itself recommends a category label rather than suppression; a label that changes nothing about what runs is not worth the rule.

The brief asks whether a cheap rule can suppress the `token_hex(8) -> 9` / `hexdigest()[:16] -> 17`
family without losing the cap off-by-one that was today's best finding. My answer, with the counts:

**The family is small.** Grepped the whole consumer: `secrets.token_hex(8)` appears **6 times**
(`user_prompt.py:116,117,118,119,188`, `prompt_safety.py:389`) and `hexdigest()[:16]` **3 times**
(`run_registry.py:99,124`, `stage2_llm_eval.py:904`), plus `hashing.py:65` where the length is a
parameter and so not a constant at all. **Nine mutants**, against **129 numeric mutants** and **1174
mutants total** on the measured files. It is not the noise problem; it is a visible annoyance because
each one has to be individually accepted into the baseline.

**A safe rule exists, and it is narrower than "lengths".** Suppress `int -> int+1` when *all* of:
1. the literal is the sole argument to a call whose dotted name is in a small allowlist —
   `secrets.token_hex`, `secrets.token_bytes`, `secrets.token_urlsafe`, `os.urandom`; OR it is the
   `upper` of a slice applied directly to a `.hexdigest()` / `.hex()` call;
2. the literal does not also appear as an operand of a `Compare` anywhere in the file.

Condition (1) alone is already tight — it names entropy-sizing, where by construction no observable
boundary exists. Condition (2) is the guard that protects today's finding: a *cap* is a constant that
something is compared against (`user_prompt.py:260` `if len(_attachments) > _max_attachments`,
`:284` `if _remaining <= 0`, `:291` `if len(_raw_text) > _remaining`, `truncation.py:66,80,81`), and
no cap constant is ever the sole argument of `token_hex`. I could not construct a case in this
consumer where the rule loses a real off-by-one.

**Do NOT generalise it to "lengths with no boundary".** Two counterexamples from this codebase:
- `models.py:380` — `str(...)[:4]  # just the year`. A slice length, no comparison anywhere, and
  `4 -> 5` is a *real* defect (the member-since year gains a digit). It survives the allowlist rule
  because it is not a hexdigest slice. A broader "slice length" rule would kill it.
- `models.py:946` — `_adaptive_bytes = _MAX_ADAPTIVE_STRATEGIES_BYTES + 1`. This one genuinely IS
  equivalent (`+2` is also `> _MAX`), and it is `+1` on a *cap-adjacent* expression, so any
  cap-aware heuristic would keep it. It is the case that argues for reporting-with-a-category rather
  than hard suppression.

**Preferred remedy: category, not deletion.** The `Mutant.category` field and the
`noise:table-sample:` convention already exist. Emit these as `category="noise:opaque-length"` and
have `assert_no_new_surviving_mutant` accept them into the baseline automatically (still listed in
the report, sorted last) rather than dropping them from generation. That keeps the run count honest,
keeps a human able to disagree, and costs one accepted line instead of a judgement call each time —
which is the same trade the module already made for `_repr_coupled_lines` by leaving it opt-in.

**The larger waste is elsewhere.** DEMONSTRATED: emptied-strings are **662 of 1174 (56%)** mutants;
`prompt_safety.py` is 200/241 = **83%** emptied-strings. Two sub-families are cheap to fix:
- **Implicit string concatenation** produces one mutant per fragment for one decision.
  `letter_guards.py:146-153` is a single repair-hint message written as 8 adjacent literals -> 8
  mutants; `prompt_safety.py:96-102` is one injection regex as 5 fragments -> 5. Measured across the
  corpus: **12 concatenation groups, 43 member literals — collapsing each group to one mutant saves
  31 mutants** and, more importantly, makes each surviving one mean something.
- **Long prose literals** (>~120 chars) inside a single call — e.g. `letter_guards.py:146-153`,
  `user_prompt.py:331-335` — where "emptied" is asking "does any test assert this exact sentence?",
  a question the repo has already answered no to, and will answer no to forever.

Suggested: treat an implicit-concatenation group as ONE candidate (empty the whole group), and give
prose literals over a length threshold `category="noise:prose"`. Neither loses a boundary.

---

## Already covered — do not re-propose

Verified present and firing on the real code, from the letter_guards / user_prompt mutant dumps:

- `and` <-> `or` (`letter_guards.py:132`, `user_prompt.py:281`, `:353`, `:355`, `:357`)
- `not` removal (`letter_guards.py:128,191,194,210,243`, `user_prompt.py:367,397`)
- `is` -> `is not` and `is not` -> `is` (`user_prompt.py:209,367,397`)
- `in` <-> `not in` (`letter_guards.py:132,212,247`, `prompt_safety.py:339` `"<" not in probe`)
- `True` <-> `False` (`letter_guards.py:211`)
- Inverted if/else via `==` -> `!=` where the branch is keyed on a comparison
  (`user_prompt.py:281,301,317,381`)
- `.get(k)` key emptying, and `.get(k, default)` key emptying (`user_prompt.py:257,258,279,280,320`)
- Statement-level `await`ed and plain call deletion (`user_prompt.py:261,288,289,292,300,311,318,321,323`)
- Float thresholds `+0.1` (`word_count.py:67,68,77,79,80`)

Not applicable to this consumer: `asyncio.gather` vs sequential — I found no `gather` in the four
briefed files or their package; `str()` vs `repr()` is folded into P2-8's builtin-swap table, and
`!r` removal is an f-string conversion, so it is blocked by P0-1 and unlocks with it
(`user_prompt.py:288` `f"{name!r} omitted entirely"`, `:292`, `models.py:985`
`f"...keys were {sorted(data.keys())!r}"`).

---

## Ranked remedy list

| # | operator to add | sites found | effort | value |
|---|---|---|---|---|
| P0-1 | mutate `FSTRING_MIDDLE` | whole prompt template + 5 fence sites | low | very high |
| P1-2 | regex perturbation (quantifier / `\b` / flag / branch) | ~20 patterns in 2 files | medium | very high |
| P1-3 | fix `_container_members`: `AnnAssign` + span-based suppression | 2 tables | low | high |
| P1-4 | adjacent-argument and unpack-target transposition | 5 sites | low | high |
| P1-5 | `continue` <-> `break` | 3 sites | very low | high |
| P2-6 | sibling string substitution (keys, codec names, escape chars) | ~8 sites | medium | med-high |
| P2-7 | `+=`/`//`/`%`/`**` in `_OP_SWAP`, plus `x += e` -> `x = e` | 8 sites | very low | med-high |
| P2-8 | builtin swap `max`/`min`/`any`/`all`/`str`/`repr` | 6 sites | low | med-high |
| P3-9 | slice direction `[:n]` <-> `[n:]` | 5 sites | very low | medium |
| P3-10 | handler body -> `pass`; exception-tuple narrowing | 4 sites | low | medium |
| P3-11 | invert bare `if <expr>:` | ~8 sites, low info rate | very low | low-med |
| P3-12 | `noise:opaque-length` category; collapse concat groups; `noise:prose` | 9 + 31 mutants | low | medium |

Any of these changes must bump `HARNESS_VERSION` (`mutation_teeth.py:117`, currently `"2"`), which
the module already documents as the reason a stale survivor list is not silently reused.

## What I verified, and what I did not

**Verified by execution** (Python 3.14.3, `generate_mutants` on the real consumer files): the
per-file mutant counts and category breakdown; that `generate_mutants` returns `sampled_containers ==
{}` on all four briefed files; that `_container_members` skips `AnnAssign`; that the `user_prompt.py`
prompt-template f-string yields no literal-text mutants; that `letter_guards.py`'s 14 regexes each
yield exactly one `-> ""` mutant; that `+=`, `//`, `%` tokenise as single OP tokens outside
`_OP_SWAP`; that `FSTRING_MIDDLE` spans are cleanly addressable; the concatenation-group count.

**Not verified**: I did not run the consumer's test suite against any mutant, so every "a test would /
would not catch this" is a READ judgement from the test names and the doctests in the source, not a
measured survival. The precision estimates for the proposed operators are therefore projections; the
module's own precedent (the measured 6-of-8 for statement-call deletion) is the right bar, and each
new operator should be measured that way before it is kept.
