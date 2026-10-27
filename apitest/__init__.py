"""apitest package: CampusClaw end-to-end API tests.

Run with::

    python apitest/run_all.py

which discovers all ``test_*.py`` modules, executes them with a single
in-memory SQLite database + a self-hosted MCP server running in a
background thread, and writes an HTML report to
``apitest/report.html`` (gitignored).
"""
