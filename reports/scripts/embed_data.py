"""
Embed the JSON payloads from reports/scripts/prepare_data.py into the
two report HTML files' <script id="report-data"> placeholders.

Run after prepare_data.py, from the repo root:

    uv run python reports/scripts/embed_data.py
"""

from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPORTS_DIR / "data"

REPORTS = {
    "phenotypic_overview.html": "phenotypic_report_data.json",
    "buscar_reversal.html": "buscar_report_data.json",
    "pipeline_run.html": "pipeline_run_report_data.json",
}

PLACEHOLDER = "__DATA__"


def embed_one(html_name: str, json_name: str) -> None:
    html_path = REPORTS_DIR / html_name
    json_path = DATA_DIR / json_name
    html = html_path.read_text()
    payload = json_path.read_text()

    if PLACEHOLDER in html:
        updated = html.replace(PLACEHOLDER, payload)
    else:
        # Re-embedding after a previous run: replace the existing JSON
        # block between the report-data script tags.
        start_tag = '<script id="report-data" type="application/json">'
        end_tag = "</script>"
        start = html.index(start_tag) + len(start_tag)
        end = html.index(end_tag, start)
        updated = html[:start] + "\n" + payload + "\n" + html[end:]

    html_path.write_text(updated)
    print(f"embedded {len(payload)} bytes of {json_name} into {html_name}")


def main() -> None:
    for html_name, json_name in REPORTS.items():
        embed_one(html_name, json_name)


if __name__ == "__main__":
    main()
