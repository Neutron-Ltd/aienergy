import mysql.connector
import pandas as pd
import warnings
import sys
import webbrowser
import os
import re

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
TARGET_DATE = "260203"       # Feb 3rd, 2026
REPORT_FILE = f"Frequency-20{TARGET_DATE}.html"
EXPECTED_FREQ_MIN = 15       # Expected interval in minutes
EXPECTED_COUNT = int((24 * 60) / EXPECTED_FREQ_MIN) # 96 records
# ---------------------

db_config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

# --- HTML CSS & TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Meter Frequency Report: 20{date}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        
        .summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #fff; border: 1px solid #ddd; padding: 20px; border-radius: 8px; text-align: center; }}
        .card h3 {{ margin: 0; font-size: 36px; color: #2c3e50; }}
        .card p {{ margin: 5px 0 0; color: #7f8c8d; font-weight: bold; text-transform: uppercase; font-size: 12px; }}
        .card.danger {{ border-left: 5px solid #e74c3c; }}
        .card.warning {{ border-left: 5px solid #f39c12; }}
        .card.success {{ border-left: 5px solid #27ae60; }}

        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
        th {{ background-color: #f8f9fa; color: #2c3e50; padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; }}
        td {{ padding: 12px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background-color: #f1f1f1; }}

        .badge {{ padding: 5px 10px; border-radius: 4px; font-weight: bold; font-size: 11px; display: inline-block; min-width: 60px; text-align: center; }}
        .bg-red {{ background-color: #ffeaea; color: #e74c3c; }}
        .bg-orange {{ background-color: #fff8e1; color: #f39c12; }}
        .bg-green {{ background-color: #e8f8f5; color: #27ae60; }}

        .progress-bg {{ background-color: #eee; border-radius: 10px; height: 8px; width: 100px; display: inline-block; margin-right: 10px; }}
        .progress-fill {{ height: 100%; border-radius: 10px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Meter Frequency Report: 20{date}</h1>
        <p>Target: <strong>{target_count} records</strong> per day (Every {freq} mins)</p>
        
        <div class="summary-grid">
            <div class="card"><h3>{total}</h3><p>Total Meters</p></div>
            <div class="card danger"><h3>{dead}</h3><p>Dead (0 Data)</p></div>
            <div class="card warning"><h3>{low_perf}</h3><p>Low Performance</p></div>
            <div class="card success"><h3>{perfect}</h3><p>Perfect Count</p></div>
        </div>

        <h2>⚠️ Performance Analysis (Sorted by Uptime)</h2>
        {gap_table}
    </div>
</body>
</html>
"""

def analyze_meter_count(series):
    # 1. Count actual records
    actual_count = len(series)
    
    # 2. Calculate Uptime % based on Target
    uptime = (actual_count / EXPECTED_COUNT) * 100
    if uptime > 100: uptime = 100 # Cap at 100% visually
    
    # 3. Calculate Average Insert Delay (Interval)
    if actual_count > 1:
        # Calculate time difference between consecutive rows
        deltas = series.sort_values().diff()
        
        # Get Mean interval
        mean_delta = deltas.mean()
        
        # Format nicely (e.g. "15m 30s")
        total_seconds = mean_delta.total_seconds()
        mins = int(total_seconds // 60)
        secs = int(total_seconds % 60)
        avg_delay_str = f"{mins}m {secs}s"
    else:
        avg_delay_str = "N/A"

    return actual_count, uptime, avg_delay_str

def generate_report():
    print(f"--- Generating Report for {TARGET_DATE} (Target: {EXPECTED_COUNT} records) ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
        print("Fetching meter registry...")
        meters_df = pd.read_sql("SELECT meter_number, meter_name, mac_address FROM meter_info", conn)
        
        table_name = f"site_power_{TARGET_DATE}"
        print(f"Reading logs from {table_name}...")
        query = f"SELECT meter_number, ts FROM {table_name}"
        all_data = pd.read_sql(query, conn)
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")
        return

    # Data Prep
    all_data['ts'] = pd.to_datetime(all_data['ts'])
    grouped = all_data.groupby('meter_number')
    meters_with_data = set(grouped.groups.keys())

    stats_list = []
    dead_count = 0
    perfect_count = 0
    low_perf_count = 0

    print("Analyzing meters...")

    for index, row in meters_df.iterrows():
        m_id = row['meter_number']
        m_name = row['meter_name']
        m_mac = row['mac_address']
        
        # DEAD METERS
        if m_id not in meters_with_data:
            stats_list.append({
                'id': m_id,
                'name': m_name,
                'count': 0,
                'uptime': 0.0,
                'avg_delay': 'OFFLINE'
            })
            dead_count += 1
            continue

        # ACTIVE METERS
        ts_series = grouped.get_group(m_id)['ts']
        actual_count, uptime, avg_delay = analyze_meter_count(ts_series)
        
        stats_list.append({
            'id': m_id,
            'name': m_name,
            'count': actual_count,
            'uptime': round(uptime, 1),
            'avg_delay': avg_delay
        })
        
        if uptime >= 98.0:
            perfect_count += 1
        elif uptime > 0:
            low_perf_count += 1

    # Sort by Uptime (Lowest first)
    stats_list.sort(key=lambda x: x['uptime'])

    # --- GENERATE HTML TABLE ---
    table_rows = ""
    for m in stats_list:
        # Colors
        if m['uptime'] == 0: 
            color = "#e74c3c" # Red
            badge = "<span class='badge bg-red'>OFFLINE</span>"
        elif m['uptime'] < 90: 
            color = "#f39c12" # Orange
            badge = "<span class='badge bg-orange'>LOW</span>"
        else: 
            color = "#27ae60" # Green
            badge = "<span class='badge bg-green'>OK</span>"

        # Highlight count if it matches target
        count_display = f"<b>{m['count']}</b>"
        if m['count'] < EXPECTED_COUNT:
            count_display = f"<span style='color:#e74c3c'><b>{m['count']}</b></span>"

        table_rows += f"""
        <tr>
            <td><b>{m['id']}</b></td>
            <td>{m['name']}</td>
            <td>
                <div style="display:flex; align-items:center;">
                    <div class="progress-bg"><div class="progress-fill" style="width:{m['uptime']}%; background-color:{color};"></div></div>
                    <strong>{m['uptime']}%</strong>
                </div>
            </td>
            <td>{count_display} / {EXPECTED_COUNT}</td>
            <td>{m['avg_delay']}</td>
            <td>{badge}</td>
        </tr>
        """
    
    html_table = f"""
    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Uptime (Based on Count)</th>
                <th>Records (Actual / Target)</th>
                <th>Avg Insert Interval</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>{table_rows}</tbody>
    </table>
    """

    # --- FILL TEMPLATE ---
    final_html = HTML_TEMPLATE.format(
        date=TARGET_DATE,
        freq=EXPECTED_FREQ_MIN,
        target_count=EXPECTED_COUNT,
        total=len(meters_df),
        dead=dead_count,
        low_perf=low_perf_count,
        perfect=perfect_count,
        gap_table=html_table
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"Success! Report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    generate_report()