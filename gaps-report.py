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
TARGET_DATE = "260203"       # Feb 2nd, 2026
REPORT_FILE = f"Report-20{TARGET_DATE}.html"
EXPECTED_FREQ = '15min'      # Expected report interval
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
    <title>Meter Health Report: 20{date}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; }}
        
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
        <h1>📊 Daily Meter Health Report: 20{date}</h1>
        
        <div class="summary-grid">
            <div class="card"><h3>{total}</h3><p>Total Meters</p></div>
            <div class="card danger"><h3>{dead}</h3><p>Dead (Offline)</p></div>
            <div class="card warning"><h3>{gaps}</h3><p>Unstable</p></div>
            <div class="card success"><h3>{perfect}</h3><p>Perfect Score</p></div>
        </div>

        <h2>🔴 Dead Meters (Critical Action Required)</h2>
        <p>These devices sent <strong>zero data</strong> for the entire day.</p>
        {dead_table}

        <h2>⚠️ Performance & Gap Analysis</h2>
        <p>Sorted by lowest uptime first. (Expected Frequency: {freq})</p>
        {gap_table}
    </div>
</body>
</html>
"""

def analyze_gaps_consecutive(series, freq_str):
    # Extract integer minutes from freq string (e.g. '15min' -> 15)
    # This prevents the hardcoded multiplication error
    try:
        interval_minutes = int(re.search(r'\d+', freq_str).group())
    except:
        interval_minutes = 5 # Fallback safety

    # Create perfect index
    start_of_day = series.index.min().replace(hour=0, minute=0, second=0)
    end_of_day = series.index.min().replace(hour=23, minute=59, second=59)
    full_index = pd.date_range(start=start_of_day, end=end_of_day, freq=freq_str)
    
    # Reindex
    reindexed = series.reindex(full_index)
    is_missing = reindexed.isnull()
    
    total_missing = is_missing.sum()
    total_slots = len(full_index)
    uptime = ((total_slots - total_missing) / total_slots) * 100
    
    gap_blocks = (is_missing != is_missing.shift()).cumsum()
    gap_sizes = is_missing.groupby(gap_blocks).sum()
    
    # FIXED: Multiply by actual interval_minutes, not hardcoded 5
    longest_gap_min = gap_sizes.max() * interval_minutes if not gap_sizes.empty else 0
    
    return total_missing, longest_gap_min, uptime

def generate_report():
    print(f"--- Generating HTML Report for {TARGET_DATE} (Freq: {EXPECTED_FREQ}) ---")
    
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

    dead_list = []
    gap_list = []
    perfect_count = 0

    print("Analyzing meters...")

    for index, row in meters_df.iterrows():
        m_id = row['meter_number']
        m_name = row['meter_name']
        m_mac = row['mac_address']
        
        if m_id not in meters_with_data:
            dead_list.append({'id': m_id, 'name': m_name, 'mac': m_mac})
            continue

        ts_series = grouped.get_group(m_id).set_index('ts')['meter_number']
        
        # FIXED: Passing EXPECTED_FREQ here!
        missing_count, max_gap_min, uptime = analyze_gaps_consecutive(ts_series, EXPECTED_FREQ)
        
        if missing_count > 0:
            gap_list.append({
                'id': m_id,
                'name': m_name,
                'uptime': round(uptime, 2),
                'gap_min': int(max_gap_min)
            })
        else:
            perfect_count += 1

    # Sort Gaps (Worst first)
    gap_list.sort(key=lambda x: x['uptime'])

    # --- GENERATE HTML TABLES ---
    
    # 1. Dead Table
    if dead_list:
        dead_rows = ""
        for m in dead_list:
            dead_rows += f"<tr><td><b>{m['id']}</b></td><td>{m['name']}</td><td><code>{m['mac']}</code></td><td><span class='badge bg-red'>OFFLINE</span></td></tr>"
        dead_html = f"<table><thead><tr><th>ID</th><th>Name</th><th>MAC Address</th><th>Status</th></tr></thead><tbody>{dead_rows}</tbody></table>"
    else:
        dead_html = "<p><i>No dead meters found. Great job!</i></p>"

    # 2. Gap Table
    if gap_list:
        gap_rows = ""
        for m in gap_list:
            if m['uptime'] < 50: color = "#e74c3c"
            elif m['uptime'] < 90: color = "#f39c12"
            else: color = "#27ae60"

            hours = m['gap_min'] // 60
            mins = m['gap_min'] % 60
            time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} min"

            gap_rows += f"""
            <tr>
                <td><b>{m['id']}</b></td>
                <td>{m['name']}</td>
                <td>
                    <div style="display:flex; align-items:center;">
                        <div class="progress-bg"><div class="progress-fill" style="width:{m['uptime']}%; background-color:{color};"></div></div>
                        <strong>{m['uptime']}%</strong>
                    </div>
                </td>
                <td>{time_str}</td>
            </tr>
            """
        gap_html = f"<table><thead><tr><th>ID</th><th>Name</th><th>Uptime %</th><th>Longest Downtime</th></tr></thead><tbody>{gap_rows}</tbody></table>"
    else:
        gap_html = "<p><i>No unstable meters found.</i></p>"

    # --- FILL TEMPLATE ---
    final_html = HTML_TEMPLATE.format(
        date=TARGET_DATE,
        freq=EXPECTED_FREQ,
        total=len(meters_df),
        dead=len(dead_list),
        gaps=len(gap_list),
        perfect=perfect_count,
        dead_table=dead_html,
        gap_table=gap_html
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"Success! Report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    generate_report()