"""Dashboard: in-process Api (native window) + a dev-only browser server."""
from .api import READ_ENDPOINTS, Api
from .server import DashboardServer

__all__ = ["Api", "DashboardServer", "READ_ENDPOINTS"]
