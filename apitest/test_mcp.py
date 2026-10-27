"""MCP integration tests (registration / discovery / skill binding / audit).

Covers the six acceptance areas from the assignment:
  1. mcp-server standalone: /health, tools/list (Bearer), tools/call
  2. Host-side: register, connect (discover tools), disconnect, 403 for students
  3. Skill binding validation: MCP tool must be in-class, connected, discovered
  4. End-to-end: connect MCP → skill with formula_hint@campus-tools → ask → audit ok
  5. Disconnect path: same skill no longer succeeds (denied)
  6. Token hygiene: server list responses never carry auth_token
"""

import json
import unittest

import requests

from _fixtures import (
    BaseMCPTestCase,
    MCP_BASE_URL,
    MCP_SERVER_NAME,
    MCP_TOKEN,
)


class TestMCPServerStandalone(BaseMCPTestCase):
    """Direct hits against the self-hosted MCP server."""

    def test_health(self):
        resp = requests.get(f"{MCP_BASE_URL}/health", timeout=5)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_tools_list_requires_token(self):
        resp = requests.post(
            f"{MCP_BASE_URL}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            timeout=5,
        )
        self.assertEqual(resp.status_code, 401)

    def test_tools_list_returns_tools(self):
        resp = requests.post(
            f"{MCP_BASE_URL}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            timeout=5,
        )
        self.assertEqual(resp.status_code, 200)
        tools = resp.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("formula_hint", names)
        self.assertIn("unit_convert", names)

    def test_tools_call_returns_text(self):
        resp = requests.post(
            f"{MCP_BASE_URL}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "formula_hint", "arguments": {"topic": "二次函数"}},
            },
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            timeout=5,
        )
        self.assertEqual(resp.status_code, 200)
        content = resp.json()["result"]["content"]
        text = "".join(c["text"] for c in content if c.get("type") == "text")
        self.assertIn("二次函数", text)


class TestMCPHostManagement(BaseMCPTestCase):
    """Host-side registration / connect / disconnect / tool listing."""

    def test_student_cannot_register(self):
        token = self.student_token()
        resp = self.client.post(
            "/api/mcp/servers",
            json={
                "name": "campus-tools",
                "base_url": MCP_BASE_URL,
                "auth_token": MCP_TOKEN,
            },
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_register_connect_discover_disconnect(self):
        token = self.teacher_token()
        # 1. Register
        resp = self.client.post(
            "/api/mcp/servers",
            json={
                "name": MCP_SERVER_NAME,
                "base_url": MCP_BASE_URL,
                "auth_token": MCP_TOKEN,
            },
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 201)
        server = resp.get_json()["server"]
        self.assertNotIn("auth_token", server)  # token never returned
        server_id = server["id"]

        # 2. Connect + discover
        resp = self.client.post(
            f"/api/mcp/servers/{server_id}/connect",
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        self.assertTrue(data["server"]["connected"])
        tool_names = {t["name"] for t in data["tools"]}
        self.assertIn("formula_hint", tool_names)
        self.assertIn("unit_convert", tool_names)

        # 3. List tools endpoint
        resp = self.client.get(
            f"/api/mcp/servers/{server_id}/tools",
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(len(resp.get_json()["tools"]) >= 2)

        # 4. Disconnect
        resp = self.client.post(
            f"/api/mcp/servers/{server_id}/disconnect",
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["server"]["connected"])

        # After disconnect the cached tool list is empty.
        resp = self.client.get(
            f"/api/mcp/servers/{server_id}/tools",
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.get_json()["tools"], [])

    def test_register_response_strips_token(self):
        token = self.teacher_token()
        resp = self.client.post(
            "/api/mcp/servers",
            json={
                "name": "no-leak-server",
                "base_url": MCP_BASE_URL,
                "auth_token": MCP_TOKEN,
            },
            headers=self.auth_headers(token),
        )
        body = resp.get_data(as_text=True)
        self.assertNotIn(MCP_TOKEN, body)

    def test_connect_to_nonexistent_server_404(self):
        token = self.teacher_token()
        resp = self.client.post(
            "/api/mcp/servers/99999/connect",
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 404)


class TestMCPSkillBinding(BaseMCPTestCase):
    """Skill ``tools`` validation and the end-to-end call path."""

    def _connect_server(self, token):
        resp = self.client.post(
            "/api/mcp/servers",
            json={
                "name": MCP_SERVER_NAME,
                "base_url": MCP_BASE_URL,
                "auth_token": MCP_TOKEN,
            },
            headers=self.auth_headers(token),
        )
        if resp.status_code != 201:
            # Already registered from another test; find it.
            resp = self.client.get(
                "/api/mcp/servers", headers=self.auth_headers(token)
            )
            server = next(
                s for s in resp.get_json()["servers"]
                if s["name"] == MCP_SERVER_NAME
            )
        else:
            server = resp.get_json()["server"]
        sid = server["id"]
        resp = self.client.post(
            f"/api/mcp/servers/{sid}/connect",
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        return sid

    def test_save_skill_with_unknown_mcp_tool_rejected(self):
        token = self.teacher_token()
        resp = self.client.post(
            "/api/skills",
            json={
                "name": "坏技能",
                "triggers": "测试",
                "instruction": "x",
                "tools": ["formula_hint@nonexistent-server"],
            },
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_save_skill_with_disconnected_tool_rejected(self):
        token = self.teacher_token()
        # Register but do NOT connect.
        resp = self.client.post(
            "/api/mcp/servers",
            json={
                "name": "offline-server",
                "base_url": MCP_BASE_URL,
                "auth_token": MCP_TOKEN,
            },
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 201)
        resp = self.client.post(
            "/api/skills",
            json={
                "name": "未连接技能",
                "triggers": "测试",
                "instruction": "x",
                "tools": ["formula_hint@offline-server"],
            },
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_end_to_end_formula_hint_audit_ok(self):
        """Connect MCP → skill with formula_hint@campus-tools → ask → audit ok."""
        token = self.teacher_token()
        self._connect_server(token)

        # Reuse the skill if a prior test already created it, else create.
        resp = self.client.get("/api/skills", headers=self.auth_headers(token))
        skill = next(
            (s for s in resp.get_json()["skills"] if s["name"] == "公式提示技能"),
            None,
        )
        if skill is None:
            resp = self.client.post(
                "/api/skills",
                json={
                    "name": "公式提示技能",
                    "triggers": "公式,提示,formula",
                    "instruction": "调用公式提示工具后给学生讲解。",
                    "tools": ["formula_hint@campus-tools"],
                },
                headers=self.auth_headers(token),
            )
            self.assertEqual(resp.status_code, 201, resp.data)
            skill = resp.get_json()["skill"]
        skill_id = skill["id"]

        # Bind the skill to the class's default agent.
        resp = self.client.get(
            "/api/agents", headers=self.auth_headers(token)
        )
        agent_id = resp.get_json()["agents"][0]["id"]
        resp = self.client.post(
            f"/api/agents/{agent_id}/update",
            json={"skill_ids": [skill_id]},
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 200)

        # Mock the LLM so the ask path completes without an upstream key.
        self.mock_llm("已根据公式提示给出思路。")

        # Open a conversation and ask a question that hits the skill.
        resp = self.client.post(
            "/api/conversations",
            json={"agent_id": agent_id},
            headers=self.auth_headers(token),
        )
        conv_id = resp.get_json()["conversation"]["id"]

        resp = self.client.post(
            f"/api/conversations/{conv_id}/ask",
            json={"question": "二次函数的公式是什么，给我点提示"},
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        events = self.parse_sse_events(resp.data)
        meta = next(e for e in events if e["event"] == "meta")["data"]
        tool_calls = meta["tool_calls"]
        self.assertTrue(any(
            tc["tool"] == "formula_hint@campus-tools" and tc["status"] == "ok"
            for tc in tool_calls
        ))

        # Audit table must contain an ok record for this tool.
        resp = self.client.get(
            "/api/mcp/audit", headers=self.auth_headers(token)
        )
        audit = resp.get_json()["audit"]
        self.assertTrue(any(
            a["tool_ref"] == "formula_hint@campus-tools" and a["status"] == "ok"
            for a in audit
        ))

        self.unmock_llm()

    def test_disconnect_then_skill_call_denied(self):
        """After disconnect, an ask that hits the skill records a denied call."""
        token = self.teacher_token()
        server_id = self._connect_server(token)

        # Reuse the skill from the previous test if present, else create.
        resp = self.client.get("/api/skills", headers=self.auth_headers(token))
        skill = next(
            (s for s in resp.get_json()["skills"]
             if s["name"] == "公式提示技能"),
            None,
        )
        if skill is None:
            resp = self.client.post(
                "/api/skills",
                json={
                    "name": "公式提示技能",
                    "triggers": "公式,提示,formula",
                    "instruction": "调用公式提示工具。",
                    "tools": ["formula_hint@campus-tools"],
                },
                headers=self.auth_headers(token),
            )
            self.assertEqual(resp.status_code, 201)
            skill = resp.get_json()["skill"]
        skill_id = skill["id"]

        resp = self.client.get("/api/agents", headers=self.auth_headers(token))
        agent_id = resp.get_json()["agents"][0]["id"]
        self.client.post(
            f"/api/agents/{agent_id}/update",
            json={"skill_ids": [skill_id]},
            headers=self.auth_headers(token),
        )

        # Disconnect the server.
        self.client.post(
            f"/api/mcp/servers/{server_id}/disconnect",
            headers=self.auth_headers(token),
        )

        self.mock_llm("回答")
        resp = self.client.post(
            "/api/conversations",
            json={"agent_id": agent_id},
            headers=self.auth_headers(token),
        )
        conv_id = resp.get_json()["conversation"]["id"]

        resp = self.client.post(
            f"/api/conversations/{conv_id}/ask",
            json={"question": "给我点二次函数公式的提示"},
            headers=self.auth_headers(token),
        )
        self.assertEqual(resp.status_code, 200)
        events = self.parse_sse_events(resp.data)
        meta = next(e for e in events if e["event"] == "meta")["data"]
        self.assertTrue(any(
            tc["tool"] == "formula_hint@campus-tools" and tc["status"] == "denied"
            for tc in meta["tool_calls"]
        ))

        # And the audit records the denied call.
        resp = self.client.get("/api/mcp/audit", headers=self.auth_headers(token))
        audit = resp.get_json()["audit"]
        self.assertTrue(any(
            a["tool_ref"] == "formula_hint@campus-tools" and a["status"] == "denied"
            for a in audit
        ))
        self.unmock_llm()


if __name__ == "__main__":
    unittest.main()
