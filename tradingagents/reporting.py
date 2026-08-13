"""User-report writer.

The report boundary is intentionally separate from the Agent graph.  Raw
Agent discussions are retained in ``full_agent_log.md`` while the default
report is a concise synthesis for investors.
"""

from datetime import datetime
import html
from pathlib import Path
from typing import Any

import markdown

from tradingagents.report_synthesizer import ReportSynthesizer


def build_report_sections(
    final_state: dict[str, Any], *, include_debate_detail: bool = False
) -> list[tuple[str, str]]:
    """Return concise, localized user-visible sections.

    The legacy keyword remains accepted for callers, but raw debate is never
    inserted into the default report.  It belongs to the debug artifact.
    """
    del include_debate_detail
    return ReportSynthesizer().synthesize(final_state).sections


def write_report_tree(final_state: dict, ticker: str, save_path) -> Path:
    """Save ``complete_report.md`` and its independent full-Agent debug log."""
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)
    synthesized = ReportSynthesizer().synthesize(final_state)
    content = "\n\n".join(section for _title, section in synthesized.sections)
    header = f"# {ticker} 投资研究报告\n\n生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    output = save_path / "complete_report.md"
    output.write_text(header + content, encoding="utf-8")
    (save_path / "complete_report.html").write_text(
        _render_saved_html(ticker, final_state.get("trade_date"), synthesized.sections), encoding="utf-8"
    )
    (save_path / "full_agent_log.md").write_text(synthesized.full_agent_log, encoding="utf-8")
    return output


def _render_saved_html(ticker: str, trade_date: Any, sections: list[tuple[str, str]]) -> str:
    """Self-contained print HTML saved next to the Markdown report."""
    blocks = "\n".join(
        f'<section class="report-section">{markdown.markdown(content, extensions=["tables", "fenced_code"])}</section>'
        for _title, content in sections
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(ticker)} 投资研究报告</title>
<style>
@page {{ size:A4; margin:13mm 12mm; }}
body {{ font-family:"PingFang SC","Microsoft YaHei",sans-serif; color:#1f2937; line-height:1.62; max-width:820px; margin:0 auto; }}
h1 {{ font-size:22px; margin:24px 0 10px; }} h2 {{ font-size:17px; margin:18px 0 8px; }} h3 {{ font-size:15px; }}
.report-section {{ margin:0 0 22px; break-inside:avoid-page; }} .report-section:first-of-type {{ background:#f5f3ff; border:1px solid #ddd6fe; border-radius:10px; padding:14px; }}
.summary-cards {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }} .summary-card {{ border:1px solid #ddd6fe; border-radius:8px; padding:9px; }} .summary-card span {{ color:#6b7280; font-size:12px; display:block; }} .summary-card strong {{ font-size:14px; overflow-wrap:anywhere; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }} th,td {{ border:1px solid #e5e7eb; padding:6px 8px; text-align:left; }}
</style></head><body><header><h1>{html.escape(ticker)} 投资研究报告</h1><p>分析日期：{html.escape(str(trade_date or '数据未提供'))}</p></header>{blocks}</body></html>"""
