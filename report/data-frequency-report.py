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
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_DATE = "260207"       # YYMMDD (Feb 3rd, 2026)
REPORT_FILE = os.path.join(SCRIPT_DIR, "daily", f"Frequency-20{TARGET_DATE}.html")
EXPECTED_FREQ_MIN = 15       # Expected interval in minutes
EXPECTED_COUNT = int((24 * 60) / EXPECTED_FREQ_MIN) # Target: 96 records
EXCLUDED_METER_IDS = {1}     # 1 = 电网计量表 (main grid input table, not a meter)
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
        .container {{ max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        
        .summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #fff; border: 1px solid #ddd; padding: 20px; border-radius: 8px; text-align: center; }}
        .card h3 {{ margin: 0; font-size: 36px; color: #2c3e50; }}
        .card p {{ margin: 5px 0 0; color: #7f8c8d; font-weight: bold; text-transform: uppercase; font-size: 12px; }}
        .card.danger {{ border-left: 5px solid #e74c3c; }}
        .card.warning {{ border-left: 5px solid #f39c12; }}
        .card.success {{ border-left: 5px solid #27ae60; }}

        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
        th {{ background-color: #f8f9fa; color: #2c3e50; padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; }}
        td {{ padding: 10px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background-color: #f1f1f1; }}

        .badge {{ padding: 5px 10px; border-radius: 4px; font-weight: bold; font-size: 11px; display: inline-block; min-width: 60px; text-align: center; }}
        .bg-red {{ background-color: #ffeaea; color: #e74c3c; }}
        .bg-orange {{ background-color: #fff8e1; color: #f39c12; }}
        .bg-green {{ background-color: #e8f8f5; color: #27ae60; }}

        .progress-bg {{ background-color: #eee; border-radius: 10px; height: 8px; width: 80px; display: inline-block; margin-right: 10px; }}
        .progress-fill {{ height: 100%; border-radius: 10px; }}
        
        .time-col {{ font-family: monospace; color: #555; }}

        .energy-comparison {{ margin-top: 30px; padding: 20px; background: #f8f9fa; border-radius: 8px; border: 1px solid #dee2e6; }}
        .energy-comparison h2 {{ margin-top: 0; color: #2c3e50; border-left: 5px solid #2980b9; padding-left: 10px; }}
        .energy-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }}
        .energy-card {{ background: white; border: 1px solid #ddd; padding: 15px; border-radius: 8px; text-align: center; }}
        .energy-card h3 {{ margin: 0; font-size: 28px; color: #2c3e50; }}
        .energy-card p {{ margin: 5px 0 0; color: #7f8c8d; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
        .energy-card.source {{ border-left: 5px solid #2980b9; }}
        .energy-card.meters {{ border-left: 5px solid #27ae60; }}
        .energy-card.diff {{ border-left: 5px solid #e67e22; }}
        .energy-unavailable {{ color: #95a5a6; font-style: italic; padding: 15px; }}
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

        {energy_comparison_section}

        <h2>⚠️ Performance Analysis (Sorted by Uptime)</h2>
        {gap_table}
    </div>
</body>
</html>
"""

def analyze_meter_count(series):
    # 1. Count actual records
    actual_count = len(series)
    
    # 2. Get Start/End Times
    if actual_count > 0:
        start_str = series.min().strftime('%H:%M:%S')
        end_str = series.max().strftime('%H:%M:%S')
    else:
        start_str = "-"
        end_str = "-"

    # 3. Calculate Uptime % based on Target
    uptime = (actual_count / EXPECTED_COUNT) * 100
    if uptime > 100: uptime = 100 
    
    # 4. Calculate Average Insert Delay
    if actual_count > 1:
        start_time = series.min()
        end_time = series.max()
        duration_seconds = (end_time - start_time).total_seconds()
        
        avg_seconds = duration_seconds / (actual_count - 1)
        
        mins = int(avg_seconds // 60)
        secs = int(avg_seconds % 60)
        avg_delay_str = f"{mins}m {secs}s"
    else:
        avg_delay_str = "N/A"

    return actual_count, uptime, avg_delay_str, start_str, end_str

def generate_report():
    print(f"--- Generating Report for {TARGET_DATE} (Target: {EXPECTED_COUNT} records) ---")
    
    # Date Filtering Logic
    year = "20" + TARGET_DATE[0:2]
    month = TARGET_DATE[2:4]
    day = TARGET_DATE[4:6]
    full_date_str = f"{year}-{month}-{day}"
    print(f"Filtering Data Range: {full_date_str} 00:00:00 to 23:59:59")
    
    try:
        conn = mysql.connector.connect(**db_config)
        print("Fetching meter registry...")
        meters_df = pd.read_sql("SELECT meter_number, meter_name, mac_address FROM meter_info", conn)
        
        table_name = f"site_power_{TARGET_DATE}"
        print(f"Reading logs from {table_name}...")
        
        # Strict Query
        query = f"""
            SELECT meter_number, ts 
            FROM {table_name}
            WHERE ts >= '{full_date_str} 00:00:00'
            AND ts <= '{full_date_str} 23:59:59'
        """
        all_data = pd.read_sql(query, conn)

        # --- Energy Comparison: Source (ID 1) vs Sum of Meters ---
        energy_source_kwh = None
        energy_meters_kwh = None
        energy_table = f"site_energy_{TARGET_DATE}"
        try:
            energy_query = f"""
                SELECT meter_number, ts, TotalEP
                FROM {energy_table}
                WHERE ts >= '{full_date_str} 00:00:00'
                AND ts <= '{full_date_str} 23:59:59'
            """
            energy_df = pd.read_sql(energy_query, conn)
            if not energy_df.empty:
                # Source: meter 1 (电网计量表) - TotalEP max - min
                source_rows = energy_df[energy_df['meter_number'] == 1]
                if not source_rows.empty:
                    energy_source_kwh = round(source_rows['TotalEP'].max() - source_rows['TotalEP'].min(), 2)
                # Meters: sum of (TotalEP max - min) per meter, excluding meter 1
                meter_ids = [m for m in meters_df['meter_number'].tolist() if m != 1]
                meters_total = 0.0
                for mid in meter_ids:
                    m_rows = energy_df[energy_df['meter_number'] == mid]
                    if not m_rows.empty:
                        meters_total += m_rows['TotalEP'].max() - m_rows['TotalEP'].min()
                energy_meters_kwh = round(meters_total, 2)
        except Exception as e:
            print(f"Energy table unavailable ({energy_table}): {e}")
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")
        return

    # Exclude non-meter entries (e.g. main grid input table)
    meters_df = meters_df[~meters_df['meter_number'].isin(EXCLUDED_METER_IDS)]

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
        
        # DEAD METERS
        if m_id not in meters_with_data:
            stats_list.append({
                'id': m_id,
                'name': m_name,
                'count': 0,
                'uptime': 0.0,
                'avg_delay': 'OFFLINE',
                'start': '-',
                'end': '-'
            })
            dead_count += 1
            continue

        # ACTIVE METERS
        ts_series = grouped.get_group(m_id)['ts']
        actual_count, uptime, avg_delay, start_time, end_time = analyze_meter_count(ts_series)
        
        stats_list.append({
            'id': m_id,
            'name': m_name,
            'count': actual_count,
            'uptime': round(uptime, 1),
            'avg_delay': avg_delay,
            'start': start_time,
            'end': end_time
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

        # Highlight count
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
            <td class="time-col">{m['start']}</td>
            <td class="time-col">{m['end']}</td>
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
                <th>Records</th>
                <th>Avg Interval</th>
                <th>First Seen</th>
                <th>Last Seen</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>{table_rows}</tbody>
    </table>
    """

    # --- ENERGY COMPARISON SECTION ---
    if energy_source_kwh is not None and energy_meters_kwh is not None:
        diff_kwh = round(energy_source_kwh - energy_meters_kwh, 2)
        diff_pct = round((diff_kwh / energy_source_kwh * 100), 1) if energy_source_kwh != 0 else 0
        diff_sign = "+" if diff_kwh >= 0 else ""
        energy_comparison_section = f"""
        <div class="energy-comparison">
            <h2>⚡ Energy Comparison: Source vs Meters</h2>
            <p>From <code>site_energy_{TARGET_DATE}</code> (TotalEP cumulative). Source = ID 1 (电网); Meters = sum of all other meters.</p>
            <div class="energy-grid">
                <div class="energy-card source">
                    <h3>{energy_source_kwh} kWh</h3>
                    <p>Source Input (ID 1)</p>
                </div>
                <div class="energy-card meters">
                    <h3>{energy_meters_kwh} kWh</h3>
                    <p>Meters Total</p>
                </div>
                <div class="energy-card diff">
                    <h3>{diff_sign}{diff_kwh} kWh ({diff_sign}{diff_pct}%)</h3>
                    <p>Difference (Source − Meters)</p>
                </div>
            </div>
        </div>
        """
    else:
        energy_comparison_section = """
        <div class="energy-comparison">
            <h2>⚡ Energy Comparison: Source vs Meters</h2>
            <p class="energy-unavailable">Energy data not available for this date (site_energy table may not exist or has no data).</p>
        </div>
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
        energy_comparison_section=energy_comparison_section,
        gap_table=html_table
    )

    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"Success! Report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    generate_report()