#!/usr/bin/env python3
"""
Solar & Storage Energy Diagnostic
Analyzes raw energy data (ImpEP, ExpEP, TotalEP) for Solar (29, 31, 56) and Storage (30) meters.
Shows daily deltas to identify which columns the hardware uses for logging.
"""

import mysql.connector
import os
import webbrowser
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

START_DATE_STR = "2026-02-01"
DAYS_TO_ANALYZE = 7
END_DATE_STR = "2026-02-07"
METER_IDS = (29, 30, 31, 56)
METER_LABELS = {
    29: "Phase 1 PV",
    30: "Energy Storage",
    31: "Phase 2 PV",
    56: "Phase 3 PV",
}
REPORT_FILE = os.path.join(DATA_DIR, f"Solar-Energy-Diagnostic-{START_DATE_STR}.html")

db_config = {
    "host": "localhost",
    "port": 3307,
    "user": "energy_user",
    "password": "REDACTED",
    "database": "unified",
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Solar & Storage Energy Diagnostic</title>
    <meta charset="utf-8">
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; background-color: #f4f6f9; padding: 20px; color: #333; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .meta {{ color: #7f8c8d; font-size: 14px; margin-bottom: 25px; }}
        h2 {{ color: #34495e; margin-top: 30px; margin-bottom: 12px; font-size: 18px; border-left: 5px solid #27ae60; padding-left: 12px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 25px; }}
        th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #ddd; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        tr:hover {{ background-color: #f1f1f1; }}
        th.num, td.num {{ text-align: right; font-family: monospace; }}
        .highlight {{ font-weight: bold; color: #27ae60; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>☀️ Solar & Storage Energy Diagnostic</h1>
        <p class="meta">Period: <strong>{start_date}</strong> to <strong>{end_date}</strong> — Delta (Max − Min) for ImpEP, ExpEP, TotalEP</p>

        {meter_sections}
    </div>
</body>
</html>
"""

METER_SECTION_TEMPLATE = """
        <h2>Meter ID: {meter_id} ({label})</h2>
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th class="num">Delta ImpEP</th>
                    <th class="num">Delta ExpEP</th>
                    <th class="num">Delta TotalEP</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
"""


def fmt_val(v):
    """Format value for display; handle NaN."""
    if v != v:  # NaN
        return "N/A"
    return f"{v:.1f}"


def fetch_daily_deltas(conn, table_name, date_str):
    """Query MIN/MAX for ImpEP, ExpEP, TotalEP per meter for a single day."""
    query = f"""
        SELECT
            meter_number,
            MIN(ImpEP) AS min_imp,
            MAX(ImpEP) AS max_imp,
            MIN(ExpEP) AS min_exp,
            MAX(ExpEP) AS max_exp,
            MIN(TotalEP) AS min_total,
            MAX(TotalEP) AS max_total
        FROM {table_name}
        WHERE meter_number IN (29, 30, 31, 56)
        GROUP BY meter_number
    """
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()

    result = {}
    for row in rows:
        mid = row["meter_number"]
        result[mid] = {
            "delta_imp": (row["max_imp"] or 0) - (row["min_imp"] or 0),
            "delta_exp": (row["max_exp"] or 0) - (row["min_exp"] or 0),
            "delta_total": (row["max_total"] or 0) - (row["min_total"] or 0),
        }
    return result


def main():
    start_date = datetime.strptime(START_DATE_STR, "%Y-%m-%d")
    dates = [start_date + timedelta(days=i) for i in range(DAYS_TO_ANALYZE)]
    date_strs = [d.strftime("%Y-%m-%d") for d in dates]
    table_suffixes = [d.strftime("%y%m%d") for d in dates]

    # Structure: {meter_id: {date_str: {delta_imp, delta_exp, delta_total}}}
    all_data = {mid: {} for mid in METER_IDS}

    print("Connecting to database...")
    try:
        conn = mysql.connector.connect(**db_config)
    except mysql.connector.Error as err:
        print(f"Database error: {err}")
        return

    for i in range(DAYS_TO_ANALYZE):
        table_name = f"site_energy_{table_suffixes[i]}"
        date_str = date_strs[i]
        print(f"  [{i + 1}/{DAYS_TO_ANALYZE}] Querying {table_name}...")

        try:
            deltas = fetch_daily_deltas(conn, table_name, date_str)
            for mid in METER_IDS:
                all_data[mid][date_str] = deltas.get(
                    mid,
                    {"delta_imp": 0.0, "delta_exp": 0.0, "delta_total": 0.0},
                )
        except mysql.connector.Error as err:
            print(f"    Error: {err}")
            for mid in METER_IDS:
                all_data[mid][date_str] = {
                    "delta_imp": float("nan"),
                    "delta_exp": float("nan"),
                    "delta_total": float("nan"),
                }

    conn.close()

    # --- Build HTML Report ---
    meter_sections = []
    for mid in METER_IDS:
        label = METER_LABELS.get(mid, f"Meter {mid}")
        rows_html = ""
        for date_str in date_strs:
            d = all_data[mid][date_str]
            rows_html += f"""
                <tr>
                    <td>{date_str}</td>
                    <td class="num">{fmt_val(d["delta_imp"])}</td>
                    <td class="num">{fmt_val(d["delta_exp"])}</td>
                    <td class="num">{fmt_val(d["delta_total"])}</td>
                </tr>
            """
        meter_sections.append(
            METER_SECTION_TEMPLATE.format(
                meter_id=mid, label=label, rows=rows_html.strip()
            )
        )

    html = HTML_TEMPLATE.format(
        start_date=START_DATE_STR,
        end_date=END_DATE_STR,
        meter_sections="".join(meter_sections),
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print()
    print("=" * 62)
    print("Solar & Storage Energy Diagnostic (2026-02-01 to 2026-02-07)")
    print("=" * 62)
    print(f"Success! Report generated: {REPORT_FILE}")
    webbrowser.open("file://" + os.path.realpath(REPORT_FILE))


if __name__ == "__main__":
    main()
