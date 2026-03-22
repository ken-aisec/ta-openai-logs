"""
Unit tests for anthropic_checkpoint.py

solnlib is a Splunk runtime dependency — not available locally.
We stub the entire solnlib tree before importing, then patch the
module-level `checkpointer` reference directly in each test.
"""

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# Build minimal solnlib stub tree so the import doesn't fail
_stub_checkpointer = types.ModuleType("solnlib.modular_input.checkpointer")
_stub_checkpointer.KVStoreCheckpointer = MagicMock()

_stub_modular_input = types.ModuleType("solnlib.modular_input")
_stub_modular_input.checkpointer = _stub_checkpointer

_stub_solnlib = types.ModuleType("solnlib")
_stub_solnlib.modular_input = _stub_modular_input

sys.modules.setdefault("solnlib", _stub_solnlib)
sys.modules.setdefault("solnlib.modular_input", _stub_modular_input)
sys.modules.setdefault("solnlib.modular_input.checkpointer", _stub_checkpointer)

# Also stub anthropic_consts if not importable standalone
if "anthropic_consts" not in sys.modules:
    _stub_consts = types.ModuleType("anthropic_consts")
    _stub_consts.KVSTORE_COLLECTION = "ta_anthropic_logs_checkpoints"
    sys.modules["anthropic_consts"] = _stub_consts

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "package", "bin"))

import anthropic_checkpoint  # noqa: E402
from anthropic_checkpoint import CheckpointManager  # noqa: E402


class TestCheckpointManager(unittest.TestCase):

    def setUp(self):
        self.mock_kv = MagicMock()
        patcher = patch.object(anthropic_checkpoint.checkpointer, "KVStoreCheckpointer",
                               return_value=self.mock_kv)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cp = CheckpointManager("fake_session_key", "my_input")

    def test_load_returns_empty_dict_when_no_checkpoint(self):
        self.mock_kv.get.return_value = None
        self.assertEqual(self.cp.load(), {})

    def test_load_returns_stored_dict(self):
        data = {"last_fetched_date": "2026-03-22", "count": 5}
        self.mock_kv.get.return_value = data
        self.assertEqual(self.cp.load(), data)

    def test_save_calls_update(self):
        state = {"last_fetched_date": "2026-03-22"}
        self.cp.save(state)
        self.mock_kv.update.assert_called_once_with("my_input", state)

    def test_clear_calls_delete(self):
        self.cp.clear()
        self.mock_kv.delete.assert_called_once_with("my_input")

    def test_load_handles_exception_gracefully(self):
        self.mock_kv.get.side_effect = Exception("KV Store unavailable")
        self.assertEqual(self.cp.load(), {})

    def test_save_handles_exception_gracefully(self):
        self.mock_kv.update.side_effect = Exception("KV Store write failed")
        # Should not raise — logs critical but continues
        try:
            self.cp.save({"last_fetched_date": "2026-03-22"})
        except Exception:
            self.fail("save() should not raise on KV Store failure")


if __name__ == "__main__":
    unittest.main()
