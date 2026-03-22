"""
openai_usage_logs.py — Modular input for OpenAI Usage Logs

Polls /organization/usage/completions for daily token-usage buckets.
Uses date-based checkpointing; re-fetches last REFETCH_DAYS to catch
late-arriving records.

Sourcetype: openai:usage:logs
"""

import sys
import os
import json
import datetime
import traceback

import import_declare_test  # noqa — must be first to fix sys.path

from splunklib import modularinput as smi

import openai_utils as utils
import openai_consts as occ
from openai_api_client import OpenAIClient, OpenAIAPIError
from openai_checkpoint import CheckpointManager

APP_NAME = "ta_openai_logs"


class OpenAIUsageLogs(smi.Script):

    def __init__(self):
        super().__init__()

    def get_scheme(self):
        scheme = smi.Scheme("openai_usage_logs")
        scheme.description = "OpenAI Usage Logs"
        scheme.use_external_validation = True
        scheme.streaming_mode_xml = True
        scheme.use_single_instance = False

        scheme.add_argument(smi.Argument("name", title="Name", required_on_create=True))
        scheme.add_argument(smi.Argument("account", required_on_create=True))
        scheme.add_argument(smi.Argument("start_date", required_on_create=False))
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
        logger = utils.set_logger(session_key, occ.USAGE_LOGFILE_PREFIX + input_name_short)

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
                "start_date": input_items.get("start_date", ""),
            }
            config.update(account_details)

            collector = UsageCollector(event_writer, config, logger, proxy)
            collector.collect_events()

            logger.info("Modular Input Exited.")

        except Exception as exc:
            logger.error("Error in input %s: %s", input_name, traceback.format_exc())


class UsageCollector:

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

        today = datetime.date.today()
        configured_start = self.config.get("start_date", "").strip()

        if state.get("last_fetched_date"):
            start_date = _parse_date(state["last_fetched_date"]) - datetime.timedelta(
                days=occ.REFETCH_DAYS - 1
            )
        elif configured_start:
            start_date = _parse_date(configured_start)
        else:
            start_date = today - datetime.timedelta(days=occ.DEFAULT_LOOKBACK_DAYS)

        if start_date > today:
            self.logger.info("Start date %s is in the future — nothing to collect.", start_date)
            return

        self.logger.info("Collecting usage data from %s to %s", start_date, today)
        total_events = 0
        current_start = start_date

        while current_start <= today:
            current_end = min(
                current_start + datetime.timedelta(days=occ.MAX_DAYS_PER_CALL - 1), today
            )
            start_ts = int(
                datetime.datetime.combine(current_start, datetime.time.min).timestamp()
            )
            # end_time is exclusive — add 1 day so current_end is included
            end_ts = int(
                datetime.datetime.combine(
                    current_end + datetime.timedelta(days=1), datetime.time.min
                ).timestamp()
            )

            params = {
                "start_time": start_ts,
                "end_time": end_ts,
                "bucket_width": "1d",
                "limit": occ.MAX_DAYS_PER_CALL,
            }

            try:
                response = self.client.get("/organization/usage/completions", params=params)
            except OpenAIAPIError as exc:
                self.logger.error(
                    "API error fetching usage [%s - %s]: %s", current_start, current_end, exc
                )
                return

            for bucket in response.get("data", []):
                bucket_time = bucket.get("start_time")
                for result in bucket.get("results", []):
                    record = {
                        "start_time": bucket_time,
                        "end_time": bucket.get("end_time"),
                    }
                    record.update(result)
                    event = smi.Event(
                        data=json.dumps(record),
                        time=bucket_time,
                        source="openai://usage/{}".format(self.config["input_name"]),
                        sourcetype=occ.USAGE_SOURCETYPE,
                        host=occ.OPENAI_HOST,
                        index=self.config["index"],
                    )
                    self.event_writer.write_event(event)
                    total_events += 1

            current_start = current_end + datetime.timedelta(days=1)

        state["last_fetched_date"] = today.isoformat()
        cp.save(state)
        self.logger.info("Completed usage collection: %d events written", total_events)


def _parse_date(date_str):
    return datetime.datetime.strptime(date_str.strip(), "%Y-%m-%d").date()


if __name__ == "__main__":
    exit_code = OpenAIUsageLogs().run(sys.argv)
    sys.exit(exit_code)
