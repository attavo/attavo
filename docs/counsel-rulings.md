# Counsel and the conversation: rulings of 2026-09-25

Observed on #219 (R3's own build): attempt 1 halted after 3,510 s as `non-question`; `counsel-01` read it, verdict `answer`, text "Continue the build from the existing working tree"; under R2 the product filed an issue and waited for the operator. Under R3 counsel's `answer` is the next attempt's steer.

## Troubleshoot, measured

One `attavo counsel 219 --ask "why did this build stop?"` in a scratch checkout: 73 s, three model turns. Turn one answered with no citations; the adapter's citation guard sent it back; it read the record, the last 30 KB of the 4.8 MB build log, searched for the brief and found nothing (the corpus was not in the repository); turn three answered with citations. It wrote `counsel-02.json` into the scratch checkout, which was discarded.

## Ruled

1. A question to counsel is a follow-up to a stop counsel has already read. `attavo counsel N --ask` is handed the prior counsel records for the item as its first input (verdict, text, citations), never re-derives a class a record states, and writes its answer as the next `counsel-NN.json` with `answers` naming the counsel record it follows beside `record`. The screen commits and pushes it like any counsel record. The answer is a record: an intervention in the attestation and precedent.
2. The panel's first message is the newest counsel record read from the branch (no invocation); `--ask` runs only for a question the operator types.
3. Resuming a vendor's own thread is an adapter-internal optimisation, recorded in the adapter's report when used, never part of the contract; the records are the mechanism that carries context between invocations.
4. For a level that is the operator's (budget spent, two identical failures, counsel says `person`) the panel opens with the exhaustion report and ends in one of three drafted acts, each a record: a ruling (R4), a spec amendment (moves the approval), a product or tenant defect (counsel's `patch`).
5. Counsel is handed the stop record, the signature and the stage's last events rather than the whole log, unless the question is about the log.
6. Counsel's corpus, the text it reads before it answers (method rules, briefs, rulings), lives in the repository under `docs/`, named by `[counsel].reads`; a manifest naming a path that does not exist is refused.

## The conversation and its records

The exchange is split from its outcome. A turn in the conversation is working material and lives in the product's datastore with the item; a record is written only for an outcome the attestation cares about: counsel's verdict on a stop (answer, patch, person); a follow-up whose answer changes what the next attempt is told; or an operator turning a turn into an act (a ruling, a spec amendment, an issue). The record carries the question that produced it. A question that belongs to Plan is answered and routed there; in Execute the only outcomes are those above, so a tangent produces nothing in git however long it runs.
