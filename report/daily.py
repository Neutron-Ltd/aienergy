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
TARGET_DATE = "260202"       # Default date (YYMMDD)
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
EXPECTED_FREQ_MIN = 15       # Expected interval in minutes
EXPECTED_COUNT = int((24 * 60) / EXPECTED_FREQ_MIN) # Target: 96 records
# ---------------------

db_config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

# --- HTML CSS & TEMPLATE (Restored to Original UI) ---
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
        .card.info {{ border-left: 5px solid #3498db; }}
        
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 20px; }}
        th {{ background: #2c3e50; color: white; padding: 12px; text-align: left; position: sticky; top: 0; z-index: 10; }}
        td {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background-color: #f9f9f9; }}
        
        .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; color: white; }}
        .bg-green {{ background: #27ae60; }}
        .bg-orange {{ background: #f39c12; }}
        .bg-red {{ background: #e74c3c; }}
        .bg-grey {{ background: #95a5a6; }}
        
        .type-badge {{ font-size: 12px; font-weight: bold; padding: 3px 6px; border-radius: 4px; border: 1px solid #ccc; background: #fff; display: inline-block; }}
        
        .time-col {{ font-family: monospace; color: #7f8c8d; }}
        .progress-bg {{ background: #ecf0f1; border-radius: 10px; height: 10px; width: 100px; margin-right: 10px; display: inline-block; overflow: hidden; vertical-align: middle; }}
        .progress-fill {{ height: 100%; border-radius: 10px; transition: width 0.3s; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📡 Network Pulse: Daily Frequency Report ({date})</h1>
        <p>Target Interval: <strong>{freq} minutes</strong> | Expected Records: <strong>{target_count}/day</strong></p>

        <div class="summary-grid">
            <div class="card info">
                <h3>{total}</h3>
                <p>Total Registered Devices</p>
            </div>
            <div class="card danger">
                <h3>{dead}</h3>
                <p>Offline / Dead (< 50%)</p>
            </div>
            <div class="card warning">
                <h3>{low_perf}</h3>
                <p>Low Performance (< 95%)</p>
            </div>
            <div class="card success">
                <h3>{perfect}</h3>
                <p>Perfect Rhythm (95%+)</p>
            </div>
        </div>

        <h2>Detailed Device Diagnostics</h2>
        {gap_table}
    </div>
</body>
</html>
"""

def analyze_rhythm(timestamps):
    if len(timestamps) == 0:
        return 0, "-", "-", "-", "0.0"
        
    ts_series = pd.Series(pd.to_datetime(timestamps)).sort_values().reset_index(drop=True)
    count = len(ts_series)
    
    first_seen = ts_series.iloc[0].strftime('%H:%M:%S')
    last_seen = ts_series.iloc[-1].strftime('%H:%M:%S')
    
    if count > 1:
        deltas = ts_series.diff().dropna().dt.total_seconds() / 60.0
        avg_gap = deltas.mean()
        avg_gap_str = f"{int(avg_gap)}m {int((avg_gap % 1) * 60)}s"
    else:
        avg_gap_str = "N/A"
        
    uptime_pct = min((count / EXPECTED_COUNT) * 100, 100.0)
    return count, avg_gap_str, first_seen, last_seen, f"{uptime_pct:.1f}"

def run_daily_report(date_suffix):
    report_file = os.path.join(DATA_DIR, f"daily-{date_suffix}.html")
    print(f"--- Running Fleet-Wide Health Report for 20{date_suffix} ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # 1. Fetch All Meters
        cursor.execute("SELECT meter_number, meter_name FROM meter_info ORDER BY meter_number ASC")
        meters = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        
        # 2. Bulk Fetch Power & Energy Tables
        print("Fetching Speedometer (Power) and Odometer (Energy) data...")
        try:
            df_power = pd.read_sql(f"SELECT meter_number, ts FROM site_power_{date_suffix}", conn)
        except: df_power = pd.DataFrame(columns=['meter_number', 'ts'])

        try:
            df_energy = pd.read_sql(f"SELECT meter_number, ts, TotalEP, ImpEP, ExpEP FROM site_energy_{date_suffix}", conn)
        except: df_energy = pd.DataFrame(columns=['meter_number', 'ts', 'TotalEP', 'ImpEP', 'ExpEP'])
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return

    dead_count = 0
    low_perf_count = 0
    perfect_count = 0
    table_rows = ""

    print("Analyzing gaps and categorizing devices...")
    for m in meters:
        mid = m['id']
        name = m['name']
        
        # Filter data for this meter
        m_power = df_power[df_power['meter_number'] == mid] if not df_power.empty else pd.DataFrame()
        m_energy = df_energy[df_energy['meter_number'] == mid] if not df_energy.empty else pd.DataFrame()
        
        has_power = not m_power.empty
        has_energy = not m_energy.empty
        
        # Check if energy registers are moving
        energy_is_zero = True
        if has_energy:
            for col in ['TotalEP', 'ImpEP', 'ExpEP']:
                if col in m_energy.columns and m_energy[col].max() > 0:
                    energy_is_zero = False
                    break

        # Detect Type & Assign Target Timestamps
        target_ts = []
        if has_power and not energy_is_zero:
            m_type_html = "<span class='type-badge' style='color: #2980b9; border-color: #3498db;'>Standard Load</span>"
            target_ts = m_power['ts'].tolist()
        elif not has_power and has_energy and not energy_is_zero:
            m_type_html = "<span class='type-badge' style='color: #8e44ad; border-color: #9b59b6;'>Energy-Only (Grid)</span>"
            target_ts = m_energy['ts'].tolist()
        elif has_power and energy_is_zero:
            m_type_html = "<span class='type-badge' style='color: #d35400; border-color: #e67e22;'>Power-Only (Anomaly)</span>"
            target_ts = m_power['ts'].tolist()
        else:
            m_type_html = "<span class='type-badge' style='color: #7f8c8d; border-color: #95a5a6;'>Offline</span>"
            
        count, avg_gap, first_s, last_s, uptime = analyze_rhythm(target_ts)
        uptime_val = float(uptime)

        # Apply Original Status Logic
        if count == 0:
            badge = "<span class='badge bg-grey'>OFFLINE</span>"
            color = "#95a5a6"
            dead_count += 1
        elif uptime_val < 50:
            badge = "<span class='badge bg-red'>CRITICAL</span>"
            color = "#e74c3c"
            dead_count += 1
        elif uptime_val < 95:
            badge = "<span class='badge bg-orange'>WARNING</span>"
            color = "#f39c12"
            low_perf_count += 1
        else:
            badge = "<span class='badge bg-green'>OK</span>"
            color = "#27ae60"
            perfect_count += 1
            
        count_display = f"<span style='color:#e74c3c'><b>{count}</b></span>" if count < (EXPECTED_COUNT * 0.95) else count

        # Render HTML Row
        table_rows += f"""
        <tr>
            <td><b>{mid}</b></td>
            <td>{name}</td>
            <td>{m_type_html}</td>
            <td>
                <div style="display:flex; align-items:center;">
                    <div class="progress-bg"><div class="progress-fill" style="width:{uptime}%; background-color:{color};"></div></div>
                    <strong>{uptime}%</strong>
                </div>
            </td>
            <td>{count_display} / {EXPECTED_COUNT}</td>
            <td>{avg_gap}</td>
            <td class="time-col">{first_s}</td>
            <td class="time-col">{last_s}</td>
            <td>{badge}</td>
        </tr>
        """

    html_table = f"""
    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Detected Type</th>
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

    final_html = HTML_TEMPLATE.format(
        date=date_suffix,
        freq=EXPECTED_FREQ_MIN,
        target_count=EXPECTED_COUNT,
        total=len(meters),
        dead=dead_count,
        low_perf=low_perf_count,
        perfect=perfect_count,
        gap_table=html_table
    )

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"Success! Report generated: {report_file}")
    
    try:
        webbrowser.open('file://' + os.path.realpath(report_file))
    except:
        pass

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate a daily frequency report.")
    parser.add_argument("--date", type=str, default=TARGET_DATE, help="Target Date Suffix (YYMMDD, e.g., 260202)")
    args = parser.parse_args()
    
    run_daily_report(args.date)