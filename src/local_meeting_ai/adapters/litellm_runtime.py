"""Contain logging side effects when LiteLLM fails during lazy initialization."""

from __future__ import annotations

import importlib
import logging
import traceback
from threading import Lock
from types import ModuleType

from local_meeting_ai.domain.errors import CapabilityUnavailableError

_import_lock = Lock()
_sanitized_stamp = object()


class _FailedImportFilter(logging.Filter):
    """Keep diagnostics without exposing a record whose redaction has failed."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_meet2notes_litellm_sanitized", None) is _sanitized_stamp:
            return True
        message = "LiteLLM initialization failed; third-party log content withheld"
        if record.exc_info and record.exc_info[1] is not None:
            message += f"; exception type: {type(record.exc_info[1]).__name__}"
            # Frame locations help identify the original failure. Do not include
            # exception text, source lines, locals, arguments or record extras.
            for frame in traceback.extract_tb(record.exc_info[2]):
                message += f"\n  {frame.filename}:{frame.lineno} in {frame.name}"
        sanitized = logging.LogRecord(
            record.name, record.levelno, record.pathname, record.lineno,
            message, (), None,
        )
        record.__dict__.clear()
        record.__dict__.update(sanitized.__dict__)
        record._meet2notes_litellm_sanitized = _sanitized_stamp
        return True


def _logging_targets() -> list[logging.Logger | logging.Handler]:
    loggers = [logging.getLogger()] + [
        item for item in logging.Logger.manager.loggerDict.copy().values()
        if isinstance(item, logging.Logger)
    ]
    return list(dict.fromkeys([*loggers, *(h for lg in loggers for h in lg.handlers)]))


def load_litellm() -> ModuleType:
    """Serialize initialization and quarantine only newly installed failed filters.

    A failed Python import removes the parent module but may leave submodules
    and logging filters behind. Those filters must not mask asyncio's original
    exception or release unredacted content. Successful imports are untouched.
    """
    with _import_lock:
        previous = {target: tuple(target.filters) for target in _logging_targets()}
        try:
            return importlib.import_module("litellm")
        except Exception as error:
            fallback = _FailedImportFilter()
            for target in _logging_targets():
                for item in target.filters[:]:
                    if (
                        item not in previous.get(target, ())
                        and type(item).__module__.startswith("litellm.")
                    ):
                        position = target.filters.index(item)
                        target.filters[position] = fallback
            # The chained cause is kept for application traceback logging; the
            # user-facing message does not echo dependency errors or secrets.
            raise CapabilityUnavailableError(
                f"LiteLLM could not initialize ({type(error).__name__}). "
                "Check the application traceback and installed dependencies; "
                "no model/API request was sent."
            ) from error
