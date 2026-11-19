# A bounded window for final validation

**Proposed decision for #2682, awaiting maintainer review.** Allow the
designated fleet coordinator to defer other ordinary merges while one
nominated PR finishes validation against an exact, green `main`. A grant
would reserve time for one base and one PR head. The publisher App, required
checks, route-specific approvals and the final ancestry check still decide
whether that head can merge.

In #2675 the owner had already synchronized to the #2680 merge when #2681
advanced the base again during final validation. In #2697 the owner had
finished 18,831 backend cases, frontend and browser tests, and local and hosted
review when #2696 changed backend code and the testing guide. The owner
stopped before finalization. Both PRs eventually merged. These episodes
establish the cost of interruption, without estimating how long every future
validation will take. [First incident](https://github.com/bioedca/Yeliztli/issues/2682)
and [second incident](https://github.com/bioedca/Yeliztli/issues/2682#issuecomment-6966103234).

## Allocation and expiry

| Step | Proposed coordinator and owner actions |
| --- | --- |
| Nominate | An active issue owner names the open PR, frozen head, freshly verified green base, remaining gates and estimated elapsed time. Implementation and ordinary focused checks should already be complete. The coordinator admits one outstanding nomination per PR and assigns it a sequence number. |
| Grant | After the previous merge and all its covering main checks finish green, choose the oldest eligible nomination. The coordinator records one grant with a monotonically increasing generation, exact SHAs, issue claim ID and UTC expiry. The owner acknowledges that generation within five minutes. A missed acknowledgment releases it. |
| Validate | Recheck the grant and sole live issue claim before starting or resuming work. Keep the existing claim heartbeats and record material progress. Other owners may continue permitted independent work. They defer ordinary merges and any premature hosted review that a base change would invalidate. |
| Expire | Use the owner's estimated duration up to a proposed **four-hour maximum**, measured from grant. Heartbeats do not extend it. A head change, base change or lost issue ownership invalidates the grant immediately. A PR consuming its window without finishing may rejoin at the back after it becomes eligible again. |
| Merge and release | Immediately before a merge request, the coordinator and owner confirm the same live generation, open PR, head, green base, ancestry, exact checks, resolved threads and dedicated publisher success. V2 also requires the human gates and explicit authorization. Record the merge as in flight until its exact main checks finish green, even if the window's deadline passes meanwhile. |

The four-hour ceiling is a proposed scheduling limit. The incidents do not
establish a completion percentile. If work reliably exceeds the ceiling, use
measured durations to revisit the limit or divide genuinely independent work.
An incomplete run remains incomplete.

An unserved nominee keeps its sequence number across a base refresh. When
main turns green, give the oldest waiting owner up to 15 minutes to confirm
its refreshed base and frozen head. Then grant to the oldest eligible
nominee. Offer this grace once per nominee per base. An owner needing
longer keeps its place for a later selection while ready work proceeds.

Only the designated coordinator can grant, revoke or sequence a window.
It must persist each decision before acknowledging it, serialize changes,
and fence a retired coordinator generation before transferring allocation.
Issue comments and the Project may mirror a grant. Concurrent comments,
reactions or client clocks cannot elect an allocator or authorize a merge.
Without a known coordinator and reconciled state, suspend new grants. A
maintainer can explicitly assign a successor after fencing the prior writer.

## Preemption, owner loss and recovery

- The coordinator may preempt for an urgent safety or release fix designated
  by the maintainer. Record a sanitized reason and revocation generation.
  The outgoing owner acknowledges the fence before another merge proceeds.
  An already-sent merge request with an unknown outcome must be reconciled on
  GitHub first. The urgent PR passes the same gates.
- On an orderly handoff, fence the predecessor, checkpoint exact inputs,
  completed checks, live work and the next step. Use the normal named-successor
  issue election. A new grant to the confirmed successor expires by the
  original deadline. After expiry, rejoin at the back of the queue.
  Each continuing job needs an acknowledged monitoring owner.
  Otherwise its owner cancels that exact job and confirms its terminal state.
  Handle operational details privately under the existing resource rules.
- On an unresponsive owner, expire or revoke the grant and quarantine any
  uncertain jobs or writes. Reconcile the PR, main ref and any outstanding
  merge request. A designated operator must take over monitoring or confirm
  exact cancellation before reusing resources. Recovery cannot assume a job
  stopped when the clock expired. Never duplicate an unverified job.
- If another writer advances main despite the window, fetch and wait for the
  new exact main checks. Synchronize with a normal merge, rerun affected
  checks, complete final local review and invoke exactly one selected hosted
  provider on the new head. Only the existing independent-maintainer exception
  can accept documented test/input independence. It cannot transfer provider
  review, App authority or a claimed execution to a different SHA.

## Scheduling examples

1. A long PR receives a 120-minute window. Three short PRs become eligible
   at minutes 10, 20 and 30. At minute 85 the long PR passes every gate and
   merges. The coordinator waits for its merge SHA to pass Tier-2 and the
   other main checks. The first short PR then has first opportunity once it
   has synchronized to that green base. Newly arriving PRs cannot cut ahead
   of eligible nominees. Each later merge checks the new base again.
2. If the long PR instead reaches minute 120 unfinished, the grant expires.
   After fencing and reconciling any uncertain merge, the oldest eligible
   short PR gets the next window. The long PR can nominate again after the
   short PRs. If its owner disappears, use the recovery procedure above.
3. If an urgent fix arrives at minute 40, the coordinator revokes the grant,
   reconciles it and schedules the urgent fix. A preempted owner retains its
   original waiting position when eligible again, but must validate on the
   new base. Repeated urgent interventions or an unfenced writer can still
   prevent progress. Escalate that condition to the maintainer.

Optimistic synchronization remains the simplest choice for a quiet queue.
Under steady arrivals it can repeatedly invalidate the longest PR. An
independent-maintainer input assessment can save genuinely unaffected test
work, but requires evidence and does not reserve any time. The bounded grant
trades a predictable delay for waiting PRs against fewer discarded runs.

If adopted, update both development contracts and test the coordinator's
sequence, expiry, recovery and one-merge-in-flight rules before enabling grants.
Use public issue/PR IDs, public SHAs, the public claim ID, generation, UTC
deadline and sanitized state in shared records. Keep job IDs, host details,
private paths, credentials and nonpublic data out of those records. This
proposal introduces no scheduling service or GitHub merge queue.
