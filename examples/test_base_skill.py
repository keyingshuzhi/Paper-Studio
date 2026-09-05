"""BaseSkill 契约测试（无网络、无磁盘副作用）。"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills import (  # noqa: E402
    BaseSkill,
    SkillInvocationError,
    SkillPermission,
    SkillProgress,
)


class ContractEchoSkill(BaseSkill):
    name = "test.contract_echo"
    description = "测试标准 Skill 契约。"
    version = "2.0.0"
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["echo"],
        "properties": {"echo": {"type": "string"}},
        "additionalProperties": False,
    }
    permissions = frozenset({SkillPermission.FILESYSTEM_READ})
    default_timeout_seconds = 1.0
    created = 0

    def __init__(self) -> None:
        type(self).created += 1

    def execute(self, text: str) -> dict:
        self.report_progress(40, "处理中", stage="work")
        self.report_progress(80, "即将完成", stage="work")
        return {"echo": text}


class InvalidOutputSkill(BaseSkill):
    name = "test.invalid_output"
    output_schema = {"type": "array"}

    def execute(self, **_kwargs):
        return {"unexpected": True}


class SlowContractSkill(BaseSkill):
    name = "test.slow_contract"
    default_timeout_seconds = 0.03

    def execute(self, **_kwargs):
        time.sleep(0.2)
        return "late"


class BaseSkillContractTests(unittest.TestCase):
    def test_execute_remains_backward_compatible(self) -> None:
        self.assertEqual(ContractEchoSkill().execute("raw"), {"echo": "raw"})

    def test_success_schema_progress_and_standard_result(self) -> None:
        events: list[SkillProgress] = []
        result = ContractEchoSkill().invoke(
            text="hello",
            allowed_permissions={SkillPermission.FILESYSTEM_READ},
            progress_callback=events.append,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.unwrap(), {"echo": "hello"})
        self.assertEqual([event.percent for event in events], [0, 40, 80, 100])
        self.assertEqual(result.meta["skill_version"], "2.0.0")
        self.assertIn("request_id", result.meta)
        self.assertEqual(result.to_dict()["data"], {"echo": "hello"})

    def test_input_schema_fails_before_execution(self) -> None:
        result = ContractEchoSkill().invoke(
            text="",
            allowed_permissions={SkillPermission.FILESYSTEM_READ},
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "input_validation_error")
        with self.assertRaises(SkillInvocationError):
            result.unwrap()

    def test_output_schema_is_validated(self) -> None:
        result = InvalidOutputSkill().invoke()
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "output_validation_error")

    def test_permissions_are_enforced_when_host_supplies_policy(self) -> None:
        result = ContractEchoSkill().invoke(text="hello", allowed_permissions=set())
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "permission_denied")
        self.assertEqual(result.error.details["missing"], ["filesystem.read"])

    def test_timeout_returns_without_waiting_for_worker(self) -> None:
        started = time.monotonic()
        result = SlowContractSkill().invoke()
        elapsed = time.monotonic() - started
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "timeout")
        self.assertTrue(result.error.retryable)
        self.assertLess(elapsed, 0.15)

    def test_registry_is_lazy_and_manifest_is_discoverable(self) -> None:
        before = ContractEchoSkill.created
        isolated = BaseSkill.create("test.contract_echo")
        self.assertIsInstance(isolated, ContractEchoSkill)
        self.assertEqual(ContractEchoSkill.created, before + 1)
        self.assertIs(BaseSkill.get("test.contract_echo"),
                      BaseSkill.get("test.contract_echo"))
        manifest = BaseSkill.manifests()["test.contract_echo"]
        self.assertEqual(manifest["version"], "2.0.0")
        self.assertEqual(manifest["permissions"], ["filesystem.read"])


if __name__ == "__main__":
    unittest.main()
