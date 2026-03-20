"""
openai_audit_logs.py — Modular input for OpenAI Audit Logs

Polls /organization/audit_logs using cursor-based pagination.
Events are returned newest-first; we buffer and write oldest-first
to maintain chronological order in Splunk. The last-seen event ID
is checkpointed in KV Store.

Sourcetype: openai:audit:logs
"""

import sys
import json
import traceback

import import_declare_test  # noqa — must be first to fix sys.path

from splunklib import modularinput as smi

import openai_utils as utils
import openai_consts as occ
from openai_api_client import OpenAIClient, OpenAIAPIError
from openai_checkpoint import CheckpointManager

APP_NAME = "ta_openai_logs"


class OpenAIAuditLogs(smi.Script):

    def __init__(self):
        super().__init__()

    def get_scheme(self):
        scheme = smi.Scheme("openai_audit_logs")
        scheme.description = "OpenAI Audit Logs"
        scheme.use_external_validation = True
        scheme.streaming_mode_xml = True
        scheme.use_single_instance = False

        scheme.add_argument(smi.Argument("name", title="Name", required_on_create=True))
        scheme.add_argument(smi.Argument("account", required_on_create=True))
        scheme.add_argument(smi.Argument("effective_at_gte", required_on_create=False))
        return scheme

    def validate_input(self, definition):
        account_name = definition.parameters.get("account")
        if not account_name:
            raise ValueError("Account is required.")
        import logging
        import openai_utils as utils
        session_key = definition.metadata.get("session_key", "")
        logger = logging.getLogger(__name__)
        utils.get_account_details(session_key, logger, account_name)

    def stream_events(self, inputs, event_writer):
        session_key = self._input_definition.metadata["session_key"]
        checkpoint_dir = inputs.metadata.get("checkpoint_dir", "")

        for input_name, input_items in inputs.inputs.items():
            input_items["input_name"] = input_name

        input_name_short = input_name.split("://")[-1]
        logger = utils.set_logger(session_key, occ.AUDIT_LOGFILE_PREFIX + input_name_short)

        try:
            logger.info("Modular Input Started.")

            account_name = input_items.get("account")
            account_details = utils.get_account_details(session_key, logger, account_name)
            proxy = utils.get_proxy_settings(session_key, logger)

            config = {
                "session_key": session_key,
                "input_name": input_name_short,
                "checkpoint_dir": checkpoint_dir,
                "index": input_items.get("index", "default"),
                "effective_at_gte": input_items.get("effective_at_gte", ""),
            }
            config.update(account_details)

            collector = AuditCollector(event_writer, config, logger, proxy)
            collector.collect_events()

            logger.info("Modular Input Exited.")

        except Exception as exc:
            logger.error("Error in input %s: %s", input_name, traceback.format_exc())


class AuditCollector:

    def __init__(self, event_writer, config, logger, proxy):
        self.event_writer = event_writer
        self.config = config
        self.logger = logger
        self.client = OpenAIClient(
            api_key=config["api_key"],
            org_id=config.get("org_id"),
            proxy=proxy,
        )

    def collect_events(self):
        cp = CheckpointManager(
            self.config["session_key"],
            self.config["input_name"],
            self.config.get("checkpoint_dir"),
        )
        state = cp.load()

        params = {"limit": occ.AUDIT_PAGE_LIMIT}

        # On first run with no checkpoint, honour the configured start timestamp
        configured_start = self.config.get("effective_at_gte", "").strip()
        last_event_id = state.get("last_event_id")

        if configured_start and not last_event_id:
            try:
                params["effective_at[gte]"] = int(configured_start)
            except ValueError:
                self.logger.warning(
                    "Invalid effective_at_gte '%s' — ignoring", configured_start
                )

        # The API returns newest-first. Collect all new pages into a buffer,
        # stopping when we see the previously checkpointed event ID.
        all_events = []
        new_last_event_id = None
        pages_fetched = 0

        try:
            for page in self.client.paginate_cursor("/organization/audit_logs", params=params):
                items = page.get("data", [])
                pages_fetched += 1
                self.logger.debug("Page %d: %d events", pages_fetched, len(items))

                found_known = False
                for item in items:
                    if last_event_id and item.get("id") == last_event_id:
                        found_known = True
                        break

                    all_events.append(item)

                    if new_last_event_id is None:
                        new_last_event_id = item.get("id")

                if found_known:
                    self.logger.debug(
                        "Reached previously seen event %s — stopping pagination",
                        last_event_id,
                    )
                    break

        except OpenAIAPIError as exc:
            self.logger.error("API error fetching audit logs: %s", exc)
            return

        if not all_events:
            self.logger.info("No new audit log events found.")
            return

        # Write oldest-first
        all_events.reverse()

        for item in all_events:
            event_time = item.get("effective_at") or item.get("created_at")
            event = smi.Event(
                data=json.dumps(item),
                time=event_time,
                source="openai://audit/{}".format(self.config["input_name"]),
                sourcetype=occ.AUDIT_SOURCETYPE,
                host=occ.OPENAI_HOST,
                index=self.config["index"],
            )
            self.event_writer.write_event(event)

        if new_last_event_id:
            state["last_event_id"] = new_last_event_id
            cp.save(state)

        self.logger.info(
            "Completed audit collection: %d events written across %d pages",
            len(all_events),
            pages_fetched,
        )


if __name__ == "__main__":
    exit_code = OpenAIAuditLogs().run(sys.argv)
    sys.exit(exit_code)
