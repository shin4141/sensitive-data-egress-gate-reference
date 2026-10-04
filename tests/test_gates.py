import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from sdeg import Gate, ManualClock, ReleaseClass, Status
from sdeg.demo import current_state_export
from sdeg.gate import APPROVAL_TTL_SECONDS, SEATS, TOTAL_RECORDS, WAIT_SECONDS, WINDOW_SECONDS


class GateTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.gate = Gate(self.clock)

    def request(self, amount, **kwargs):
        return self.gate.request("operator", amount, **kwargs)

    def release(self, amount, **kwargs):
        return self.gate.release(self.request(amount, **kwargs), "operator")

    def fill_window(self):
        acquired = set()
        for n, path in enumerate(("csv", "api", "export", "csv")):
            result = self.release(500, path=path, offset=n * 500)
            self.assertEqual(result.status, Status.PASS)
            acquired.update(result.records)
        self.assertEqual(len(acquired), 2_000)

    def ready_escalation(self):
        request = self.request("ALL", release_class=ReleaseClass.ESCALATION)
        self.assertEqual(self.gate.notify(request, "operator").status, Status.PASS)
        self.clock.advance(WAIT_SECONDS)
        self.assertEqual(self.gate.approve_independent(request, "reviewer").status, Status.PASS)
        self.assertEqual(self.gate.approve_owner(request, "owner").status, Status.PASS)
        return request

    def test_current_one_credential_reaches_all(self):
        records = current_state_export("operator")
        self.assertEqual(len(records), TOTAL_RECORDS)
        self.assertTrue(all(r.startswith("FICTIONAL/") for r in records))

    def test_normal_500_passes_with_actual_records(self):
        result = self.release(500)
        self.assertEqual(result.status, Status.PASS)
        self.assertEqual(result.count, 500)
        self.assertEqual(len(set(result.records)), 500)

    def test_normal_501_is_blocked(self):
        result = self.release(501)
        self.assertEqual(result.status, Status.BLOCK)
        self.assertEqual(result.count, 0)
        self.assertIn("UPPER_GATE", result.reason)

    def test_rolling_2000_passes_across_paths(self):
        self.fill_window()
        self.assertEqual(self.gate.rolling_total("operator"), 2_000)

    def test_rolling_2001_requires_independent_approval(self):
        self.fill_window()
        result = self.release(1, offset=2_000)
        self.assertEqual(result.status, Status.INDEPENDENT_APPROVAL_REQUIRED)
        self.assertEqual(result.count, 0)
        self.assertEqual(self.gate.rolling_total("operator"), 2_000)

    def test_switching_scope_does_not_reset_rolling_total(self):
        self.fill_window()
        self.assertEqual(self.release(1, scope="documents").status,
                         Status.INDEPENDENT_APPROVAL_REQUIRED)

    def test_rolling_window_boundary(self):
        self.fill_window()
        self.clock.advance(WINDOW_SECONDS - 1)
        self.assertEqual(self.release(1).status, Status.INDEPENDENT_APPROVAL_REQUIRED)
        self.clock.advance(1)
        self.assertEqual(self.release(500).status, Status.PASS)

    def test_window_rolls_instead_of_resetting_at_midnight(self):
        self.release(500)
        self.clock.advance(3_600)
        self.release(500, offset=500)
        self.clock.advance(WINDOW_SECONDS - 3_600)
        self.assertEqual(self.gate.rolling_total("operator"), 500)

    def test_large_extraction_needs_independent_seat(self):
        request = self.request(501, release_class=ReleaseClass.LARGE)
        self.assertEqual(self.gate.release(request, "operator").status,
                         Status.INDEPENDENT_APPROVAL_REQUIRED)
        self.assertEqual(self.gate.approve_independent(request, "reviewer").status, Status.PASS)
        self.assertEqual(self.gate.release(request, "operator").count, 501)

    def test_splitting_large_requests_cannot_avoid_high_impact_gate(self):
        for offset in (0, 4_000):
            request = self.request(4_000, offset=offset, release_class=ReleaseClass.LARGE)
            self.gate.approve_independent(request, "reviewer")
            self.assertEqual(self.gate.release(request, "operator").count, 4_000)
        request = self.request(2_000, offset=8_000, release_class=ReleaseClass.LARGE)
        self.gate.approve_independent(request, "reviewer")
        self.assertEqual(self.gate.release(request, "operator").reason,
                         "ALL_OR_HIGH_IMPACT_REQUIRES_ESCALATION")

    def test_near_all_coverage_still_requires_escalation_across_days(self):
        for n in range(19):
            if n and n % 4 == 0:
                self.clock.advance(WINDOW_SECONDS)
            self.assertEqual(self.release(500, offset=n * 500).count, 500)
        request = self.request(500, offset=9_500)
        self.assertEqual(self.gate.release(request, "operator").reason,
                         "ALL_OR_HIGH_IMPACT_REQUIRES_ESCALATION")

    def test_applicant_cannot_count_as_approver(self):
        request = self.request(501, release_class=ReleaseClass.LARGE)
        self.assertEqual(self.gate.approve_independent(request, "operator").status, Status.BLOCK)
        self.assertEqual(self.gate.release(request, "operator").status,
                         Status.INDEPENDENT_APPROVAL_REQUIRED)

    def test_owner_cannot_count_as_independent_seat(self):
        request = self.request(501, release_class=ReleaseClass.LARGE)
        self.assertEqual(self.gate.approve_independent(request, "owner").status, Status.BLOCK)

    def test_all_and_high_impact_cannot_use_normal_or_large_gate(self):
        for amount in (10_000, "ALL", TOTAL_RECORDS):
            for route in (ReleaseClass.NORMAL, ReleaseClass.LARGE):
                with self.subTest(amount=amount, route=route):
                    request = self.request(amount, release_class=route)
                    if route == ReleaseClass.LARGE:
                        self.gate.approve_independent(request, "reviewer")
                    result = self.gate.release(request, "operator")
                    self.assertEqual(result.status, Status.BLOCK)
                    self.assertEqual(result.count, 0)
                    self.assertIn("ESCALATION", result.reason)

    def test_owner_alone_cannot_request_notify_approve_release_all(self):
        request = self.gate.request("owner", "ALL", release_class=ReleaseClass.ESCALATION)
        self.assertEqual(self.gate.notify(request, "owner").status, Status.BLOCK)
        self.clock.advance(WAIT_SECONDS)
        self.assertEqual(self.gate.approve_independent(request, "owner").status, Status.BLOCK)
        self.assertEqual(self.gate.approve_owner(request, "owner").status, Status.BLOCK)
        self.assertEqual(self.gate.release(request, "owner").status, Status.BLOCK)

    def test_owner_cannot_approve_before_independent_seat(self):
        request = self.request("ALL", release_class=ReleaseClass.ESCALATION)
        self.gate.notify(request, "operator")
        self.clock.advance(WAIT_SECONDS)
        self.assertEqual(self.gate.approve_owner(request, "owner").status, Status.BLOCK)
        self.assertEqual(self.gate.release(request, "operator").status, Status.BLOCK)

    def test_notification_is_required_before_wait(self):
        request = self.request("ALL", release_class=ReleaseClass.ESCALATION)
        self.clock.advance(WAIT_SECONDS)
        self.assertEqual(self.gate.approve_independent(request, "reviewer").status, Status.BLOCK)
        self.assertEqual(self.gate.release(request, "operator").reason, "NOTIFY_REQUIRED")

    def test_notify_targets_all_registered_seats_without_external_io(self):
        request = self.request("ALL", release_class=ReleaseClass.ESCALATION)
        self.gate.notify(request, "operator")
        self.assertEqual(self.gate.notifications(request), frozenset(SEATS))

    def test_before_wait_completes_approval_and_release_are_blocked(self):
        request = self.request("ALL", release_class=ReleaseClass.ESCALATION)
        self.gate.notify(request, "operator")
        self.clock.advance(WAIT_SECONDS - 1)
        self.assertEqual(self.gate.approve_independent(request, "reviewer").status, Status.BLOCK)
        self.assertEqual(self.gate.approve_owner(request, "owner").status, Status.BLOCK)
        self.assertEqual(self.gate.release(request, "operator").reason, "WAIT_NOT_COMPLETE")
        self.clock.advance(1)
        self.assertEqual(self.gate.approve_independent(request, "reviewer").status, Status.PASS)

    def test_final_owner_approval_is_required(self):
        request = self.request("ALL", release_class=ReleaseClass.ESCALATION)
        self.gate.notify(request, "operator")
        self.clock.advance(WAIT_SECONDS)
        self.gate.approve_independent(request, "reviewer")
        self.assertEqual(self.gate.release(request, "operator").reason, "FINAL_OWNER_APPROVAL_REQUIRED")

    def test_complete_escalation_chain_releases_all(self):
        result = self.gate.release(self.ready_escalation(), "operator")
        self.assertEqual(result.status, Status.PASS)
        self.assertEqual(result.count, TOTAL_RECORDS)

    def test_owner_cannot_execute_another_request(self):
        request = self.ready_escalation()
        self.assertEqual(self.gate.release(request, "owner").status, Status.BLOCK)

    def test_new_destination_invalidates_existing_approval(self):
        request = self.ready_escalation()
        result = self.gate.revise(request, "operator", destination="fictional://new-vault")
        self.assertEqual(result.reason, "APPROVAL_INVALIDATED")
        self.assertEqual(self.gate.notifications(request), frozenset())
        self.assertEqual(self.gate.release(request, "operator").reason, "DESTINATION_NOT_APPROVED")
        self.gate.revise(request, "operator", destination="fictional://approved-vault")
        self.assertEqual(self.gate.release(request, "operator").reason, "NOTIFY_REQUIRED")

    def test_mutation_invalidates_chain_conditions(self):
        for field, value in (("scope", "documents"), ("volume", 10_000),
                             ("requester", "operator-b"), ("release_class", ReleaseClass.LARGE),
                             ("path", "api")):
            with self.subTest(field=field):
                self.setUp()
                request = self.ready_escalation()
                self.assertEqual(self.gate.revise(request, "operator", **{field: value}).reason,
                                 "APPROVAL_INVALIDATED")
                actor = "operator-b" if field == "requester" else "operator"
                self.assertEqual(self.gate.release(request, actor).status, Status.BLOCK)

    def test_changed_range_invalidates_large_approval(self):
        request = self.request(501, release_class=ReleaseClass.LARGE)
        self.gate.approve_independent(request, "reviewer")
        self.gate.revise(request, "operator", offset=501)
        self.assertEqual(self.gate.release(request, "operator").status,
                         Status.INDEPENDENT_APPROVAL_REQUIRED)

    def test_revised_chain_can_only_pass_after_fresh_steps(self):
        request = self.ready_escalation()
        self.gate.revise(request, "operator", scope="documents")
        self.gate.notify(request, "operator")
        self.assertEqual(self.gate.release(request, "operator").reason, "WAIT_NOT_COMPLETE")
        self.clock.advance(WAIT_SECONDS)
        self.gate.approve_independent(request, "reviewer")
        self.gate.approve_owner(request, "owner")
        self.assertEqual(self.gate.release(request, "operator").count, TOTAL_RECORDS)

    def test_approval_expiry_blocks_release(self):
        request = self.ready_escalation()
        self.clock.advance(APPROVAL_TTL_SECONDS - WAIT_SECONDS)
        self.assertEqual(self.gate.release(request, "operator").reason, "APPROVAL_EXPIRED")

    def test_approvals_cannot_be_reused_for_another_request(self):
        self.ready_escalation()
        other = self.request("ALL", release_class=ReleaseClass.ESCALATION)
        self.assertEqual(self.gate.release(other, "operator").reason, "NOTIFY_REQUIRED")

    def test_successful_release_is_single_use(self):
        request = self.ready_escalation()
        self.gate.release(request, "operator")
        self.assertEqual(self.gate.release(request, "operator").reason, "ALREADY_RELEASED")
        self.assertEqual(self.gate.rolling_total("operator"), TOTAL_RECORDS)

    def test_normal_rolling_check_and_allocation_are_atomic(self):
        barrier = Barrier(8)

        def attempt(n):
            request = self.request(500, offset=n * 500)
            barrier.wait()
            return self.gate.release(request, "operator")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(r.count for r in results), 2_000)
        self.assertEqual(sum(r.status == Status.PASS for r in results), 4)
        self.assertEqual(self.gate.rolling_total("operator"), 2_000)

    def test_invalid_inputs_fail_closed(self):
        for amount in (0, -1, True, False, 500.0, "500", TOTAL_RECORDS + 1):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.request(amount)
        for change in ({"requester": "unknown"}, {"scope": "unknown"},
                       {"release_class": "unknown"}, {"offset": -1}, {"offset": TOTAL_RECORDS}):
            args = {"requester": "operator", "volume": 500, **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.gate.request(**args)

    def test_clock_cannot_move_backwards(self):
        with self.assertRaises(ValueError):
            self.clock.advance(-1)

    def test_cli_output_is_reproducible(self):
        root = Path(__file__).resolve().parents[1]
        output = subprocess.check_output([sys.executable, "-m", "sdeg"], cwd=root, text=True)
        self.assertEqual(output, (root / "examples/cli-output.txt").read_text())


if __name__ == "__main__":
    unittest.main()
