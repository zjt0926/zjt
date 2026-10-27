"""Shared test fixtures for the apitest suite.

Provides:
  * ``BaseMCPTestCase`` — spins up a self-hosted MCP server in a daemon
    thread on a free port, creates a fresh in-memory-file CampusClaw app
    with the seed data, and exposes helper methods to log in as the
    seeded teacher / student and to mock the LLM.

The MCP server is started once per test process (module level) so every
test case shares the same ``campus-tools`` demo server.
"""

import importlib.util
import os
import sys
import threading
import time
import uuid
import unittest
from typing import Optional

from werkzeug.serving import make_server

# ---------------------------------------------------------------------------
# Project root + make the main ``app`` package importable.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# MCP server (loaded from mcp-server/app.py under an alias).
# ---------------------------------------------------------------------------
_MCP_PORT = 18090
_MCP_TOKEN = "test-mcp-token-" + uuid.uuid4().hex[:8]

os.environ["MCP_AUTH_TOKEN"] = _MCP_TOKEN
os.environ["MCP_SERVER_NAME"] = "campus-tools"
MCP_TOKEN = _MCP_TOKEN

_spec = importlib.util.spec_from_file_location(
    "mcp_server_app",
    os.path.join(_PROJECT_ROOT, "mcp-server", "app.py"),
)
mcp_server_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_server_app)

_mcp_server = make_server("127.0.0.1", _MCP_PORT, mcp_server_app.app)
_mcp_thread = threading.Thread(target=_mcp_server.serve_forever, daemon=True)
_mcp_thread.start()
# Give the server a moment to bind.
time.sleep(0.3)

MCP_BASE_URL = f"http://127.0.0.1:{_MCP_PORT}"
MCP_SERVER_NAME = "campus-tools"


# ---------------------------------------------------------------------------
# CampusClaw app with an isolated test database.
# ---------------------------------------------------------------------------

def _make_test_app():
    """Create a CampusClaw app backed by a fresh temp SQLite file."""
    db_path = os.path.join(
        _PROJECT_ROOT, "data", f"test_{uuid.uuid4().hex}.db"
    )
    # Ensure a clean slate.
    if os.path.exists(db_path):
        os.remove(db_path)

    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ["DATABASE_PATH"] = db_path
    os.environ["UPLOAD_FOLDER"] = os.path.join(_PROJECT_ROOT, "uploads")
    os.environ["VECTOR_DB_PATH"] = os.path.join(
        _PROJECT_ROOT, "data", f"chroma_{uuid.uuid4().hex}"
    )
    os.environ.setdefault("LLM_BASE_URL", "")
    os.environ.setdefault("LLM_API_KEY", "")
    os.environ.setdefault("LLM_MODEL", "")

    from app import create_app

    return create_app(), db_path


class BaseMCPTestCase(unittest.TestCase):
    """Base test case providing the test client, auth helpers and LLM mocking."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app, cls.db_path = _make_test_app()
        cls.client = cls.app.test_client()
    # -- auth helpers -----------------------------------------------------

    def login(self, username: str, password: str) -> str:
        resp = self.client.post(
            "/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200, f"login failed for {username}: {resp.data}"
        return resp.get_json()["token"]

    def auth_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def teacher_token(self) -> str:
        return self.login("teacher_a", "teacherpass")

    def student_token(self) -> str:
        return self.login("student_a1", "studentpass")

    # -- LLM mocking ------------------------------------------------------

    def mock_llm(self, response_text: str = "这是测试回答"):
        """Patch llm so is_configured() is True and stream_chat yields text.

        Registers an automatic cleanup so the original functions are
        restored even if the test fails midway.
        """
        import app.llm as llm_mod

        orig_is_configured = llm_mod.is_configured
        orig_stream_chat = llm_mod.stream_chat
        llm_mod.is_configured = lambda config=None: True

        def fake_stream(messages, config=None, timeout=None):
            yield response_text

        llm_mod.stream_chat = fake_stream
        self.addCleanup(self._restore_llm, orig_is_configured, orig_stream_chat)

    @staticmethod
    def _restore_llm(orig_is_configured, orig_stream_chat):
        import app.llm as llm_mod

        llm_mod.is_configured = orig_is_configured
        llm_mod.stream_chat = orig_stream_chat

    def unmock_llm(self):
        # Kept for explicit-call compatibility; cleanup handles restoration.
        pass

    # -- SSE parsing ------------------------------------------------------

    @staticmethod
    def parse_sse_events(data: bytes) -> list[dict]:
        """Parse a text/event-stream body into a list of {event, data}."""
        events = []
        current_event = None
        current_data = []
        for line in data.decode("utf-8").splitlines():
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                current_data.append(line[len("data:"):].strip())
            elif line == "":
                if current_event is not None:
                    import json

                    payload = (
                        json.loads("".join(current_data))
                        if current_data
                        else None
                    )
                    events.append({"event": current_event, "data": payload})
                    current_event = None
                    current_data = []
        return events
