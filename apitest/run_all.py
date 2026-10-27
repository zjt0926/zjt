"""Run the apitest suite and emit an HTML report.

Usage::

    python apitest/run_all.py

Discovers every ``test_*.py`` under ``apitest/``, runs them with the
standard ``unittest`` runner, and writes a self-contained HTML report to
``apitest/report.html``. The report is gitignored (see .gitignore).
"""

import datetime
import io
import os
import sys
import unittest
from html import escape

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPORT_PATH = os.path.join(_HERE, "report.html")


class _HTMLResult(unittest.TestResult):
    """Collect successes / failures / errors for HTML rendering."""

    def __init__(self):
        super().__init__(stream=io.StringIO(), descriptions=True, verbosity=2)
        self.successes: list[tuple] = []
        self.failures: list[tuple] = []
        self.errors: list[tuple] = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append((test, ""))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.failures.append((test, self._exc_info_to_string(err, test)))

    def addError(self, test, err):
        super().addError(test, err)
        self.errors.append((test, self._exc_info_to_string(err, test)))


def _row(test, status, detail, color):
    name = test.id() if hasattr(test, "id") else str(test)
    return (
        f"<tr><td>{escape(name)}</td>"
        f"<td style='color:{color};font-weight:600'>{status}</td>"
        f"<td><pre style='margin:0;white-space:pre-wrap;font-size:12px;'>"
        f"{escape(detail)}</pre></td></tr>"
    )


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(_HERE, pattern="test_*.py")

    result = _HTMLResult()
    start = datetime.datetime.now()
    suite.run(result)
    elapsed = (datetime.datetime.now() - start).total_seconds()

    total = result.testsRun
    ok = len(result.successes)
    fail = len(result.failures)
    err = len(result.errors)

    rows = []
    for test, detail in result.successes:
        rows.append(_row(test, "OK", detail or "passed", "#2e7d32"))
    for test, detail in result.failures:
        rows.append(_row(test, "FAIL", detail, "#e65100"))
    for test, detail in result.errors:
        rows.append(_row(test, "ERROR", detail, "#94070a"))

    overall = "PASS" if (fail + err) == 0 else "FAIL"
    overall_color = "#2e7d32" if overall == "PASS" else "#94070a"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>CampusClaw apitest 报告</title>
<style>
body{{font-family:"Microsoft YaHei",Arial,sans-serif;margin:24px;color:#333}}
h1{{font-size:20px}}
.summary{{margin:16px 0;font-size:15px}}
.badge{{display:inline-block;padding:2px 12px;border-radius:12px;color:#fff;
  background:{overall_color};font-weight:600}}
table{{border-collapse:collapse;width:100%;margin-top:16px;font-size:13px}}
th,td{{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}
th{{background:#94070a;color:#fff}}
tr:nth-child(even){{background:#fafafa}}
pre{{max-height:240px;overflow:auto}}
</style></head><body>
<h1>CampusClaw apitest 报告</h1>
<div class="summary">
  <span class="badge">{overall}</span>
  共 {total} 项 · 通过 {ok} · 失败 {fail} · 错误 {err} ·
  用时 {elapsed:.2f}s · {start.strftime("%Y-%m-%d %H:%M:%S")}
</div>
<table>
<thead><tr><th>用例</th><th>结果</th><th>详情</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</body></html>"""

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Ran {total} tests in {elapsed:.2f}s — {overall} "
          f"(ok={ok} fail={fail} err={err})")
    print(f"Report written to {REPORT_PATH}")
    return 0 if (fail + err) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
