"""PDF parts list (WeasyPrint): title block, indented list, open-items appendix.

Every page carries the footer "Generated BoM — review before release". The page is always printed
in light colours; the on-screen preview in the web app follows the user's theme.
"""

from __future__ import annotations

from html import escape
from typing import Any

from c2b_domain.bom import BomVersion
from c2b_exports.flatten import LABEL, ebom_rows, open_item_rows

_CSS = """
@page {
  size: A4 landscape;
  margin: 14mm 12mm 18mm 12mm;
  @bottom-left {
    content: "%(label)s";
    font: 600 8pt "DejaVu Sans", sans-serif;
    color: #b42318;
  }
  @bottom-right {
    content: "Page " counter(page) " of " counter(pages);
    font: 8pt "DejaVu Sans", sans-serif;
    color: #475467;
  }
}
html { font: 8.5pt/1.35 "DejaVu Sans", sans-serif; color: #101828; }
h1 { font-size: 16pt; margin: 0 0 2mm; }
h2 { font-size: 11pt; margin: 6mm 0 2mm; }
.title-block { border: 0.4mm solid #101828; border-collapse: collapse; width: 100%%; margin-bottom: 4mm; }
.title-block td { border: 0.2mm solid #98a2b3; padding: 1.2mm 2mm; vertical-align: top; }
.title-block .k { font-size: 7pt; color: #475467; text-transform: uppercase; }
.title-block .v { font-size: 10pt; font-weight: 600; }
table.parts { width: 100%%; border-collapse: collapse; }
table.parts thead { display: table-header-group; }
table.parts th { background: #1f3a5f; color: #fff; text-align: left; padding: 1.2mm 1.5mm; font-size: 8pt; }
table.parts td { border-bottom: 0.2mm solid #d0d5dd; padding: 1mm 1.5mm; vertical-align: top; }
table.parts tr { break-inside: avoid; }
td.num, th.num { text-align: right; }
.sub { color: #475467; }
.appendix { break-before: page; }
.items td { border-bottom: 0.2mm solid #d0d5dd; padding: 1mm 1.5mm; vertical-align: top; }
.items { width: 100%%; border-collapse: collapse; }
""" % {"label": LABEL}


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return ""
    return escape(str(value))


def build_html(version: BomVersion) -> str:
    product = version.product
    name = product.name if product else "Product"
    created = (version.created_at or "")[:10]
    rows = ebom_rows(version)

    body: list[str] = []
    body.append(f"<h1>Parts list &ndash; {escape(name)}</h1>")
    body.append('<table class="title-block"><tr>')
    for key, val in (
        ("Product", name),
        ("BoM version", f"v{version.version}"),
        ("Date", created),
        ("Library", version.library_version),
        ("Lines", f"{len(version.ebom)} EBOM / {len(version.mbom)} MBOM"),
    ):
        body.append(f'<td><div class="k">{escape(key)}</div><div class="v">{_fmt(val)}</div></td>')
    body.append("</tr></table>")

    body.append('<table class="parts"><thead><tr>')
    for h, cls in (
        ("Find", "num"),
        ("Part number", ""),
        ("Rev", ""),
        ("Description", ""),
        ("Qty", "num"),
        ("UoM", ""),
        ("Material", ""),
        ("Mass (g)", "num"),
        ("Make / buy", ""),
    ):
        body.append(f'<th class="{cls}">{escape(h)}</th>')
    body.append("</tr></thead><tbody>")
    for r in rows:
        pad = 4 * int(r["depth"])
        body.append(
            "<tr>"
            f'<td class="num">{_fmt(r["find_no"])}</td>'
            f"<td>{_fmt(r['part_number'])}</td>"
            f"<td>{_fmt(r['revision'])}</td>"
            f'<td style="padding-left:{1.5 + pad}mm">{_fmt(r["description"])}</td>'
            f'<td class="num">{_fmt(r["qty"])}</td>'
            f"<td>{_fmt(r['uom'])}</td>"
            f"<td>{_fmt(r['material'])}</td>"
            f'<td class="num">{_fmt(r["mass_g"])}</td>'
            f"<td>{_fmt(r['make_buy'])}</td>"
            "</tr>"
        )
    body.append("</tbody></table>")

    items = open_item_rows(version)
    body.append('<div class="appendix"><h2>Appendix &ndash; open items</h2>')
    if items:
        body.append('<table class="items">')
        for it in items:
            body.append(
                f"<tr><td>{_fmt(it['kind'])}</td><td>{_fmt(it['ref'])}</td>"
                f"<td>{_fmt(it['text'])}</td></tr>"
            )
        body.append("</table>")
    else:
        body.append('<p class="sub">No open items.</p>')
    body.append("</div>")

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Parts list - {escape(name)} v{version.version}</title>"
        f"<style>{_CSS}</style></head><body>{''.join(body)}</body></html>"
    )


def build_pdf(version: BomVersion) -> bytes:
    from weasyprint import HTML  # imported here so the other formats work without pango

    return bytes(HTML(string=build_html(version)).write_pdf())
