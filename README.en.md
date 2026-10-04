# Sensitive Data Egress Gate — Minimal Reference Implementation

**Current: one credential → potentially ALL**

**Target: one credential → bounded**

**This repository is a reference implementation, not a production security product.**

Commercial design and sample deliverable: [Sensitive Data Egress Gate](https://shin4141.github.io/sensitive-data-egress-gate/?lang=en).

Run a small CLI to see the design work with fictional records. Normal access is limited to **500 records/request** and **2,000 over a rolling 24-hour window**. Larger access requires independent approval. Full or high-impact access requires a separate escalation chain: Escalation Multisig Gate.

[日本語](README.md)

## Run it

Use Python **3.12 or later** and Git. No third-party packages, credentials, pip install, or runtime network access are needed.
Clone the public repository, then run these commands from its root:

```sh
git clone https://github.com/shin4141/sensitive-data-egress-gate-reference.git
cd sensitive-data-egress-gate-reference
python3 -m sdeg
python3 -m unittest discover -s tests -v
```

The short CLI shows per-request and rolling limits, independent approval, destination invalidation, blocked Owner-only access, and the successful chain:

```text
Request -> Notify -> WAIT -> Independent Seat -> Owner Seat -> Release
ALL / complete chain            PASS | released=12,000
```

See [the complete deterministic output](examples/cli-output.txt). WAIT advances a simulated clock. Notifications remain in a mock outbox.
Successful extraction returns actual fictional-record arrays; blocked extraction returns no records.

## Fixed example rules

| Operation or condition | Result |
| --- | --- |
| Normal access: 500 / 501 records | PASS / BLOCK; use an upper gate |
| Rolling 24h: 2,000 / 2,001 records | PASS / INDEPENDENT_APPROVAL_REQUIRED |
| Large extraction | Separate independent approval required |
| Applicant approves own request; Owner acts as independent approver | BLOCK |
| Destination, scope, volume, requester, or release class changes | Invalidate prior approvals; new destinations remain blocked |
| ALL or high-impact access through normal/large paths | BLOCK; use escalation |
| Owner alone, no notification, WAIT incomplete, missing intermediate/final approval | BLOCK |
| Valid request, all-Seat notification, WAIT, independent approval, Owner approval | PASS |
| Expired approval, reuse for another request, or replay of a release | BLOCK |

Rolling totals aggregate one requester's access across CSV/API/export paths, scopes, and destinations, including repeated records.
The window is `(now−24h, now]`. At **10,000 records** in that window, or **10,000 distinct fictional records** acquired during the process's lifetime, escalation is required. Splitting extraction across days does not bypass the latter condition.

ALL means **12,000 records in one fictional scope**. WAIT is **60 seconds** and approval expires **300 seconds** after a request or revision.
Approvals are bound to the request, scope, volume, destination, requester, release class, path, and offset. Changes reset approvals, notifications, and waiting.
This example uses one independent approver and one final Owner. These counts and values are illustrative, not recommendations or a universal architecture.

## Inspect the code

- [Gate model](sdeg/gate.py): bounded extraction and escalation state.
- [CLI](sdeg/demo.py): Current → Target and the required scenarios.
- [Tests](tests/test_gates.py): boundaries, ordering, invalidation, expiry, replay, and atomic allocation.
- [CI](.github/workflows/ci.yml): tests and CLI on Python 3.12 / 3.14, read-only repository permission.

## Assessment boundary

Caller IDs and roles are trusted model inputs. Real authentication, cryptographic signatures, notification delivery, persistence, and external data transport are outside this reference.
An attacker who can freely modify the process's code or memory is outside the model. State and audit events disappear on restart. Allocation is serialized within one process; this is not a production or distributed-system guarantee.
Data consists only of generated `FICTIONAL/...` identifiers, with no real company or personal data.

## Show the code; retain the design judgment as the service

This repository enforces fixed fictional conditions. It does not include real-company threshold selection, customer-specific Seat design decisions, the procedure for deciding which controls to remove, or commercial exception design.
Actual values, responsibilities, and assessment scope depend on each company's requirements.
See [Sensitive Data Egress Gate](https://shin4141.github.io/sensitive-data-egress-gate/).

License: [MIT](LICENSE)
