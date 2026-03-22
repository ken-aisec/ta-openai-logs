"""
anthropic_utils.py — Shared utilities for Anthropic TA inputs

Provides credential retrieval, proxy settings, and logging setup
using solnlib, following the same patterns as the OpenAI TA.
"""

import sys
import traceback

import requests
from solnlib import conf_manager, log

import anthropic_consts as acc

APP_NAME = "ta_anthropic_logs"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def set_logger(session_key, filename):
    """Return a configured logger for the given log filename."""
    logger = log.Logs().get_logger(filename)
    log_level = conf_manager.get_log_level(
        logger=logger,
        session_key=session_key,
        app_name=APP_NAME,
        conf_name=acc.SETTINGS_CONF_FILE,
        default_log_level="INFO",
    )
    logger.setLevel(log_level)
    return logger


# ---------------------------------------------------------------------------
# Conf reading
# ---------------------------------------------------------------------------

def _get_conf(session_key, logger, conf_filename):
    """Read all stanzas from a UCC-managed conf file (handles encrypted fields)."""
    try:
        cfm = conf_manager.ConfManager(
            session_key,
            APP_NAME,
            realm="__REST_CREDENTIAL__#{}#configs/conf-{}".format(APP_NAME, conf_filename),
        )
        return cfm.get_conf(conf_filename).get_all()
    except conf_manager.ConfManagerException as exc:
        logger.error("ConfManagerException reading %s: %s", conf_filename, traceback.format_exc())
        return {}
    except Exception as exc:
        logger.error("Failed to read conf %s: %s", conf_filename, traceback.format_exc())
        sys.exit(1)


# ---------------------------------------------------------------------------
# Account / credential retrieval
# ---------------------------------------------------------------------------

def get_account_details(session_key, logger, account_name):
    """
    Return a dict with api_key and workspace_id for the named account.

    UCC stores the account tab fields in ta_anthropic_logs_account.conf.
    The `password` field holds the encrypted Admin API key (sk-ant-admin-...).
    The `username` field holds the optional Workspace ID.
    """
    account_conf = _get_conf(session_key, logger, acc.ACCOUNT_CONF_FILE)
    if not account_conf:
        logger.error("No accounts found in %s", acc.ACCOUNT_CONF_FILE)
        sys.exit(1)

    stanza = account_conf.get(account_name)
    if not stanza:
        logger.error("Account '%s' not found in %s", account_name, acc.ACCOUNT_CONF_FILE)
        sys.exit(1)

    return {
        "api_key": stanza.get("password", ""),
        "workspace_id": stanza.get("username", "") or None,
    }


# ---------------------------------------------------------------------------
# Proxy settings
# ---------------------------------------------------------------------------

def get_proxy_settings(session_key, logger):
    """
    Return a requests-compatible proxy dict or None if proxy is disabled.

    Reads from ta_anthropic_logs_settings.conf [proxy] stanza.
    """
    try:
        settings_conf = _get_conf(session_key, logger, acc.SETTINGS_CONF_FILE)
        proxy_stanza = settings_conf.get("proxy", {})

        if not int(proxy_stanza.get("proxy_enabled") or 0):
            logger.debug("Proxy disabled.")
            return None

        proxy_type = proxy_stanza.get("proxy_type", "http")
        proxy_url = proxy_stanza.get("proxy_url", "")
        proxy_port = proxy_stanza.get("proxy_port", "")
        proxy_username = proxy_stanza.get("proxy_username", "")
        proxy_password = proxy_stanza.get("proxy_password", "")

        if not proxy_url or not proxy_port:
            logger.warning("Proxy enabled but host/port missing — skipping proxy.")
            return None

        # socks5 in requests requires the socks5h scheme for remote DNS
        if proxy_type == "socks5":
            proxy_type = "socks5h"

        if proxy_username and proxy_password:
            proxy_username = requests.compat.quote_plus(proxy_username)
            proxy_password = requests.compat.quote_plus(proxy_password)
            proxy_uri = "%s://%s:%s@%s:%s" % (
                proxy_type, proxy_username, proxy_password, proxy_url, proxy_port
            )
        else:
            proxy_uri = "%s://%s:%s" % (proxy_type, proxy_url, proxy_port)

        logger.debug("Proxy configured: %s://%s:%s", proxy_type, proxy_url, proxy_port)
        return {"http": proxy_uri, "https": proxy_uri}

    except Exception as exc:
        logger.error("Failed to read proxy settings: %s", traceback.format_exc())
        sys.exit(1)
