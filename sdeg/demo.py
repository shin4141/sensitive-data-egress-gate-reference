"""A short, deterministic demo with real synthetic-record release decisions."""

from .gate import Gate, ManualClock, ReleaseClass, SEATS, Status, WAIT_SECONDS, synthetic_records


def current_state_export(credential: str) -> tuple[str, ...]:
    """Deliberately unbounded contrast model, using the same mock Seat IDs."""
    if credential not in SEATS:
        raise ValueError("Unknown fictional credential")
    return synthetic_records()


def run() -> None:
    print(f"Current: 1 credential -> ALL ({len(current_state_export('operator')):,} fictional records)")
    print("Target:  1 credential -> bounded (500/request; 2,000/rolling 24h)")
    print("Fixed sample only. WAIT uses a simulated clock; no data is sent.\n")

    def show(label: str, result, expected: Status, count: int = 0) -> None:
        if result.status != expected or result.count != count:
            raise AssertionError((label, result, expected, count))
        suffix = f" | released={result.count:,}" if result.count else ""
        print(f"{label:<31} {result.status.value}{suffix}")

    def release(gate: Gate, amount: int | str, **options):
        request = gate.request("operator", amount, **options)
        return gate.release(request, "operator")

    show("Normal / 500", release(Gate(), 500), Status.PASS, 500)
    show("Normal / 501", release(Gate(), 501), Status.BLOCK)
    gate = Gate()
    acquired = set()
    for page, route in enumerate(("csv", "api", "export", "csv")):
        result = release(gate, 500, path=route, offset=page * 500)
        if result.status != Status.PASS:
            raise AssertionError(result)
        acquired.update(result.records)
    if len(acquired) != 2_000:
        raise AssertionError("The demonstration must release 2,000 distinct fictional records")
    print(f"{'Rolling 24h / 2,000':<31} PASS | cumulative={gate.rolling_total('operator'):,}")
    show("Rolling 24h / 2,001", release(gate, 1, offset=2_000), Status.INDEPENDENT_APPROVAL_REQUIRED)

    gate = Gate()
    request = gate.request("operator", 501, release_class=ReleaseClass.LARGE)
    show("Large / no independent Seat", gate.release(request, "operator"), Status.INDEPENDENT_APPROVAL_REQUIRED)
    gate.approve_independent(request, "reviewer")
    show("Large / independent Seat", gate.release(request, "operator"), Status.PASS, 501)

    gate = Gate()
    request = gate.request("operator", 501, release_class=ReleaseClass.LARGE)
    gate.approve_independent(request, "reviewer")
    changed = gate.revise(request, "operator", destination="fictional://new-vault")
    if changed.reason != "APPROVAL_INVALIDATED":
        raise AssertionError(changed)
    print(f"{'New destination':<31} APPROVAL_INVALIDATED")
    show("New destination / release", gate.release(request, "operator"), Status.BLOCK)
    show("ALL / normal path", release(Gate(), "ALL"), Status.BLOCK)

    gate = Gate()
    request = gate.request("owner", "ALL", release_class=ReleaseClass.ESCALATION)
    gate.approve_owner(request, "owner")
    show("ALL / Owner alone", gate.release(request, "owner"), Status.BLOCK)

    clock = ManualClock()
    gate = Gate(clock)
    request = gate.request("operator", "ALL", release_class=ReleaseClass.ESCALATION)
    gate.notify(request, "operator")
    show("ALL / before WAIT completes", gate.release(request, "operator"), Status.BLOCK)
    print("\nRequest -> Notify -> WAIT -> Independent Seat -> Owner Seat -> Release")
    clock.advance(WAIT_SECONDS)
    for result in (gate.approve_independent(request, "reviewer"), gate.approve_owner(request, "owner")):
        if result.status != Status.PASS:
            raise AssertionError(result)
    show("ALL / complete chain", gate.release(request, "operator"), Status.PASS, 12_000)


if __name__ == "__main__":
    run()
