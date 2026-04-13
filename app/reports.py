import io
import csv
from datetime import date
from collections import defaultdict
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm


def format_amount(amount: float, currency: str) -> str:
    if currency == "INR":
        return f"\u20b9{amount:,.0f}"
    if currency == "USD":
        return f"${amount:,.2f}"
    if currency == "EUR":
        return f"\u20ac{amount:,.2f}"
    return f"{currency} {amount:,.2f}"


def format_amount_pdf(amount: float, currency: str) -> str:
    """ASCII-safe version for PDF (Helvetica doesn't support ₹ or €)."""
    if currency == "INR":
        return f"Rs.{amount:,.0f}"
    if currency == "USD":
        return f"${amount:,.2f}"
    if currency == "EUR":
        return f"EUR {amount:,.2f}"
    return f"{currency} {amount:,.2f}"


def generate_pdf_report(expenses: list[dict], user: dict, month: date) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Heading1"],
        fontSize=18, spaceAfter=4, textColor=colors.HexColor("#1a1a2e")
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontSize=9, textColor=colors.grey, spaceAfter=18
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontSize=11, spaceBefore=14, spaceAfter=6,
        textColor=colors.HexColor("#1a1a2e")
    )

    elements = []

    month_str = month.strftime("%B %Y")
    elements.append(Paragraph(f"Expense Report \u2014 {month_str}", title_style))
    elements.append(Paragraph(f"Account: {user['phone_number']}", subtitle_style))

    if not expenses:
        elements.append(Paragraph("No expenses recorded for this period.", styles["Normal"]))
        doc.build(elements)
        return buffer.getvalue()

    # Expenses table
    elements.append(Paragraph("All Expenses", section_style))

    header = ["Date", "Description", "Category", "Amount", "INR Equiv."]
    rows = [header]
    for e in expenses:
        d = date.fromisoformat(e["expense_date"])
        inr_eq = e.get("inr_equivalent")
        inr_cell = format_amount_pdf(inr_eq, "INR") if inr_eq else "-"
        rows.append([
            d.strftime("%d %b"),
            e["description"][:34],
            e["category"],
            format_amount_pdf(e["amount"], e["currency"]),
            inr_cell
        ])

    col_widths = [2.0 * cm, 7.5 * cm, 3.5 * cm, 2.8 * cm, 2.2 * cm]
    table = Table(rows, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")]),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dee2e6")),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("PADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 0.4 * cm))

    # Category summary
    elements.append(Paragraph("Summary by Category", section_style))

    by_category: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for e in expenses:
        by_category[e["category"]][e["currency"]] += e["amount"]

    summary_rows = [["Category", "Total"]]
    for category, currencies in sorted(by_category.items()):
        parts = [format_amount_pdf(amt, cur) for cur, amt in currencies.items()]
        summary_rows.append([category, "  +  ".join(parts)])

    summary_table = Table(summary_rows, colWidths=[8 * cm, 5 * cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dee2e6")),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 0.3 * cm))

    # Grand total
    totals: dict[str, float] = defaultdict(float)
    for e in expenses:
        totals[e["currency"]] += e["amount"]
    total_str = "  +  ".join(format_amount_pdf(amt, cur) for cur, amt in totals.items())
    elements.append(Paragraph(f"<b>Grand Total: {total_str}</b>", styles["Normal"]))
    elements.append(Spacer(1, 0.2 * cm))
    elements.append(Paragraph(
        f"<i>{len(expenses)} transaction{'s' if len(expenses) != 1 else ''} \u2014 Generated by WaEE</i>",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7, textColor=colors.grey)
    ))

    doc.build(elements)
    return buffer.getvalue()


def generate_csv_report(expenses: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Description", "Category", "Amount", "Currency"])
    for e in expenses:
        writer.writerow([
            e["expense_date"],
            e["description"],
            e["category"],
            e["amount"],
            e["currency"]
        ])
    return buffer.getvalue().encode("utf-8")
