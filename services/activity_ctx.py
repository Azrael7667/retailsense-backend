"""Tiny shared holder so the activity log can learn which user and shop a request really used."""
from contextvars import ContextVar

REQUEST_INFO: ContextVar = ContextVar("request_info", default=None)


def note(user_id=None, store_id=None):
    """Called by get_store_id. The holder is a dict made per request by the logging middleware."""
    info = REQUEST_INFO.get()
    if info is not None:
        if user_id:
            info["user_id"] = str(user_id)
        if store_id:
            info["store_id"] = str(store_id)
