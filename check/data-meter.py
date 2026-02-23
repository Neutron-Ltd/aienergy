import mysql.connector
import pandas as pd
import warnings
import webbrowser
import os
import matplotlib.pyplot as plt
import io
import base64

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

TARGET_METER_ID = 29
TARGET_DATE_SUFFIX = "260202"      # Table suffix (YYMMDD)
FULL_DATE_STR = "2026-02-02"       # For strict SQL filtering (YYYY-MM-DD)
REPORT_FILE = os.path.join(DATA_DIR, f"Meter-{TARGET_METER_ID}-{TARGET_DATE_SUFFIX}.html")
# ---------------------

db_config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Meter {mid}</title>
    <style>
        body {{ font-family: 'Segoe UI', monospace; background-color: #f4f6f9; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: white; padding: 30px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
        h1 {{ border-bottom: 2px solid #333; padding-bottom: 10px; }}
        
        .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }}
        .stat-box {{ background: #eee; padding: 15px; border-radius: 5px; text-align: center; }}
        .stat-box h3 {{ margin: 0; color: #2c3e50; font-size: 24px; }}
        .stat-box span {{ font-size: 12px; color: #7f8c8d; font-weight: bold; text-transform: uppercase; }}

        table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 20px; }}
        th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; position: sticky; top: 0; }}
        td {{ padding: 8px; border-bottom: 1px solid #ddd; font-family: monospace; }}
        tr:hover {{ background-color: #f1f1f1; }}
        
        .gap-high {{ color: #e74c3c; font-weight: bold; }}
        .gap-ok {{ color: #27ae60; }}
        .gap-short {{ color: #f39c12; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 Meter {mid} Analysis ({date_str})</h1>
        
        <div class="stats-grid">
            <div class="stat-box"><h3>{count}</h3><span>Total Records</span></div>
            <div class="stat-box"><h3>{duration} hours</h3><span>Data Span</span></div>
            <div class="stat-box"><h3>{real_avg}</h3><span>Avg Interval</span></div>
            <div class="stat-box"><h3>{voltage_check}</h3><span>Voltage Status</span></div>
        </div>

        <h3>First Record: {start_time} <br> Last Record: {end_time}</h3>
        
        <h2>Visualization of Intervals</h2>
        <img src="data:image/png;base64,{plot_data}" style="width:100%; border:1px solid #ddd; margin-bottom:20px;">

        <h2>Raw Data Log</h2>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Timestamp (TS)</th>
                    <th>Time Since Last (Delta)</th>
                    <th>Voltage A (V)</th>
                    <th>Current A (A)</th>
                    <th>Total Power (kW)</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

def generate_plot(df):
    plt.figure(figsize=(12, 4))
    minutes = df['delta'].dt.total_seconds() / 60
    
    plt.bar(df.index, minutes, color='#3498db', width=0.8)
    plt.axhline(y=15, color='r', linestyle='--', label='Target (15m)')
    plt.title('Time Interval Between Packets (Minutes)')
    plt.xlabel('Record Sequence Number')
    plt.ylabel('Minutes Since Last')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    return base64.b64encode(img.getvalue()).decode()

def analyze_meter():
    print(f"--- Meter {TARGET_METER_ID} on {FULL_DATE_STR} ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
        table_name = f"site_power_{TARGET_DATE_SUFFIX}"
        
        # CHANGED: Added strict filtering for ONE DAY (00:00:00 to 23:59:59)
        query = f"""
            SELECT ts, Uan, Ia, Pt 
            FROM {table_name} 
            WHERE meter_number = {TARGET_METER_ID} 
            AND ts >= '{FULL_DATE_STR} 00:00:00'
            AND ts <= '{FULL_DATE_STR} 23:59:59'
            ORDER BY ts ASC
        """
        print(f"Running Query: {query}")
        df = pd.read_sql(query, conn)
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")
        return

    if df.empty:
        print("No data found for this specific date range!")
        return

    # Process Data
    df['ts'] = pd.to_datetime(df['ts'])
    df['delta'] = df['ts'].diff()

    # Stats
    start_time = df['ts'].min()
    end_time = df['ts'].max()
    duration_seconds = (end_time - start_time).total_seconds()
    duration_hours = round(duration_seconds / 3600, 2)
    
    # Real Avg: Duration / (Count - 1)
    if len(df) > 1:
        real_avg_sec = duration_seconds / (len(df) - 1)
        mins = int(real_avg_sec // 60)
        secs = int(real_avg_sec % 60)
        real_avg_str = f"{mins}m {secs}s"
    else:
        real_avg_str = "N/A"

    # Voltage
    avg_voltage = df['Uan'].mean()
    voltage_status = "⚡ LIVE" if avg_voltage > 10 else "🔴 DEAD (0V)"

    # HTML Rows
    rows_html = ""
    for idx, row in df.iterrows():
        ts_str = row['ts'].strftime('%Y-%m-%d %H:%M:%S')
        
        if pd.isnull(row['delta']):
            delta_str = "START"
            style_class = ""
        else:
            secs = row['delta'].total_seconds()
            mins = int(secs // 60)
            rem_secs = int(secs % 60)
            delta_str = f"{mins}m {rem_secs}s"
            
            # Logic: Green for 14-16 mins, Orange for bursts, Red for Gaps
            if 14 <= mins <= 16: style_class = "gap-ok"
            elif mins > 20:      style_class = "gap-high"
            else:                style_class = "gap-short"
            
        rows_html += f"""
        <tr>
            <td>{idx+1}</td>
            <td>{ts_str}</td>
            <td class="{style_class}">{delta_str}</td>
            <td>{row['Uan']}</td>
            <td>{row['Ia']}</td>
            <td>{row['Pt']}</td>
        </tr>
        """

    plot_b64 = generate_plot(df)

    html = HTML_TEMPLATE.format(
        mid=TARGET_METER_ID,
        date_str=FULL_DATE_STR,
        count=len(df),
        duration=duration_hours,
        start_time=start_time,
        end_time=end_time,
        real_avg=real_avg_str,
        voltage_check=voltage_status,
        plot_data=plot_b64,
        rows=rows_html
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"Success! Report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    analyze_meter()