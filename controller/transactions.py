from __future__ import annotations

import time
from dataclasses import dataclass, field

from controller.flow_manager import FlowManager, PlannedRule
from controller.transaction_journal import TransactionJournal


class StaleTopologyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TransactionResult:
    transaction_id: str
    status: str
    reason: str


@dataclass(slots=True)
class _PendingTransaction:
    transaction_id: str
    topology_generation: int
    started_at: float
    old_rules: tuple[PlannedRule, ...]
    new_rules: tuple[PlannedRule, ...]
    phase: str = "install"
    rollback_reason: str | None = None
    barriers: set[tuple[int, int]] = field(
        default_factory=set
    )


class RouteTransactionManager:
    """Barrier-backed two-phase route replacement with rollback."""

    def __init__(
        self,
        flow_manager: FlowManager,
        timeout_seconds: float = 3.0,
        journal: TransactionJournal | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.flow_manager = flow_manager
        self.timeout_seconds = timeout_seconds
        self.journal = journal
        self._pending: dict[str, _PendingTransaction] = {}
        self._barriers: dict[
            tuple[int, int], str
        ] = {}

    @property
    def pending_ids(self) -> frozenset[str]:
        return frozenset(self._pending)

    def begin(
        self,
        *,
        transaction_id: str,
        topology_generation: int,
        current_generation: int,
        datapaths: dict[int, object],
        old_rules: tuple[PlannedRule, ...],
        new_rules: tuple[PlannedRule, ...],
        now: float,
    ) -> None:
        if topology_generation != current_generation:
            raise StaleTopologyError(
                "routing decision topology generation is stale"
            )
        if transaction_id in self._pending:
            raise ValueError("transaction already exists")
        transaction = _PendingTransaction(
            transaction_id=transaction_id,
            topology_generation=topology_generation,
            started_at=now,
            old_rules=old_rules,
            new_rules=new_rules,
        )
        self._pending[transaction_id] = transaction
        try:
            self.flow_manager.install(datapaths, new_rules)
            transaction.barriers = self._request_barriers(
                datapaths,
                {rule.dpid for rule in new_rules},
                transaction_id,
            )
            self._record(
                transaction,
                "install_pending",
                now,
            )
        except Exception:
            self._rollback_immediate(
                transaction,
                datapaths,
                "transaction_start_failed",
                now,
            )
            raise

    def acknowledge(
        self,
        *,
        dpid: int,
        xid: int,
        current_generation: int,
        datapaths: dict[int, object],
        now: float | None = None,
    ) -> TransactionResult | None:
        observed_at = (
            time.monotonic() if now is None else now
        )
        key = (dpid, xid)
        transaction_id = self._barriers.pop(key, None)
        if transaction_id is None:
            return None
        transaction = self._pending.get(transaction_id)
        if transaction is None:
            return None
        transaction.barriers.discard(key)
        if (
            current_generation
            != transaction.topology_generation
            and transaction.phase != "rollback"
        ):
            return self._start_rollback(
                transaction,
                datapaths,
                "topology_generation_changed",
                observed_at,
            )
        if transaction.barriers:
            return None

        if transaction.phase == "rollback":
            self._forget(transaction)
            self._record(
                transaction,
                "rolled_back",
                observed_at,
                {"reason": transaction.rollback_reason},
            )
            return TransactionResult(
                transaction_id=transaction_id,
                status="rolled_back",
                reason=(
                    transaction.rollback_reason
                    or "rollback_completed"
                ),
            )

        if transaction.phase == "install":
            new_dpids = frozenset(
                rule.dpid for rule in transaction.new_rules
            )
            self.flow_manager.delete(
                datapaths,
                transaction.old_rules,
                exclude_dpids=new_dpids,
            )
            retired_dpids = {
                rule.dpid
                for rule in transaction.old_rules
                if rule.dpid not in new_dpids
                and rule.dpid in datapaths
            }
            transaction.phase = "retire"
            transaction.started_at = observed_at
            transaction.barriers = self._request_barriers(
                datapaths,
                retired_dpids,
                transaction_id,
            )
            self._record(
                transaction,
                "retire_pending",
                observed_at,
            )
            if transaction.barriers:
                return None

        self._forget(transaction)
        self._record(
            transaction,
            "committed",
            observed_at,
        )
        return TransactionResult(
            transaction_id=transaction_id,
            status="committed",
            reason="barriers_acknowledged",
        )

    def expire(
        self,
        *,
        now: float,
        current_generation: int,
        datapaths: dict[int, object],
    ) -> tuple[TransactionResult, ...]:
        results = []
        for transaction in tuple(self._pending.values()):
            stale = (
                current_generation
                != transaction.topology_generation
            )
            timed_out = (
                now - transaction.started_at
                >= self.timeout_seconds
            )
            if stale or timed_out:
                if transaction.phase == "rollback":
                    self._forget(transaction)
                    reason = (
                        f"{transaction.rollback_reason or 'rollback'}"
                        ":rollback_barrier_timeout"
                    )
                    self._record(
                        transaction,
                        "rolled_back",
                        now,
                        {"reason": reason},
                    )
                    results.append(
                        TransactionResult(
                            transaction_id=(
                                transaction.transaction_id
                            ),
                            status="rolled_back",
                            reason=reason,
                        )
                    )
                    continue
                result = self._start_rollback(
                    transaction,
                    datapaths,
                    (
                        "topology_generation_changed"
                        if stale
                        else "barrier_timeout"
                    ),
                    now,
                )
                if result is not None:
                    results.append(result)
        return tuple(results)

    def _start_rollback(
        self,
        transaction: _PendingTransaction,
        datapaths: dict[int, object],
        reason: str,
        now: float,
    ) -> TransactionResult | None:
        # Reinstalling old rules restores shared-switch actions too.
        self._clear_barriers(transaction)
        rollback_complete = True
        try:
            self.flow_manager.install(
                datapaths, transaction.old_rules
            )
        except (KeyError, ValueError):
            rollback_complete = False
        try:
            old_dpids = frozenset(
                rule.dpid for rule in transaction.old_rules
            )
            self.flow_manager.delete(
                datapaths,
                transaction.new_rules,
                exclude_dpids=old_dpids,
            )
        except (KeyError, ValueError):
            rollback_complete = False
        if not rollback_complete:
            self._forget(transaction)
            failure_reason = f"{reason}:rollback_incomplete"
            self._record(
                transaction,
                "rolled_back",
                now,
                {"reason": failure_reason},
            )
            return TransactionResult(
                transaction_id=transaction.transaction_id,
                status="rolled_back",
                reason=failure_reason,
            )

        transaction.phase = "rollback"
        transaction.rollback_reason = reason
        transaction.started_at = now
        rollback_dpids = {
            rule.dpid
            for rule in (
                *transaction.old_rules,
                *transaction.new_rules,
            )
            if rule.dpid in datapaths
        }
        transaction.barriers = self._request_barriers(
            datapaths,
            rollback_dpids,
            transaction.transaction_id,
        )
        self._record(
            transaction,
            "rollback_pending",
            now,
            {"reason": reason},
        )
        if transaction.barriers:
            return None
        self._forget(transaction)
        self._record(
            transaction,
            "rolled_back",
            now,
            {"reason": reason},
        )
        return TransactionResult(
            transaction_id=transaction.transaction_id,
            status="rolled_back",
            reason=reason,
        )

    def _rollback_immediate(
        self,
        transaction: _PendingTransaction,
        datapaths: dict[int, object],
        reason: str,
        now: float,
    ) -> None:
        try:
            self.flow_manager.install(
                datapaths, transaction.old_rules
            )
        except (KeyError, ValueError):
            reason = f"{reason}:rollback_incomplete"
        try:
            old_dpids = frozenset(
                rule.dpid for rule in transaction.old_rules
            )
            self.flow_manager.delete(
                datapaths,
                transaction.new_rules,
                exclude_dpids=old_dpids,
            )
        except (KeyError, ValueError):
            if not reason.endswith(":rollback_incomplete"):
                reason = f"{reason}:rollback_incomplete"
        self._forget(transaction)
        self._record(
            transaction,
            "rolled_back",
            now,
            {"reason": reason},
        )

    def _request_barriers(
        self,
        datapaths: dict[int, object],
        dpids: set[int],
        transaction_id: str,
    ) -> set[tuple[int, int]]:
        barriers = set()
        for dpid in sorted(dpids):
            if dpid not in datapaths:
                raise ValueError(
                    f"missing datapath for barrier: {dpid}"
                )
            xid = self.flow_manager.request_barrier(
                datapaths[dpid]
            )
            key = (dpid, xid)
            barriers.add(key)
            self._barriers[key] = transaction_id
        return barriers

    def _forget(
        self, transaction: _PendingTransaction
    ) -> None:
        for key in transaction.barriers:
            self._barriers.pop(key, None)
        self._pending.pop(transaction.transaction_id, None)

    def _clear_barriers(
        self, transaction: _PendingTransaction
    ) -> None:
        for key in transaction.barriers:
            self._barriers.pop(key, None)
        transaction.barriers.clear()

    def _record(
        self,
        transaction: _PendingTransaction,
        status: str,
        occurred_at: float,
        details: dict | None = None,
    ) -> None:
        if self.journal is None:
            return
        self.journal.append(
            transaction_id=transaction.transaction_id,
            status=status,
            occurred_at=occurred_at,
            details={
                "phase": transaction.phase,
                "topology_generation": (
                    transaction.topology_generation
                ),
                **(details or {}),
            },
        )