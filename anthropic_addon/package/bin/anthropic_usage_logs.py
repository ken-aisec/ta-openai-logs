"""
anthropic_usage_logs.py — Modular input for Anthropic Usage Logs

Polls /organizations/usage_report/messages for hourly token-usage buckets
covering all model types (completions, embeddings, etc.).
Uses date-based checkpointing; re-fetches last REFETCH_DAYS to catch
late-arriving records.

Sourcetype: anthropic:usage:logs
"""

import sys
import json
import datetime
import traceback

import import_declare_test  # noqa — must be first to fix sys.path

from splunklib import modularinput as smi

import anthropic_utils as utils
import anthropic_consts as acc
from anthropic_api_client import AnthropicClient, AnthropicAPIError
from anthropic_checkpoint import CheckpointManager

APP_NAME = "ta_anthropic_logs"


class AnthropicUsageLogs(smi.Script):

    def __init__(self):
        super().__init__()

    def get_scheme(self):
        scheme = smi.Scheme("anthropic_usage_logs")
        scheme.description = "Anthropic Usage Logs"
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
        import anthropic_utils as utils
        session_key = definition.metadata.get("session_key", "")
        logger = logging.getLogger(__name__)
        utils.get_account_details(session_key, logger, account_name)

    def stream_events(self, inputs, event_writer):
        session_key = self._input_definition.metadata["session_key"]
        checkpoint_dir = inputs.metadata.get("checkpoint_dir", "")

        for input_name, input_items in inputs.inputs.items():
            input_items["input_name"] = input_name

        input_name_short = input_name.split("://")[-1]
        logger = utils.set_logger(session_key, acc.USAGE_LOGFILE_PREFIX + input_name_short)

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
        self.client = AnthropicClient(
            api_key=config["api_key"],
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
                days=acc.REFETCH_DAYS - 1
            )
        elif configured_start:
            start_date = _parse_date(configured_start)
        else:
            start_date = today - datetime.timedelta(days=acc.DEFAULT_LOOKBACK_DAYS)

        if start_date > today:
            self.logger.info("Start date %s is in the future — nothing to collect.", start_date)
            return

        self.logger.info("Collecting usage data from %s to %s", start_date, today)
        total_events = 0
        current_start = start_date

        # Chunk in 7-day windows (168 hourly buckets max per API call)
        chunk_days = acc.MAX_HOURS_PER_CALL // 24

        while current_start <= today:
            current_end = min(
                current_start + datetime.timedelta(days=chunk_days - 1), today
            )

            # Anthropic uses RFC 3339 timestamps for API params
            starting_at = datetime.datetime.combine(
                current_start, datetime.time.min
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            # ending_at is exclusive — add 1 day so current_end is included
            ending_at = datetime.datetime.combine(
                current_end + datetime.timedelta(days=1), datetime.time.min
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            params = {
                "starting_at": starting_at,
                "ending_at": ending_at,
                "bucket_width": acc.USAGE_BUCKET_WIDTH,
                "limit": acc.MAX_HOURS_PER_CALL,
            }

            try:
                for page in self.client.paginate_usage(acc.USAGE_ENDPOINT, params=params):
                    for bucket in page.get("data", []):
                        bucket_time_str = bucket.get("start_time")
                        bucket_ts = _rfc3339_to_unix(bucket_time_str) if bucket_time_str else None

                        for result in bucket.get("results", []):
                            record = {
                                "start_time": bucket_time_str,
                                "end_time": bucket.get("end_time"),
                            }
                            record.update(result)
                            event = smi.Event(
                                data=json.dumps(record),
                                time=bucket_ts,
                                source="anthropic://usage/{}".format(self.config["input_name"]),
                                sourcetype=acc.USAGE_SOURCETYPE,
                                host=acc.ANTHROPIC_HOST,
                                index=self.config["index"],
                            )
                            self.event_writer.write_event(event)
                            total_events += 1

            except AnthropicAPIError as exc:
                self.logger.error(
                    "API error fetching usage [%s - %s]: %s", current_start, current_end, exc
                )

            current_start = current_end + datetime.timedelta(days=1)

        state["last_fetched_date"] = today.isoformat()
        cp.save(state)
        self.logger.info("Completed usage collection: %d events written", total_events)


def _parse_date(date_str):
    return datetime.datetime.strptime(date_str.strip(), "%Y-%m-%d").date()


def _rfc3339_to_unix(rfc3339_str):
    """Convert RFC 3339 string (e.g. '2026-03-22T00:00:00Z') to Unix timestamp."""
    try:
        dt = datetime.datetime.strptime(rfc3339_str, "%Y-%m-%dT%H:%M:%SZ")
        epoch = datetime.datetime(1970, 1, 1)
        return int((dt - epoch).total_seconds())
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    exit_code = AnthropicUsageLogs().run(sys.argv)
    sys.exit(exit_code)
