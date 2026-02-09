import mysql.connector
import pandas as pd
import warnings
import webbrowser
import os

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
TARGET_ID = 1               # The Source Meter
TARGET_DATE = "260203"      # Analysis Date
REPORT_FILE = f"Source-Analysis-ID{TARGET_ID}.html"
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
    <title>Source Structure Analysis: ID {mid}</title>
    <style>
        body {{ font-family: 'Segoe UI', monospace; background-color: #f4f6f9; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; border-left: 5px solid #3498db; padding-left: 10px; }}
        
        /* Key Value Grid */
        .kv-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 15px; }}
        .kv-item {{ background: #f8f9fa; padding: 10px; border: 1px solid #dee2e6; border-radius: 4px; }}
        .kv-label {{ font-size: 11px; color: #7f8c8d; font-weight: bold; text-transform: uppercase; display: block; }}
        .kv-value {{ font-size: 16px; color: #2c3e50; font-weight: bold; }}

        /* Tables */
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 15px; }}
        th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
        td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
        
        .active-col {{ background-color: #e8f8f5; color: #27ae60; font-weight: bold; }}
        .null-col {{ color: #bdc3c7; font-style: italic; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🏗️ Source Structure Analysis: ID {mid}</h1>
        
        <h2>1. Device Configuration (meter_info)</h2>
        <div class="kv-grid">
            {config_html}
        </div>

        <h2>2. Network Hierarchy</h2>
        <div class="kv-item" style="background: #eaf2f8; border-color: #aed6f1;">
            <span class="kv-label">Child Meters</span>
            <span class="kv-value">{child_count} meters</span>
            <br><small>(Devices listing ID {mid} as their parent)</small>
        </div>

        <h2>3. Data Capability Scan</h2>
        <p>Checking which telemetry fields are actually active for this device.</p>
        <table>
            <thead>
                <tr>
                    <th>Column Name</th>
                    <th>Sample Value</th>
                    <th>Status</th>
                    <th>Description ( inferred)</th>
                </tr>
            </thead>
            <tbody>
                {columns_html}
            </tbody>
        </table>

        <h2>4. Power & Load Snapshot (Avg over 24h)</h2>
        <div class="kv-grid">
            <div class="kv-item"><span class="kv-label">Avg Voltage (A/B/C)</span><span class="kv-value">{avg_u} V</span></div>
            <div class="kv-item"><span class="kv-label">Avg Current</span><span class="kv-value">{avg_i} A</span></div>
            <div class="kv-item"><span class="kv-label">Total Active Power</span><span class="kv-value">{avg_pt} kW</span></div>
            <div class="kv-item"><span class="kv-label">Power Factor</span><span class="kv-value">{avg_pf}</span></div>
        </div>
    </div>
</body>
</html>
"""

def get_column_desc(col_name):
    """Simple dictionary to guess what columns mean"""
    desc_map = {
        'ts': 'Timestamp',
        'Uan': 'Voltage Phase A', 'Ubn': 'Voltage Phase B', 'Ucn': 'Voltage Phase C',
        'Ia': 'Current Phase A', 'Ib': 'Current Phase B', 'Ic': 'Current Phase C',
        'Pt': 'Total Active Power (kW)', 'Qt': 'Total Reactive Power', 'St': 'Apparent Power',
        'FPt': 'Power Factor', 'freq': 'Grid Frequency',
        'Ep_imp': 'Import Energy (Consumption)', 'Ep_exp': 'Export Energy (Generation)'
    }
    return desc_map.get(col_name, '-')

def analyze_source():
    print(f"--- Inspecting Source ID {TARGET_ID} ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True) # Dict cursor is easier here

        # 1. Get Config
        print("Fetching Configuration...")
        cursor.execute(f"SELECT * FROM meter_info WHERE meter_number = {TARGET_ID}")
        config = cursor.fetchone()
        
        if not config:
            print("Error: ID 1 not found in meter_info!")
            return

        # 2. Check Hierarchy
        print("Checking Hierarchy...")
        cursor.execute(f"SELECT COUNT(*) as cnt FROM meter_info WHERE parent_meter_number = {TARGET_ID}")
        child_count = cursor.fetchone()['cnt']

        # 3. Get Data Sample
        table_name = f"site_power_{TARGET_DATE}"
        print(f"Scanning Data Structure in {table_name}...")
        
        # Get one full row to see columns
        cursor.execute(f"SELECT * FROM {table_name} WHERE meter_number = {TARGET_ID} LIMIT 1")
        sample_row = cursor.fetchone()

        # Get Averages
        print("Calculating Loads...")
        avg_query = f"""
            SELECT 
                AVG(Uan) as u_a, AVG(Ubn) as u_b, AVG(Ucn) as u_c,
                AVG(Ia) as i_a, AVG(Ib) as i_b, AVG(Ic) as i_c,
                AVG(Pt) as pt, AVG(FPt) as pf
            FROM {table_name} 
            WHERE meter_number = {TARGET_ID}
        """
        cursor.execute(avg_query)
        stats = cursor.fetchone()
        
        conn.close()

    except Exception as e:
        print(f"Error: {e}")
        return

    # --- HTML GENERATION ---
    
    # 1. Config HTML
    config_html = ""
    for k, v in config.items():
        config_html += f'<div class="kv-item"><span class="kv-label">{k}</span><span class="kv-value">{v}</span></div>'

    # 2. Columns HTML
    cols_html = ""
    if sample_row:
        for col, val in sample_row.items():
            desc = get_column_desc(col)
            
            # Determine if column is "Active"
            is_active = val is not None and val != 0
            
            style_class = "active-col" if is_active else "null-col"
            status_text = "✅ Active" if is_active else "⚪ Null/Zero"
            
            cols_html += f"""
            <tr>
                <td><b>{col}</b></td>
                <td class="{style_class}">{val}</td>
                <td>{status_text}</td>
                <td>{desc}</td>
            </tr>
            """
    else:
        cols_html = "<tr><td colspan='4'>No data found for ID 1 on this date.</td></tr>"

    # 3. Stats Prep
    avg_u = round((stats['u_a'] + stats['u_b'] + stats['u_c']) / 3, 2) if stats['u_a'] else 0
    avg_i = round((stats['i_a'] + stats['i_b'] + stats['i_c']), 2) if stats['i_a'] else 0
    avg_pt = round(stats['pt'], 2) if stats['pt'] else 0
    avg_pf = round(stats['pf'], 3) if stats['pf'] else 0

    html = HTML_TEMPLATE.format(
        mid=TARGET_ID,
        config_html=config_html,
        child_count=child_count,
        columns_html=cols_html,
        avg_u=avg_u,
        avg_i=avg_i,
        avg_pt=avg_pt,
        avg_pf=avg_pf
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Success! Analysis saved to: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    analyze_source()