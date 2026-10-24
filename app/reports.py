"""Approved report exports share one immutable analysis payload."""
from html import escape
import io
import json
import os
from pathlib import Path
import threading

from .database import digest

_font_lock=threading.Lock()


def report_font():
    """Embed a Unicode TrueType subset so viewers need no installed CJK font."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    candidates=[os.environ.get("REPORT_FONT_PATH",""),
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/System/Library/Fonts/STHeiti Medium.ttc"]
    with _font_lock:
        for candidate in candidates:
            if not candidate or not Path(candidate).is_file():continue
            name="MarketLensCJK_"+digest(candidate)[:12]
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name,candidate,subfontIndex=0))
            return name
        # Local development without an installed TrueType CJK font. Production
        # images install WenQuanYi and always take the embedded branch above.
        if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"


def report_sections(run):
    if run.get("status") != "approved" or not run.get("review"):
        raise ValueError("An approved, reviewed analysis is required for report export.")
    result = run["result"]
    actual_hash=digest(result)
    if actual_hash!=run["review"].get("result_sha256") or (run.get("result_sha256") and actual_hash!=run["result_sha256"]):
        raise ValueError("Approved report integrity check failed.")
    decision = result["decision"]
    action = decision.get("actions", {}).get(decision["choice"], {})
    sections = [("Market Lens", result["question"]),
                ("Recommendation", action.get("title", decision["choice"])),
                ("Rationale", result.get("rationale", "")),
                ("Review", f"{run['review']['reviewer']} · {run['review']['created_at']}\n{run['review'].get('notes', '')}"),
                ("Record", f"Run: {run['id']}\nModel: {result.get('model') or decision.get('model', '')}\nCreated: {run['created_at']}\nSHA256: {actual_hash}")]
    routing = result.get("local_routing")
    if routing:
        sections.append(("Local model routing",
                         f"Mode: Local {routing['profile'].title()}\nPlanning model: {routing['planner_model']}\n"
                         f"Selected analysis model: {routing['analysis_model']}\n{routing['reason']}"))
    for agent in result.get("agents", []):
        sections.append((agent["name"], "\n".join(c["text"] + " [" + ", ".join(c.get("evidence_ids", [])) + "]" for c in agent.get("claims", []))))
    for evidence in result.get("evidence", []):
        text = evidence.get("content") or (evidence.get("sql", "") + "\n" + json.dumps(evidence.get("rows", []), ensure_ascii=False, indent=2))
        sections.append(("Evidence " + evidence["id"],
                         f"{evidence.get('source', '')} {evidence.get('location', '')}\n{text}"))
    sections.append(("Limitations", "\n".join(result.get("warnings", []))))
    return sections


def html_report(run):
    sections = report_sections(run)
    blocks = "\n".join(f"<section><h2>{escape(title)}</h2><pre>{escape(text)}</pre></section>" for title, text in sections)
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Market Lens report</title><style>body{font:16px/1.6 system-ui;max-width:960px;margin:40px auto;padding:24px;color:#172b36}"
            "section{break-inside:avoid;border-bottom:1px solid #ddd;padding:12px 0}h2{font-size:19px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}"
            "@media print{body{margin:0;padding:0}section{break-inside:auto}}</style></head><body>" + blocks + "</body></html>")


def pdf_report(run):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    styles = getSampleStyleSheet()
    body = ParagraphStyle("CJK", fontName=report_font(), fontSize=10, leading=15, wordWrap="CJK", alignment=TA_LEFT)
    heading = ParagraphStyle("HeadingCJK", parent=body, fontSize=14, leading=20, textColor=colors.HexColor("#173e46"), spaceBefore=14, spaceAfter=8, keepWithNext=True)
    content = []
    for title, text in report_sections(run):
        content.append(Paragraph(escape(title), heading))
        # Split long paragraphs so oversized SQL results can flow across pages.
        for line in text.splitlines() or [""]:
            for offset in range(0, max(1, len(line)), 1500):
                content.append(Paragraph(escape(line[offset:offset+1500]) or "&#160;", body))
        content.append(Spacer(1, 8))
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, title="Market Lens approved report", leftMargin=42, rightMargin=42, topMargin=40, bottomMargin=40)
    def footer(canvas,document):
        canvas.saveState()
        canvas.setFont("Helvetica",8)
        canvas.setFillColor(colors.HexColor("#53636b"))
        canvas.drawString(42,22,"Market Lens | "+run["id"])
        canvas.drawRightString(document.pagesize[0]-42,22,"Page "+str(document.page))
        canvas.restoreState()
    doc.build(content,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()
