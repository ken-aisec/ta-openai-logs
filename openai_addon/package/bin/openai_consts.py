SETTINGS_CONF_FILE = "ta_openai_logs_settings"
ACCOUNT_CONF_FILE = "ta_openai_logs_account"

USAGE_LOGFILE_PREFIX = "ta_openai_logs_usage_input_"
AUDIT_LOGFILE_PREFIX = "ta_openai_logs_audit_input_"

KVSTORE_COLLECTION = "ta_openai_logs_checkpoints"

USAGE_SOURCETYPE = "openai:usage:logs"
AUDIT_SOURCETYPE = "openai:audit:logs"

CONNECTION_ERROR = "log_connection_error"
SERVER_ERROR = "log_server_error"
GENERAL_EXCEPTION = "log_exception"

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_HOST = "api.openai.com"

DEFAULT_LOOKBACK_DAYS = 30
REFETCH_DAYS = 2
MAX_DAYS_PER_CALL = 31
AUDIT_PAGE_LIMIT = 100

USAGE_BUCKET_WIDTH = "1h"
MAX_BUCKETS_PER_CALL = 744  # 31 days × 24 hours
USAGE_ENDPOINTS = [
    "/organization/usage/completions",
    "/organization/usage/embeddings",
]
