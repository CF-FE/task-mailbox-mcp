import asyncio
from copy import deepcopy
import hmac
import json
from pathlib import Path
import secrets
import tempfile
import unittest

from mac.bridge_store import BridgeStore, _hash, _json
from mac.collector import Limits, collect_once
from mac.prepare_coordinator_probe import prepare
from mac.coordinator_port import CoordinatorPort, FRAME, PortError
from test_collector import FakeTransport, event, job


def sha(value):
    return _hash(_json(value))


class CoordinatorPortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Darwin Unix socket addresses are shorter than ordinary temp paths.
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp", prefix="mb-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.store = BridgeStore(base / "db", "test")
        self.policy = base / "policy"
        self.p = project = "example"
        self.j = job()
        self.t = {"repository": "ExampleOrg/demo", "project_url": "https://github.com/orgs/ExampleOrg/projects/5",
                  "issue_url": "https://github.com/ExampleOrg/demo/issues/2", "item_id": 42,
                  "iteration": {"state": "UNSCHEDULED", "reason": "Test only"}}
        self.j["payload"].update(origin_task_id=self.t["issue_url"], tracking=self.t,
            target={"service": "project-coordinator", "project_id": project},
            coordinator_request={"schema_version": 1, "kind": "QUERY_BLOCKER", "request_id": "request",
                                 "budget": {"contact_attempts": 1, "production_writes": 0, "audio_generations": 0}})
        self.j["payload_hash"] = sha(self.j["payload"])
        self.grant = {"key_id": "agent", "actions": ["verified_job", "lookup_report", "enqueue_result"],
            "project_id": project, "coordinator_id": "cloud-dot-project-coordinator", "recipient": "mac", "report_route": "cloud",
            "job_id": "job-1", "payload_hash": self.j["payload_hash"], "origin": {"channel": "verified", "subject": "native-example"},
            "origin_ref": "synthetic:receipt", "task": {"repository": self.t["repository"], "project_ref": self.t["project_url"],
                      "item_id": 42, "task_ref": self.t["issue_url"], "evidence_ref": "synthetic:project-readback"}}
        self.cfg = {"v": 1, "socket_path": str(base / "b.sock"), "keys": {"agent": secrets.token_hex(32), "probe-test": secrets.token_hex(32)},
                    "grants": [self.grant]}
        self.save()
        self.port = CoordinatorPort(self.policy, self.store)
        self.transport = FakeTransport([event(data={"payload_hash": self.j["payload_hash"]})], {"job-1": self.j})
        self.req = {"v": 1, "op": "verified_job", "job_id": "job-1"}

    def save(self):
        self.policy.write_text(json.dumps(self.cfg))
        self.policy.chmod(0o600)

    def signed(self, body, challenge, key="agent"):
        key_bytes = bytes.fromhex(self.cfg["keys"][key])
        blob = b"MBRIDGE/1\0" + challenge.encode() + b"\0" + _json(body, FRAME).encode()
        return {**body, "auth": {"key_id": key, "mac": hmac.digest(key_bytes, blob, "sha256").hex()}}

    def dispatch(self, body, key="agent"):
        challenge = secrets.token_hex(32)
        frame = self.signed(body, challenge, key)
        code = frame["auth"]["mac"]
        answer = self.port.dispatch(frame, challenge)
        received = answer.pop("server_mac")
        blob = b"MBRIDGE/1/REPLY\0" + challenge.encode() + b"\0" + code.encode() + b"\0" + _json(answer, FRAME).encode()
        self.assertEqual(received, hmac.digest(bytes.fromhex(self.cfg["keys"][key]), blob, "sha256").hex())
        return answer["value"] if answer["ok"] else answer["code"]

    async def collect(self, **kwargs):
        return await collect_once(self.store, self.transport, port=self.port, **kwargs)

    def result_request(self):
        bind = {"project_id": self.p, "request_id": "request", "job_id": "job-1", "payload_hash": self.j["payload_hash"]}
        core = {**bind, "task_ref": self.t["issue_url"], "report": {"blocker": "fixture"}}
        wire = {**bind, "schema_version": 1, "type": "PROJECT_COORDINATOR_RESULT", "core_version": 1,
                "core_result_hash": sha(core), "result": core, "task_tracking": self.t}
        eid = "coordinate-" + sha(["cloud-dot-project-coordinator", self.p, "request", "job-1", 1])
        return {"v": 1, "op": "enqueue_result", "job_id": "job-1", "project_id": self.p,
                "payload_hash": self.j["payload_hash"], "event_id": eid, "status": "REPORTED_COMPLETED", "result": wire}

    async def test_real_socket_probe_and_replay_rejection(self):
        self.cfg["grants"].clear()
        self.save()
        async with self.port.listening():
            async def send(old=None):
                r, w = await asyncio.open_unix_connection(self.port.socket_path)
                challenge = json.loads(await r.readline())["challenge"]
                frame = old or self.signed({"v": 1, "op": "probe"}, challenge, "probe-test")
                w.write((_json(frame) + "\n").encode())
                await w.drain()
                answer = json.loads(await r.readline())
                w.close()
                await w.wait_closed()
                return frame, answer
            request, reply = await send()
            self.assertEqual(set(reply["value"]), {"service", "protocol", "operations"})
            self.assertIn("server_mac", reply)
            _, rejected = await send(request)
            self.assertEqual(rejected, {"v": 1, "ok": False, "code": "ACCESS_DENIED"})
        self.assertFalse(self.port.socket_path.exists())
        self.assertEqual(self.dispatch(self.req, "probe-test"), "ACCESS_DENIED")

    async def test_unrecognized_socket_or_symlink_never_replaced(self):
        path = self.port.socket_path
        path.symlink_to(self.policy)
        with self.assertRaises(OSError):
            async with self.port.listening():
                self.fail("symlink was replaced")
        self.assertTrue(path.is_symlink())
        self.policy.chmod(0o644)
        with self.assertRaises(PortError):
            CoordinatorPort(self.policy, self.store)

    async def test_origin_from_attested_record_and_not_payload(self):
        await self.collect()
        verified = self.dispatch(self.req)
        self.assertEqual((verified["origin"], verified["origin_ref"], verified["task"]),
                         (self.grant["origin"], self.grant["origin_ref"], self.grant["task"]))
        self.assertEqual(verified["record"], self.j)
        self.assertNotEqual(verified["origin"]["subject"], self.j["payload"]["sender"])
        self.assertEqual(self.dispatch({**self.req, "job_id": "unknown"}), "ACCESS_DENIED")
        self.assertEqual(self.dispatch(self.req, "probe-test"), "ACCESS_DENIED")

    async def test_untrusted_or_wrong_scope_cannot_capture_or_expand(self):
        for name, value in [("project_id", "elsewhere"), ("recipient", "other"), ("payload_hash", "0"*64)]:
            self.cfg["grants"] = [{**self.grant, name: value}]
            self.save()
            self.assertIsNone(self.port.capture_proof(self.j))
        self.cfg["grants"] = [self.grant]
        self.grant["task"]["item_id"] += 1
        self.save()
        await self.collect()
        self.assertEqual(self.dispatch(self.req), "UNAVAILABLE")
        self.grant["task"]["item_id"] -= 1
        self.j["payload"]["coordinator_request"]["budget"]["production_writes"] = True
        self.j["payload_hash"] = self.grant["payload_hash"] = sha(self.j["payload"])
        self.save()
        self.assertIsNone(self.port.capture_proof(self.j))

    async def test_additive_legacy_refetch_requires_authorized_original_and_budget(self):
        await collect_once(self.store, self.transport)  # historical database projections only
        self.transport.calls.clear()
        await self.collect()
        self.assertEqual([op for op,_ in self.transport.calls], ["list_events"])
        self.assertEqual(self.dispatch(self.req), "UNAVAILABLE")
        self.transport.calls.clear()
        result = await self.collect()
        self.assertEqual([op for op,_ in self.transport.calls], ["list_events", "get_job"])
        self.assertEqual((result["calls"], self.store.cursor()), (2, 1))
        self.assertEqual(self.dispatch(self.req)["record"]["payload_hash"], self.j["payload_hash"])
        self.transport.calls.clear()
        self.assertEqual((await self.collect(limits=Limits(rpc=1)))["calls"], 1)

    async def test_revoke_narrow_rotate_and_freeze_proof(self):
        await self.collect()
        self.grant["actions"] = ["verified_job"]
        self.cfg["keys"]["next"] = secrets.token_hex(32)
        self.grant["key_id"] = "next"
        self.save()
        self.assertEqual(self.dispatch(self.req), "ACCESS_DENIED")
        self.assertEqual(self.dispatch(self.req,"next")["origin"], self.grant["origin"])
        self.assertEqual(self.dispatch(self.result_request(),"next"), "ACCESS_DENIED")
        self.grant["origin"]["subject"] = "changed"
        self.save()
        self.assertEqual(self.dispatch(self.req,"next"), "UNAVAILABLE")
        self.grant["key_id"] = "probe-test"
        self.save()
        with self.assertRaises(PortError):
            CoordinatorPort(self.policy, self.store)

    async def test_outbox_retry_same_event_and_exact_remote_receipt(self):
        await self.collect()
        out = self.result_request()
        look = {k: out[k] for k in ("v", "job_id", "event_id")} | {"op":"lookup_report"}
        self.assertIsNone(self.dispatch(look))
        pending = self.dispatch(out)
        self.assertEqual(pending["state"], "PENDING")
        self.assertEqual(pending, self.dispatch(out))  # local response lost
        self.assertEqual(pending, self.dispatch(look))
        self.assertEqual((await self.collect())["sent"], 1)
        sent = self.dispatch(look)
        self.assertEqual(sent, self.dispatch(out))
        self.assertEqual(sent["receipt"], self.transport.reports[out["event_id"]])
        self.assertEqual((sent["result_hash"],sent["receipt"]["version"]), (sha(out["result"]), 1))
        self.assertEqual(len(self.transport.reports), 1)

    async def test_scope_event_tracking_and_two_hashes_enforced(self):
        await self.collect()
        out = self.result_request()
        for name, value in (("status", "UNKNOWN"), ("event_id", "coordinate-"+"a"*64), ("project_id", "elsewhere"),
                            ("payload_hash", "0"*64)):
            self.assertIn(self.dispatch({**out, name:value}), ("ACCESS_DENIED","CONFLICT"))
        for name, value in (("core_version", True), ("request_id", "wrong"), ("core_result_hash", "0"*64),
                            ("task_tracking", {**self.t,"item_id":43})):
            self.assertEqual(self.dispatch({**out, "result":{**out["result"],name:value}}), "CONFLICT")
        changed = deepcopy(out)
        changed["result"]["result"]["task_ref"] = "other"
        changed["result"]["core_result_hash"] = sha(changed["result"]["result"])
        self.assertEqual(self.dispatch(changed), "CONFLICT")
        self.assertEqual(self.store.pending_reports(), [])

    async def test_conflicting_reuse_and_size_budget(self):
        await self.collect()
        out = self.result_request()
        self.dispatch(out)
        changed = deepcopy(out)
        core = changed["result"]["result"]
        core["report"]["blocker"] = "new answer"
        changed["result"]["core_result_hash"] = sha(core)
        self.assertEqual(self.dispatch(changed), "CONFLICT")
        core["report"]["blocker"] = "a" * (256*1024)
        self.assertEqual(self.dispatch(changed), "INVALID_REQUEST")
        self.assertEqual(len(self.store.pending_reports()), 1)

    async def test_four_slow_connections_do_not_prevent_collection_or_grow_slots(self):
        async with self.port.listening():
            holds = []
            try:
                for _ in range(4):
                    reader, writer = await asyncio.open_unix_connection(self.port.socket_path)
                    self.assertTrue((await reader.readline()).endswith(b"\n"))
                    holds.append(writer)
                reader, extra = await asyncio.open_unix_connection(self.port.socket_path)
                self.assertEqual(await asyncio.wait_for(reader.readline(), 1), b"")
                extra.close()
                await extra.wait_closed()
                self.assertEqual((await self.collect())["state"], "OK")
            finally:
                for writer in holds:
                    writer.close()
                    await writer.wait_closed()

    async def test_probe_provisioning_rotation_is_additive_and_preserves_grants(self):
        client = self.policy.with_name("probe-key")
        replacement = self.policy.with_name("next-key")
        prepare(self.policy, self.port.socket_path, client, "probe-new")
        prepare(self.policy, self.port.socket_path, client, "probe-new")
        prepare(self.policy, self.port.socket_path, replacement, "probe-next")
        cfg = json.loads(self.policy.read_text())
        self.assertEqual(cfg["keys"]["probe-new"], client.read_text().strip())
        self.assertEqual(cfg["keys"]["probe-next"], replacement.read_text().strip())
        self.assertNotEqual(cfg["keys"]["probe-new"], cfg["keys"]["probe-next"])
        self.assertEqual(cfg["grants"], self.cfg["grants"])
        prepare(self.policy, self.port.socket_path, replacement, "probe-next", "probe-new")
        updated = json.loads(self.policy.read_text())
        self.assertNotIn("probe-new", updated["keys"])
        self.assertEqual(updated["keys"]["agent"], self.cfg["keys"]["agent"])
        with self.assertRaises(ValueError):
            prepare(self.policy, self.port.socket_path, replacement, "probe-next", "agent")

    async def test_new_policy_starts_empty_and_never_writes_a_socket(self):
        fresh = self.policy.with_name("fresh")
        client = self.policy.with_name("client")
        prepare(fresh, self.port.socket_path, client, "probe-only")
        installed = json.loads(fresh.read_text())
        self.assertEqual((list(installed["keys"]), installed["grants"]), (["probe-only"], []))
        self.assertFalse(self.port.socket_path.exists())
        self.assertEqual((client.stat().st_mode & 0o777, fresh.stat().st_mode & 0o777), (0o600, 0o600))
