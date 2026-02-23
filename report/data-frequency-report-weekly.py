import mysql.connector
import pandas as pd
import warnings
import webbrowser
import os
from datetime import datetime, timedelta

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
START_DATE_STR = "2026-02-02"   # YYYY-MM-DD (Monday)
DAYS_TO_ANALYZE = 7             # 1 Week
EXPECTED_DAILY_COUNT = 96       # 15 min intervals
EXCLUDED_METER_IDS = {1}        # 1 = 电网计量表 (main grid input table, not a meter)
# ---------------------

db_config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_FILE = os.path.join(SCRIPT_DIR, "weekly", f"Weekly-Report-{START_DATE_STR}.html")

# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Weekly Meter Report: {start_date}</title>
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; background-color: #f4f6f9; padding: 20px; color: #333; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
        
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .meta {{ color: #7f8c8d; font-size: 14px; margin-bottom: 30px; }}

        /* Summary Cards */
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #fff; border: 1px solid #ddd; padding: 15px; border-radius: 6px; text-align: center; }}
        .card h3 {{ margin: 0; font-size: 28px; color: #2c3e50; }}
        .card p {{ margin: 5px 0 0; font-size: 12px; color: #95a5a6; font-weight: bold; text-transform: uppercase; }}

        /* Table */
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ background: #f8f9fa; padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; color: #2c3e50; }}
        td {{ padding: 10px; border-bottom: 1px solid #eee; vertical-align: middle; }}
        tr:hover {{ background-color: #f8f9fa; }}

        /* Heatmap Grid */
        .week-grid {{ display: flex; gap: 4px; }}
        .day-box {{ width: 30px; height: 30px; border-radius: 3px; font-size: 10px; display: flex; align-items: center; justify-content: center; color: white; cursor: help; font-weight: bold; }}
        
        .s-perfect {{ background-color: #2ecc71; }}  /* Green */
        .s-good {{ background-color: #f1c40f; color: #7f8c8d; }}     /* Yellow */
        .s-bad {{ background-color: #e74c3c; }}      /* Red */
        .s-empty {{ background-color: #ecf0f1; border: 1px solid #bdc3c7; color: #bdc3c7; }} /* Grey */

        .badge {{ padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; }}
        .bg-green {{ background: #d5f5e3; color: #1eb866; }}
        .bg-red {{ background: #fadbd8; color: #e74c3c; }}

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
        <h1>📅 Weekly Meter Health Report</h1>
        <div class="meta">Period: <strong>{start_date}</strong> to <strong>{end_date}</strong> (Target: {target_weekly} records)</div>

        {energy_comparison_section}

        <div class="summary">
            <div class="card"><h3>{total_meters}</h3><p>Meters Monitored</p></div>
            <div class="card"><h3>{avg_uptime}%</h3><p>Network Avg Uptime</p></div>
            <div class="card"><h3>{perfect_meters}</h3><p>100% Reliable Devices</p></div>
            <div class="card" style="border-left: 5px solid #e74c3c"><h3>{dead_meters}</h3><p>Dead / Offline</p></div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>Weekly Uptime</th>
                    <th>Total Records</th>
                    <th>7-Day Trend (Mon &rarr; Sun)</th>
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

def fetch_weekly_data(start_date, meters_df):
    """
    Loops through 7 days, queries each daily table, and aggregates counts.
    """
    weekly_data = {} # {meter_id: {date_str: count, ...}}
    
    # Initialize structure for all meters known in registry
    for mid in meters_df['meter_number']:
        weekly_data[mid] = {}

    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    # Loop through each day
    for i in range(DAYS_TO_ANALYZE):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        
        # Format table name: site_power_YYMMDD
        table_suffix = current_date.strftime("%y%m%d")
        table_name = f"site_power_{table_suffix}"
        
        print(f"[{i+1}/{DAYS_TO_ANALYZE}] Querying {table_name}...")

        try:
            # Optimized Query
            query = f"""
                SELECT meter_number, COUNT(*) 
                FROM {table_name} 
                WHERE ts >= '{date_str} 00:00:00' 
                AND ts <= '{date_str} 23:59:59'
                GROUP BY meter_number
            """
            cursor.execute(query)
            results = cursor.fetchall()
            
            # Map results to data structure
            for mid, count in results:
                # FIXED: Check if meter exists in our registry
                if mid in weekly_data:
                    weekly_data[mid][date_str] = count
                else:
                    # Optional: Print warning, but don't crash
                    # print(f"   Note: Found unregistered Meter ID {mid} in logs. Skipping.")
                    pass
            
            # Mark table as "Checked" by setting 0 for missing data (instead of None)
            # If table existed, anyone who didn't report gets a 0
            for mid in weekly_data:
                if date_str not in weekly_data[mid]:
                     weekly_data[mid][date_str] = 0

        except mysql.connector.Error as err:
            print(f"   ⚠️  Skipping {table_name} (Table does not exist)")
            # Leave as None to indicate "System Error/No Table"
            pass

    conn.close()
    return weekly_data

def fetch_weekly_energy(start_date, meter_ids):
    """
    Fetch source (ID 1) and meters total energy for each day; aggregate over 7 days.
    Returns (energy_source_kwh, energy_meters_kwh) or (None, None) if unavailable.
    """
    energy_source_total = 0.0
    energy_meters_total = 0.0
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    found_any = False

    for i in range(DAYS_TO_ANALYZE):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        table_suffix = current_date.strftime("%y%m%d")
        table_name = f"site_energy_{table_suffix}"

        try:
            query = f"""
                SELECT meter_number, MIN(TotalEP) as min_ep, MAX(TotalEP) as max_ep
                FROM {table_name}
                WHERE ts >= '{date_str} 00:00:00' AND ts <= '{date_str} 23:59:59'
                AND TotalEP IS NOT NULL
                GROUP BY meter_number
            """
            cursor.execute(query)
            results = cursor.fetchall()
            for mid, min_ep, max_ep in results:
                delta = max_ep - min_ep if (min_ep is not None and max_ep is not None) else 0
                if mid == 1:
                    energy_source_total += delta
                elif mid in meter_ids:
                    energy_meters_total += delta
                found_any = True
        except mysql.connector.Error:
            pass

    conn.close()
    if not found_any:
        return None, None
    return round(energy_source_total, 2), round(energy_meters_total, 2)

def generate_report():
    print(f"--- Starting Weekly Report for {START_DATE_STR} ---")
    
    start_dt = datetime.strptime(START_DATE_STR, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=DAYS_TO_ANALYZE-1)
    
    # 1. Get Meter List
    try:
        conn = mysql.connector.connect(**db_config)
        meters_df = pd.read_sql("SELECT meter_number, meter_name FROM meter_info", conn)
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}")
        return

    # Exclude non-meter entries (e.g. main grid input table)
    meters_df = meters_df[~meters_df['meter_number'].isin(EXCLUDED_METER_IDS)]

    # 2. Get Data
    weekly_data = fetch_weekly_data(start_dt, meters_df)

    # 2b. Energy comparison (source vs meters, aggregated over week)
    meter_ids = set(meters_df['meter_number'].tolist())
    energy_source_kwh, energy_meters_kwh = fetch_weekly_energy(start_dt, meter_ids)

    # 3. Build Rows
    table_rows = ""
    total_meters = len(meters_df)
    perfect_meters = 0
    dead_meters = 0
    sum_uptime = 0

    target_weekly = EXPECTED_DAILY_COUNT * DAYS_TO_ANALYZE

    # Generate list of date strings for sorting columns
    date_keys = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS_TO_ANALYZE)]

    print("Generating HTML...")

    # Sort meters by ID for cleaner list
    meters_df = meters_df.sort_values('meter_number')

    for index, row in meters_df.iterrows():
        mid = row['meter_number']
        m_name = row['meter_name']
        
        daily_counts = weekly_data.get(mid, {})
        
        total_rec = 0
        heatmap_html = "<div class='week-grid'>"
        
        for d_str in date_keys:
            count = daily_counts.get(d_str, None)
            
            # Determine Color Box
            if count is None:
                # Table didn't exist or script failed to read it
                css_class = "s-empty"
                tooltip = f"{d_str}: No Table/Data"
                display_txt = "X"
            else:
                total_rec += count
                display_txt = str(count)
                tooltip = f"{d_str}: {count} records"
                
                if count >= (EXPECTED_DAILY_COUNT * 0.95): css_class = "s-perfect"
                elif count >= (EXPECTED_DAILY_COUNT * 0.5): css_class = "s-good"
                else: css_class = "s-bad"
            
            heatmap_html += f"<div class='day-box {css_class}' title='{tooltip}'>{display_txt}</div>"
        
        heatmap_html += "</div>"

        # Weekly Stats
        # Avoid division by zero if all tables are missing
        if target_weekly > 0:
            uptime = (total_rec / target_weekly) * 100
        else:
            uptime = 0

        if uptime > 100: uptime = 100
        sum_uptime += uptime

        if uptime >= 98: perfect_meters += 1
        if uptime == 0: dead_meters += 1
        
        # Badge
        if uptime > 90: badge = f"<span class='badge bg-green'>{round(uptime,1)}%</span>"
        else: badge = f"<span class='badge bg-red'>{round(uptime,1)}%</span>"

        table_rows += f"""
        <tr>
            <td><b>{mid}</b></td>
            <td>{m_name}</td>
            <td>{badge}</td>
            <td><b>{total_rec}</b> / {target_weekly}</td>
            <td>{heatmap_html}</td>
        </tr>
        """

    avg_uptime = round(sum_uptime / total_meters, 1) if total_meters > 0 else 0

    # Energy comparison section
    if energy_source_kwh is not None and energy_meters_kwh is not None:
        diff_kwh = round(energy_source_kwh - energy_meters_kwh, 2)
        diff_pct = round((diff_kwh / energy_source_kwh * 100), 1) if energy_source_kwh != 0 else 0
        diff_sign = "+" if diff_kwh >= 0 else ""
        energy_comparison_section = f"""
        <div class="energy-comparison">
            <h2>⚡ Energy Comparison: Source vs Meters (Weekly)</h2>
            <p>From <code>site_energy_*</code> tables (TotalEP cumulative). Source = ID 1 (电网); Meters = sum of all other meters. Aggregated over {DAYS_TO_ANALYZE} days.</p>
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
            <h2>⚡ Energy Comparison: Source vs Meters (Weekly)</h2>
            <p class="energy-unavailable">Energy data not available for this period (site_energy tables may not exist or have no data).</p>
        </div>
        """

    # 4. Save
    html = HTML_TEMPLATE.format(
        start_date=START_DATE_STR,
        end_date=end_dt.strftime("%Y-%m-%d"),
        target_weekly=target_weekly,
        total_meters=total_meters,
        avg_uptime=avg_uptime,
        perfect_meters=perfect_meters,
        dead_meters=dead_meters,
        energy_comparison_section=energy_comparison_section,
        rows=table_rows
    )

    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"Success! Weekly report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    generate_report()