"""Fixed sample rules. Caller identities and time are trusted model inputs."""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from threading import RLock
from types import MappingProxyType

# Illustrative enforcement values, not a business-requirements decision engine.
PER_REQUEST = 500
ROLLING_LIMIT = 2_000
WINDOW_SECONDS = 24 * 60 * 60
ESCALATION_AT = 10_000
WAIT_SECONDS = 60
APPROVAL_TTL_SECONDS = 300
TOTAL_RECORDS = 12_000
SCOPES = ("members", "documents")
PATHS = ("csv", "api", "export")
APPROVED_DESTINATIONS = frozenset(("fictional://approved-vault",))
DEFAULT_DESTINATION = "fictional://approved-vault"
SEATS = MappingProxyType({
    "operator": "operator", "operator-b": "operator",
    "reviewer": "independent", "reviewer-b": "independent",
    "owner": "owner", "owner-b": "owner",
})


class Status(StrEnum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    INDEPENDENT_APPROVAL_REQUIRED = "INDEPENDENT_APPROVAL_REQUIRED"


class ReleaseClass(StrEnum):
    NORMAL = "normal"
    LARGE = "large"
    ESCALATION = "escalation"


@dataclass(frozen=True)
class Result:
    status: Status
    reason: str
    records: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.records)


class ManualClock:
    """Deterministic clock: WAIT is demonstrated without sleeping."""

    def __init__(self) -> None:
        self._now = 0

    @property
    def now(self) -> int:
        return self._now

    def advance(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 0:
            raise ValueError("Clock advances must be nonnegative integer seconds")
        self._now += seconds


@dataclass(frozen=True)
class Spec:
    requester: str
    volume: int
    scope: str
    destination: str
    release_class: ReleaseClass
    path: str
    offset: int


@dataclass
class _Request:
    spec: Spec
    expires_at: int
    notified_at: int | None = None
    recipients: frozenset[str] = frozenset()
    independent: set[str] = field(default_factory=set)
    owners: set[str] = field(default_factory=set)
    released: bool = False


def synthetic_records(scope: str = "members") -> tuple[str, ...]:
    if scope not in SCOPES:
        raise ValueError("Unknown fictional scope")
    return tuple(f"FICTIONAL/{scope}/{n:05d}" for n in range(1, TOTAL_RECORDS + 1))


class Gate:
    """One process, trusted Seat registry, in-memory ledger, no external I/O."""

    def __init__(self, clock: ManualClock | None = None) -> None:
        self.clock = clock if clock is not None else ManualClock()
        self._requests: dict[str, _Request] = {}
        self._ledger: list[tuple[int, str, int]] = []
        self._acquired: dict[str, set[str]] = {}
        self._data = {scope: synthetic_records(scope) for scope in SCOPES}
        self._lock = RLock()
        self._events: list[tuple[int, str, str, str, str]] = []

    @property
    def events(self) -> tuple[tuple[int, str, str, str, str], ...]:
        return tuple(self._events)

    def _result(self, request_id: str, actor: str, action: str, status: Status,
                reason: str, records: tuple[str, ...] = ()) -> Result:
        self._events.append((self.clock.now, request_id, actor, action, reason))
        return Result(status, reason, records)

    @staticmethod
    def _validate(spec: Spec) -> None:
        if spec.requester not in SEATS:
            raise ValueError("Unknown fictional Seat")
        if type(spec.volume) is not int or not 1 <= spec.volume <= TOTAL_RECORDS:
            raise ValueError("Volume must be an integer between 1 and the fictional scope size")
        if type(spec.offset) is not int or spec.offset < 0 or spec.offset + spec.volume > TOTAL_RECORDS:
            raise ValueError("Requested range must fit the fictional scope")
        if spec.scope not in SCOPES or spec.path not in PATHS:
            raise ValueError("Unknown fictional scope or path")
        if not isinstance(spec.release_class, ReleaseClass):
            raise ValueError("Unknown release class")
        if not isinstance(spec.destination, str) or not spec.destination:
            raise ValueError("A destination is required")

    def request(self, requester: str, volume: int | str, *, scope: str = "members",
                destination: str = DEFAULT_DESTINATION,
                release_class: ReleaseClass = ReleaseClass.NORMAL,
                path: str = "csv", offset: int = 0) -> str:
        with self._lock:
            count = TOTAL_RECORDS if volume == "ALL" else volume
            spec = Spec(requester, count, scope, destination, release_class, path, offset)
            self._validate(spec)
            request_id = f"request-{len(self._requests) + 1}"
            self._requests[request_id] = _Request(spec, self.clock.now + APPROVAL_TTL_SECONDS)
            self._result(request_id, requester, "REQUEST", Status.PASS, "REQUEST_BOUND")
            return request_id

    def _active(self, request: _Request) -> str | None:
        if request.released:
            return "ALREADY_RELEASED"
        if self.clock.now >= request.expires_at:
            return "APPROVAL_EXPIRED"
        return None

    def _chain_ready(self, request: _Request) -> str | None:
        if SEATS[request.spec.requester] != "operator":
            return "LOWER_SEAT_REQUEST_REQUIRED"
        if request.notified_at is None or request.recipients != frozenset(SEATS):
            return "NOTIFY_REQUIRED"
        if self.clock.now < request.notified_at + WAIT_SECONDS:
            return "WAIT_NOT_COMPLETE"
        return None

    def notify(self, request_id: str, actor: str) -> Result:
        with self._lock:
            request = self._requests[request_id]
            reason = self._active(request)
            if not reason and (actor != request.spec.requester or
                               SEATS[request.spec.requester] != "operator"):
                reason = "LOWER_SEAT_REQUEST_REQUIRED"
            if not reason and request.spec.release_class != ReleaseClass.ESCALATION:
                reason = "ESCALATION_ONLY"
            if not reason and request.notified_at is not None:
                reason = "ALREADY_NOTIFIED"
            if reason:
                return self._result(request_id, actor, "NOTIFY", Status.BLOCK, reason)
            request.notified_at = self.clock.now
            request.recipients = frozenset(SEATS)  # Mock outbox only; no real notification.
            return self._result(request_id, actor, "NOTIFY", Status.PASS, "ALL_REGISTERED_SEATS_NOTIFIED")

    def notifications(self, request_id: str) -> frozenset[str]:
        return self._requests[request_id].recipients

    def approve_independent(self, request_id: str, actor: str) -> Result:
        with self._lock:
            request = self._requests[request_id]
            reason = self._active(request)
            if not reason and actor == request.spec.requester:
                reason = "APPLICANT_IS_NOT_INDEPENDENT"
            if not reason and SEATS.get(actor) != "independent":
                reason = "INDEPENDENT_SEAT_REQUIRED"
            if not reason and request.spec.release_class == ReleaseClass.NORMAL:
                reason = "USE_UPPER_GATE"
            if not reason and request.spec.release_class == ReleaseClass.ESCALATION:
                reason = self._chain_ready(request)
            if reason:
                return self._result(request_id, actor, "INDEPENDENT", Status.BLOCK, reason)
            request.independent.add(actor)
            return self._result(request_id, actor, "INDEPENDENT", Status.PASS, "INDEPENDENT_APPROVAL_BOUND")

    def approve_owner(self, request_id: str, actor: str) -> Result:
        with self._lock:
            request = self._requests[request_id]
            reason = self._active(request)
            if not reason and (actor == request.spec.requester or SEATS.get(actor) != "owner"):
                reason = "SEPARATE_OWNER_SEAT_REQUIRED"
            if not reason and request.spec.release_class != ReleaseClass.ESCALATION:
                reason = "ESCALATION_ONLY"
            if not reason:
                reason = self._chain_ready(request)
            if not reason and not request.independent:
                reason = "INDEPENDENT_APPROVAL_REQUIRED"
            if reason:
                return self._result(request_id, actor, "OWNER", Status.BLOCK, reason)
            request.owners.add(actor)
            return self._result(request_id, actor, "OWNER", Status.PASS, "FINAL_OWNER_APPROVAL_BOUND")

    def revise(self, request_id: str, actor: str, **changes: object) -> Result:
        """Every changed field starts a new approval/notification/wait lifecycle."""
        with self._lock:
            request = self._requests[request_id]
            reason = self._active(request)
            if not reason and actor != request.spec.requester:
                reason = "REQUESTER_ONLY"
            if reason:
                return self._result(request_id, actor, "REVISE", Status.BLOCK, reason)
            spec = replace(request.spec, **changes)
            self._validate(spec)
            if spec == request.spec:
                return self._result(request_id, actor, "REVISE", Status.PASS, "UNCHANGED")
            self._requests[request_id] = _Request(spec, self.clock.now + APPROVAL_TTL_SECONDS)
            return self._result(request_id, actor, "REVISE", Status.PASS, "APPROVAL_INVALIDATED")

    def rolling_total(self, requester: str) -> int:
        with self._lock:
            cutoff = self.clock.now - WINDOW_SECONDS
            # One principal's total spans every sample path, scope and destination.
            self._ledger = [entry for entry in self._ledger if entry[0] > cutoff]
            return sum(count for _, seat, count in self._ledger if seat == requester)

    def release(self, request_id: str, actor: str) -> Result:
        # Check, record allocation, and consume the request atomically.
        with self._lock:
            request = self._requests[request_id]
            spec = request.spec
            reason = self._active(request)
            if not reason and actor != spec.requester:
                reason = "REQUESTER_ONLY"
            if not reason and spec.destination not in APPROVED_DESTINATIONS:
                reason = "DESTINATION_NOT_APPROVED"
            records = self._data[spec.scope][spec.offset:spec.offset + spec.volume]
            total = self.rolling_total(spec.requester) + spec.volume
            coverage = self._acquired.get(spec.requester, set()).union(records)
            # Splitting requests or waiting for a new day does not avoid near-ALL review.
            high_impact = (spec.volume == TOTAL_RECORDS or total >= ESCALATION_AT or
                           len(coverage) >= ESCALATION_AT)
            if not reason and high_impact and spec.release_class != ReleaseClass.ESCALATION:
                reason = "ALL_OR_HIGH_IMPACT_REQUIRES_ESCALATION"
            if reason:
                return self._result(request_id, actor, "RELEASE", Status.BLOCK, reason)
            if spec.release_class == ReleaseClass.NORMAL:
                if spec.volume > PER_REQUEST:
                    return self._result(request_id, actor, "RELEASE", Status.BLOCK, "PER_REQUEST_LIMIT_USE_UPPER_GATE")
                if total > ROLLING_LIMIT:
                    return self._result(request_id, actor, "RELEASE", Status.INDEPENDENT_APPROVAL_REQUIRED,
                                        "ROLLING_24H_LIMIT")
            elif spec.release_class == ReleaseClass.LARGE:
                if not request.independent:
                    return self._result(request_id, actor, "RELEASE", Status.INDEPENDENT_APPROVAL_REQUIRED,
                                        "LARGE_EXTRACTION_NEEDS_INDEPENDENT_SEAT")
            else:
                reason = self._chain_ready(request)
                if not reason and not request.independent:
                    reason = "INDEPENDENT_APPROVAL_REQUIRED"
                if not reason and not request.owners:
                    reason = "FINAL_OWNER_APPROVAL_REQUIRED"
                if reason:
                    return self._result(request_id, actor, "RELEASE", Status.BLOCK, reason)
            self._ledger.append((self.clock.now, spec.requester, len(records)))
            self._acquired[spec.requester] = coverage
            request.released = True
            return self._result(request_id, actor, "RELEASE", Status.PASS, "BOUND_RELEASE", records)
