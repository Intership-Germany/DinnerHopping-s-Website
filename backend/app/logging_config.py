"""Central logging configuration for the DinnerHopping backend.

Usage: from .logging_config import configure_logging; configure_logging()

Writes structured key=value logs to stdout (suitable for Docker) and can be
extended later for JSON format or external aggregators.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler


class KeyValueFormatter(logging.Formatter):
    """Minimal key=value structured formatter.

    Example output:
        2025-09-24T12:00:00Z INFO payments event=payment.create status=pending rid=...
    """
    # Use RFC3339 / ISO8601 like: 2025-09-24T12:00:00.123+00:00
    default_time_format = "%Y-%m-%dT%H:%M:%S%z"

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # type: ignore[override]
        # Always format in UTC with explicit +00:00 offset.
        from datetime import datetime, timezone
        # use timezone-aware fromtimestamp
        dt = datetime.fromtimestamp(record.created, timezone.utc)
        # include milliseconds
        ms = int(record.msecs)
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}+00:00"

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # Ensure a timestamp field is always present
        record.asctime = self.formatTime(record, self.default_time_format)
        level = record.levelname
        logger_name = record.name
        # Capture extras we want surfaced: request_id, client_ip, path, method
        extras = []
        for key in ("request_id", "rid", "client_ip", "path", "method"):
            val = getattr(record, key, None)
            if val is not None:
                extras.append(f"{key}={val}")
        msg = super().format(record)
        extras_s = " " + " ".join(extras) if extras else ""
        return f"{record.asctime} {level} {logger_name} {msg}{extras_s}"


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes"}


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass


def _build_file_handler(base_dir: str, logger_name: str, json_mode: bool, rotate_when: str, rotate_param: str, backup: int):
    """Create a rotating file handler for a given logger name.

    rotate_when: 'size' or 'time'
    rotate_param:
        - size bytes (e.g. '5MB', '10M', '1048576')
        - time spec (e.g. 'midnight', 'H', 'D')
    backup: number of backups to keep
    """
    safe_name = logger_name.replace('.', '_') or 'root'
    log_dir = os.path.join(base_dir, safe_name)
    _ensure_dir(log_dir)
    # write files per-date to make log rotation by date easy to inspect
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    log_path = os.path.join(log_dir, f"{today}.log")
    if rotate_when == 'size':
        # parse size
        size_str = rotate_param.lower()
        multiplier = 1
        if size_str.endswith('mb'):
            multiplier = 1024 * 1024
            size_val = size_str[:-2]
        elif size_str.endswith('m'):
            multiplier = 1024 * 1024
            size_val = size_str[:-1]
        elif size_str.endswith('kb'):
            multiplier = 1024
            size_val = size_str[:-2]
        elif size_str.endswith('k'):
            multiplier = 1024
            size_val = size_str[:-1]
        else:
            size_val = size_str
        try:
            max_bytes = int(size_val) * multiplier
        except ValueError:
            max_bytes = 5 * 1024 * 1024  # default 5MB
        handler = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup, encoding='utf-8')
    else:
        # time rotation
        when = rotate_param or 'midnight'
        # Use TimedRotatingFileHandler but still write to date-named files; handler will rotate as configured
        handler = TimedRotatingFileHandler(log_path, when=when, backupCount=backup, encoding='utf-8', utc=True)
    # formatter assigned later centrally
    return handler


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root & app loggers idempotently.

    - LEVEL from LOG_LEVEL env (default INFO)
    - If LOG_JSON=true -> reserve hook for future JSON formatting (currently key=value)
    - Avoid duplicate handlers if already configured.
    """
    if getattr(configure_logging, "_configured", False):  # type: ignore[attr-defined]
        return

    log_level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    json_mode = _env_bool("LOG_JSON", False)
    to_files = _env_bool("LOG_TO_FILES", False)
    base_dir = os.getenv("LOG_DIR", "logs")
    rotate_when = os.getenv("LOG_ROTATE_MODE", "size").lower()  # 'size' or 'time'
    rotate_param = os.getenv("LOG_ROTATE_PARAM", "10MB")  # size or time spec
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", "7"))

    root = logging.getLogger()
    root.setLevel(log_level)

    # Clear existing handlers only if they were auto-added by basicConfig.
    if not getattr(root, "_dh_custom", False):
        for h in list(root.handlers):
            root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    # Attach a PII-masking filter to handler
    class PiiMaskFilter(logging.Filter):
        import re
        _email_re = re.compile(r"([a-zA-Z0-9_.+-]{1,3})[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")
        _phone_re = re.compile(r"(?<!\d)(\+?[0-9][0-9\-\s]{4,}[0-9])(?!\d)")

        def mask_email(self, s: str) -> str:
            return self._email_re.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", s)

        def mask_phone(self, s: str) -> str:
            return self._phone_re.sub(lambda m: f"***REDACTED_PHONE***", s)

        def filter(self, record: logging.LogRecord) -> bool:
            try:
                if record.msg and isinstance(record.msg, str):
                    s = record.msg
                    s = self.mask_email(s)
                    s = self.mask_phone(s)
                    record.msg = s
                # also mask any extra attributes that are strings
                for k, v in list(record.__dict__.items()):
                    if isinstance(v, str):
                        v2 = self.mask_email(v)
                        v2 = self.mask_phone(v2)
                        record.__dict__[k] = v2
            except Exception:
                # never fail logging because of the filter
                pass
            return True
    if json_mode:
        try:
            import json  # noqa: WPS433

            class JsonFormatter(logging.Formatter):  # type: ignore
                def format(self, record: logging.LogRecord) -> str:  # noqa: D401
                    # reuse KeyValueFormatter's time formatting behaviour
                    try:
                        ts = KeyValueFormatter().formatTime(record, KeyValueFormatter.default_time_format)
                    except Exception:
                        ts = self.formatTime(record, KeyValueFormatter.default_time_format)
                    base = {
                        "ts": ts,
                        "level": record.levelname,
                        "logger": record.name,
                        "message": record.getMessage(),
                    }
                    # Include extra attributes (simple scalars) for context
                    for k, v in record.__dict__.items():
                        if k.startswith('_') or k in {"args", "msg", "message", "exc_info", "exc_text", "stack_info", "lineno", "pathname", "filename", "module", "created", "msecs", "relativeCreated", "funcName", "thread", "threadName", "processName", "process"}:
                            continue
                        if isinstance(v, (str, int, float, bool)) or v is None:
                            base.setdefault(k, v)
                    # make sure common extras are present with canonical names
                    if hasattr(record, 'request_id') and record.request_id is not None:
                        base.setdefault('request_id', record.request_id)
                    if hasattr(record, 'client_ip') and record.client_ip is not None:
                        base.setdefault('client_ip', record.client_ip)
                    if record.exc_info:
                        base["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
                    return json.dumps(base, ensure_ascii=False)

            formatter: logging.Formatter = JsonFormatter()
        except Exception:  # noqa: BLE001
            formatter = KeyValueFormatter("%(message)s")
    else:
        formatter = KeyValueFormatter("%(message)s")
    handler.setFormatter(formatter)
    handler.addFilter(PiiMaskFilter())
    root.addHandler(handler)
    root._dh_custom = True  # type: ignore[attr-defined]

    # Reduce noisy third-party loggers default level
    for noisy in ["uvicorn", "httpx", "asyncio", "passlib"]:
        logging.getLogger(noisy).setLevel(os.getenv("NOISY_LOG_LEVEL", "WARNING").upper())

    # Convenience domain-specific loggers to ensure they exist
    domain_loggers = ["auth", "payments", "payments.paypal", "payments.stripe", "webhook", "request", "email"]
    for name in domain_loggers:
        lg = logging.getLogger(name)
        if to_files:
            # avoid adding duplicate file handlers
            if not any(isinstance(h, (RotatingFileHandler, TimedRotatingFileHandler)) for h in lg.handlers):
                fh = _build_file_handler(base_dir, name, json_mode, 'size' if rotate_when == 'size' else 'time', rotate_param, backup_count)
                fh.setFormatter(formatter)
                lg.addHandler(fh)
            lg.propagate = True  # still send to root stdout
    if to_files:
        # also root file handler
        if not any(isinstance(h, (RotatingFileHandler, TimedRotatingFileHandler)) for h in root.handlers):
            _ensure_dir(base_dir)
            root_fh = _build_file_handler(base_dir, 'root', json_mode, 'size' if rotate_when == 'size' else 'time', rotate_param, backup_count)
            root_fh.setFormatter(formatter)
            root.addHandler(root_fh)

    configure_logging._configured = True  # type: ignore[attr-defined]


__all__ = ["configure_logging"]
