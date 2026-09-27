import unittest

from controller.flow_manager import FlowManager, PlannedRule
from controller.transactions import (
    RouteTransactionManager,
    StaleTopologyError,
)


class BarrierRequest:
    def __init__(self) -> None:
        self.xid = None


class FakeOfproto:
    OFPIT_APPLY_ACTIONS = 4
    OFPFF_SEND_FLOW_REM = 1
    OFPFC_DELETE_STRICT = 4
    OFPFC_DELETE = 3
    OFPTT_ALL = 0xFF
    OFPP_ANY = 0xFFFFFFFF
    OFPG_ANY = 0xFFFFFFFF


class FakeParser:
    @staticmethod
    def OFPMatch(**kwargs):
        return {"kind": "match", **kwargs}

    @staticmethod
    def OFPActionOutput(port):
        return {"kind": "output", "port": port}

    @staticmethod
    def OFPInstructionActions(kind, actions):
        return {
            "kind": "instructions",
            "type": kind,
            "actions": actions,
        }

    @staticmethod
    def OFPFlowMod(**kwargs):
        return {"kind": "flow_mod", **kwargs}

    @staticmethod
    def OFPBarrierRequest(datapath):
        return BarrierRequest()


class FakeDatapath:
    def __init__(self, dpid: int) -> None:
        self.id = dpid
        self.ofproto = FakeOfproto()
        self.ofproto_parser = FakeParser()
        self.messages = []
        self._next_xid = dpid * 100

    def set_xid(self, request) -> None:
        self._next_xid += 1
        request.xid = self._next_xid

    def send_msg(self, message) -> None:
        self.messages.append(message)


def rule(dpid: int, output_port: int) -> PlannedRule:
    return PlannedRule(
        dpid=dpid,
        source_mac="00:00:00:00:00:01",
        destination_mac="00:00:00:00:00:02",
        output_port=output_port,
    )


class RouteTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.datapaths = {
            dpid: FakeDatapath(dpid)
            for dpid in (1, 2, 3)
        }
        self.manager = RouteTransactionManager(
            FlowManager(), timeout_seconds=5
        )
        self.old_rules = (rule(1, 12), rule(2, 29))
        self.new_rules = (rule(1, 13), rule(3, 39))

    def test_two_barrier_phases_commit_transaction(self) -> None:
        self.manager.begin(
            transaction_id="tx1",
            topology_generation=7,
            current_generation=7,
            datapaths=self.datapaths,
            old_rules=self.old_rules,
            new_rules=self.new_rules,
            now=10,
        )
        first = self.manager.acknowledge(
            dpid=1,
            xid=101,
            current_generation=7,
            datapaths=self.datapaths,
        )
        self.assertIsNone(first)
        second = self.manager.acknowledge(
            dpid=3,
            xid=301,
            current_generation=7,
            datapaths=self.datapaths,
        )
        self.assertIsNone(second)

        result = self.manager.acknowledge(
            dpid=2,
            xid=201,
            current_generation=7,
            datapaths=self.datapaths,
        )

        self.assertEqual(result.status, "committed")
        self.assertNotIn("tx1", self.manager.pending_ids)

    def test_stale_generation_is_rejected_before_install(self) -> None:
        with self.assertRaises(StaleTopologyError):
            self.manager.begin(
                transaction_id="tx1",
                topology_generation=6,
                current_generation=7,
                datapaths=self.datapaths,
                old_rules=self.old_rules,
                new_rules=self.new_rules,
                now=10,
            )
        self.assertTrue(
            all(
                not datapath.messages
                for datapath in self.datapaths.values()
            )
        )

    def test_generation_change_rolls_back(self) -> None:
        self.manager.begin(
            transaction_id="tx1",
            topology_generation=7,
            current_generation=7,
            datapaths=self.datapaths,
            old_rules=self.old_rules,
            new_rules=self.new_rules,
            now=10,
        )

        result = self.manager.acknowledge(
            dpid=1,
            xid=101,
            current_generation=8,
            datapaths=self.datapaths,
            now=11,
        )

        self.assertIsNone(result)
        self.assertIn("tx1", self.manager.pending_ids)
        self.manager.acknowledge(
            dpid=1,
            xid=102,
            current_generation=8,
            datapaths=self.datapaths,
            now=12,
        )
        self.manager.acknowledge(
            dpid=2,
            xid=201,
            current_generation=8,
            datapaths=self.datapaths,
            now=12,
        )
        result = self.manager.acknowledge(
            dpid=3,
            xid=302,
            current_generation=8,
            datapaths=self.datapaths,
            now=12,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(
            result.reason, "topology_generation_changed"
        )

    def test_timeout_rolls_back(self) -> None:
        self.manager.begin(
            transaction_id="tx1",
            topology_generation=7,
            current_generation=7,
            datapaths=self.datapaths,
            old_rules=self.old_rules,
            new_rules=self.new_rules,
            now=10,
        )

        results = self.manager.expire(
            now=16,
            current_generation=7,
            datapaths=self.datapaths,
        )

        self.assertEqual(results, ())
        self.assertIn("tx1", self.manager.pending_ids)
        self.manager.acknowledge(
            dpid=1,
            xid=102,
            current_generation=7,
            datapaths=self.datapaths,
            now=17,
        )
        self.manager.acknowledge(
            dpid=2,
            xid=201,
            current_generation=7,
            datapaths=self.datapaths,
            now=17,
        )
        result = self.manager.acknowledge(
            dpid=3,
            xid=302,
            current_generation=7,
            datapaths=self.datapaths,
            now=17,
        )
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.reason, "barrier_timeout")

    def test_managed_rule_purge_uses_application_cookie(self) -> None:
        datapath = self.datapaths[1]

        FlowManager.purge_managed(datapath)

        message = datapath.messages[-1]
        self.assertEqual(
            message["cookie"],
            FlowManager.APPLICATION_ID << 48,
        )
        self.assertEqual(
            message["cookie_mask"],
            FlowManager.APPLICATION_MASK,
        )
        self.assertEqual(
            message["command"], FakeOfproto.OFPFC_DELETE
        )


if __name__ == "__main__":
    unittest.main()