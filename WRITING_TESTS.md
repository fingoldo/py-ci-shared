# Writing tests that mutation testing has nothing to catch

This is a convention for every project that consumes `py-ci-shared`, not for one of them. It is
derived from measurement rather than from taste: a mutation sweep of a 3,676-test suite in
`realtime_applications` produced 1,014 surviving mutants, and every survivor that named a real gap
fell into one of five shapes. Each shape has a corresponding habit, below. Written the other way
round, a sweep finds nothing to report — which is the point, because a sweep costs hours and these
habits cost nothing.

The five come with a sixth about mocks, and a limit that says when to stop.

---

## 1. Assert the effect, not the answer

If a function exists for something it DOES elsewhere — a row written, a transaction committed, a
lock taken, a file replaced — assert on the collaborator. A mock returns its canned value whether or
not you called it, so a test that checks the return value cannot tell the statement executing from
the statement being deleted.

```python
# Cannot fail if `cur.execute(...)` is deleted: the mock's fetchall answers regardless.
def test_it_returns_the_rows(conn):
    assert fetch_active(conn) == [{"fl_cid": "~01a"}]

# Fails the moment the statement goes.
def test_it_asks_the_database_for_active_rows(conn):
    fetch_active(conn)
    sql = cursor_of(conn).execute.call_args.args[0]
    assert "WHERE active = true" in sql
```

Measured: eight SELECT/INSERT calls, four `conn.commit()` and two `conn.rollback()` were deletable
across two modules with the suite staying green. Two consequences worth stating, because they make
the habit worth more than tidiness: DDL inside an uncommitted transaction never happened, and a
failed batch that is not rolled back poisons the connection, so every later statement fails with
`current transaction is aborted`.

## 2. Stand ON the boundary, not near it

For every comparison, write one case exactly at the threshold and one exactly one step off. Values
chosen for convenience — `0.0`, `9e9`, `100.0` against a threshold of 1 — exercise the branch and
pin nothing, so `>=` can weaken to `>` and `<` widen to `<=` unnoticed.

```python
# Three branch tests, all far from the edges. Both comparisons remain free to move.
assert decide_poll({"next_full_sweep": 9e9, "skip_until": 0.0}, 100.0) == "incremental"

# One test at the edge pins the comparison itself.
assert decide_poll({"next_full_sweep": 100.0, "skip_until": 0.0}, 100.0) == "full_sweep"
```

Measured: a function extracted specifically so this would be testable, carrying three tests — one
per branch — with both of its boundaries unpinned. **Branch coverage is not boundary coverage**, and
coverage tooling reports the first while saying nothing about the second.

## 3. Use at least two of anything

A loop driven with one item cannot distinguish `continue` from `break`, `all` from `any`, or a sort
from its absence. A single-element collection hides ordering, truncation and short-circuiting.

```python
# One freelancer: `continue` and `break` behave identically.
# Two, where the first is skipped: only `continue` reaches the second.
def test_a_freelancer_without_a_profile_does_not_stop_the_sweep(conn):
    rows_are([("~01a",), ("~01b",)])
    compute(conn)
    assert both_were_queried("~01a", "~01b")
```

Measured: `continue` becoming `break` survived a full suite. Its consequence is silent partial data
loss — every item after the first skip goes unprocessed, with no exception, no log line and no
metric, so the run is indistinguishable from a complete one.

## 4. Assert counts as counts

When the behaviour is "N times", assert N. "It was true the first time" passes under every mutation
that shifts the total: a changed default, a counter starting at 1, a step of 2, a boundary off by
one.

```python
# Passes with the default at 20 or 21, the counter starting at 0 or 1, the step 1 or 2.
assert throttle.should_log_throttled() is True

# One assertion pins all four.
assert sum(throttle.should_log_throttled() for _ in range(allowance + 3)) == allowance
```

Measured: a thirty-three-line class whose whole contract is a count had five of its seven mutants
survive. One of them, `+=` becoming `=`, pins the counter at 1 forever — so the throttle stops
throttling and fills the log with the line it exists to limit.

## 5. Exercise both sides of every guard

A test that only takes the true branch cannot see `if x` inverted to `if not x`. Where the false
branch is "do nothing", assert that nothing happened — `mock.assert_not_called()` is a real
assertion.

---

## The habit about mocks

**A mock must be no weaker than the collaborator it stands for.** If the real object is used as a
context manager, the mock must be one; if the real call takes five fields, the fixture must supply
five. A loose `MagicMock()` passes where the real thing would raise, and then the test is green for
a reason unrelated to the code.

```python
# psycopg2's cursor() is a context manager; a bare MagicMock is not.
connection.cursor.return_value.__enter__.return_value = cursor
```

---

## Where to stop

Not every surviving mutant is a gap, and chasing the rest is waste. In the sweep this came from,
**363 of 1,014 survivors were single entries in data tables** — one word emptied out of a stop-word
list of several hundred — and another 151 were deleted log lines. Those are equivalent or
near-equivalent mutants: the behaviour genuinely does not change, or changes in a way no test should
be asserting.

Rank survivors by what the mutation did before spending time on them:

| kind | worth a test |
|---|---|
| a state-changing call deleted (`execute`, `commit`, `rollback`, a lock, a write) | yes |
| control flow inverted (a guard, `continue`/`break`, `and`/`or`, a comparison) | yes |
| a value that determines a count or a threshold | yes |
| a collection mutated (`append`, `update`, `add`) | usually |
| a log line deleted, or a message string emptied | rarely |
| one entry of a data table changed | no |

And the standing rule that outranks all of the above: **a test written for a defect is not evidence
until it has been run against that defect.** Make the change, watch the test fail, put it back. Four
tests in one audit round here were green against the very defect they named.

---

## The cheap half, for the classes that allow it

Shapes 1 and the mock habit are checkable statically, in seconds, by
`py_ci_shared.effect_assertion_parity`: for every module performing an effect, does at least one of
the tests that IMPORT it inspect that effect? Its ranking of the worst files independently
reproduced the sweep's own, which is the reason to trust it as a proxy.

Shapes 2, 3 and 4 cannot be checked that way. Whether a test would notice a value moving by one step
is a question about behaviour, and answering it means running the mutant. That is what mutation
testing is for, and why these habits are worth having in advance: they are free at writing time and
expensive to retrofit.
