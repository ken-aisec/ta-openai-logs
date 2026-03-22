SETTINGS_CONF_FILE = "ta_anthropic_logs_settings"
ACCOUNT_CONF_FILE = "ta_anthropic_logs_account"

USAGE_LOGFILE_PREFIX = "ta_anthropic_logs_usage_input_"
CLAUDE_CODE_LOGFILE_PREFIX = "ta_anthropic_logs_claude_code_input_"

KVSTORE_COLLECTION = "ta_anthropic_logs_checkpoints"

USAGE_SOURCETYPE = "anthropic:usage:logs"
CLAUDE_CODE_SOURCETYPE = "anthropic:usage:claude_code"

CONNECTION_ERROR = "log_connection_error"
SERVER_ERROR = "log_server_error"
GENERAL_EXCEPTION = "log_exception"

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_HOST = "api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

USAGE_ENDPOINT = "/organizations/usage_report/messages"
CLAUDE_CODE_ENDPOINT = "/organizations/usage_report/claude_code"

USAGE_BUCKET_WIDTH = "1h"
MAX_HOURS_PER_CALL = 168  # 7 days × 24 hours (API limit for 1h bucket_width)

DEFAULT_LOOKBACK_DAYS = 7
REFETCH_DAYS = 1
