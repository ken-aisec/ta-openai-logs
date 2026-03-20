"""
openai_checkpoint.py — Checkpoint manager for OpenAI TA

Tries KVStoreCheckpointer first; falls back to FileCheckpointer
if KV Store is disabled or unavailable (common on dev instances).
"""

import sys
import traceback
import logging

from solnlib.modular_input import checkpointer

import openai_consts as occ

APP_NAME = "ta_openai_logs"

logger = logging.getLogger(__name__)


class CheckpointManager:
    """
    Dict-based checkpoint interface. Uses KV Store when available,
    falls back to file-based checkpointing otherwise.

    Example::

        cp = CheckpointManager(session_key, "my_input", checkpoint_dir)
        state = cp.load()
        state["last_cursor"] = "evt_abc123"
        cp.save(state)
    """

    def __init__(self, session_key, input_name, checkpoint_dir=None):
        self._key = input_name
        self._checkpointer = self._init_checkpointer(session_key, checkpoint_dir)

    def _init_checkpointer(self, session_key, checkpoint_dir):
        try:
            kv = checkpointer.KVStoreCheckpointer(
                occ.KVSTORE_COLLECTION, session_key, APP_NAME
            )
            # Probe to confirm KV Store is actually reachable
            kv.get("__ping__")
            logger.debug("Using KV Store checkpointer.")
            return kv
        except Exception as exc:
            logger.warning(
                "KV Store unavailable (%s) — falling back to file checkpointer.", exc
            )
            if not checkpoint_dir:
                logger.error("No checkpoint_dir provided and KV Store unavailable.")
                sys.exit(1)
            logger.debug("Using file checkpointer at: %s", checkpoint_dir)
            return checkpointer.FileCheckpointer(checkpoint_dir)

    def load(self):
        """Return the checkpoint dict, or empty dict if none exists."""
        try:
            data = self._checkpointer.get(self._key)
            if not data:
                logger.debug("No checkpoint found for key: %s", self._key)
                return {}
            logger.debug("Loaded checkpoint for %s: %s", self._key, data)
            return data
        except Exception as exc:
            logger.warning(
                "Could not load checkpoint for %s: %s — starting fresh", self._key, exc
            )
            return {}

    def save(self, state):
        """Persist the checkpoint dict."""
        try:
            self._checkpointer.update(self._key, state)
            logger.debug("Saved checkpoint for %s: %s", self._key, state)
        except Exception as exc:
            logger.critical(
                "CHECKPOINT SAVE FAILED for %s — next run will re-fetch data. %s",
                self._key, traceback.format_exc()
            )

    def clear(self):
        """Delete the checkpoint."""
        try:
            self._checkpointer.delete(self._key)
            logger.info("Cleared checkpoint for %s", self._key)
        except Exception as exc:
            logger.warning("Could not clear checkpoint for %s: %s", self._key, exc)
