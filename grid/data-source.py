import mysql.connector
import pandas as pd
import warnings
import webbrowser
import os

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

TARGET_ID = 1               # The Utility Source Meter
TARGET_DATE = "260203"      # Feb 3rd, 2026
REPORT_FILE = os.path.join(DATA_DIR, f"data-source-{TARGET_ID}-{TARGET_DATE}.html")
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
    <title>Source Energy Analysis: ID {mid}</title>
    <style>
        body {{ font-family: 'Segoe UI', monospace; background-color: #f4f6f9; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; border-left: 5px solid #27ae60; padding-left: 10px; }}
        
        /* KPI Cards */
        .kpi-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 30px; }}
        .kpi-card {{ background: #fff; border: 1px solid #ddd; padding: 20px; text-align: center; border-radius: 8px; }}
        .kpi-card h3 {{ font-size: 32px; color: #2c3e50; margin: 0; }}
        .kpi-card p {{ margin: 5px 0 0; color: #7f8c8d; font-size: 12px; font-weight: bold; text-transform: uppercase; }}
        
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 15px; }}
        th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
        td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Source Energy Analysis: ID {mid}</h1>
        <p>Analyzing table: <code>{table_name}</code> (The "Odometer" Table)</p>

        <div class="kpi-grid">
            <div class="kpi-card" style="border-bottom: 4px solid #2980b9">
                <h3>{total_kwh} kWh</h3>
                <p>Total Import (Day)</p>
            </div>
            <div class="kpi-card" style="border-bottom: 4px solid #e67e22">
                <h3>{start_read} &rarr; {end_read}</h3>
                <p>Meter Reading Range</p>
            </div>
            <div class="kpi-card" style="border-bottom: 4px solid #27ae60">
                <h3>{row_count}</h3>
                <p>Data Points Found</p>
            </div>
        </div>

        <h2>1. Raw Billing Data (First 20 Records)</h2>
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Total Active Energy (TotalEP)</th>
                    <th>Import (ImpEP)</th>
                    <th>Export (ExpEP)</th>
                    <th>Reactive (TotalEQ)</th>
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

def analyze_source_energy():
    print(f"--- Hunting for Source Data (ID {TARGET_ID}) ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
        
        # 1. Switch Target to the ENERGY table
        table_name = f"site_energy_{TARGET_DATE}"
        print(f"Querying {table_name}...")
        
        # Fetch the billing columns
        # TotalEP = Total Active Energy (What you pay for)
        # ImpEP = Import (From Grid)
        # ExpEP = Export (To Grid - e.g. Solar)
        query = f"""
            SELECT ts, TotalEP, ImpEP, ExpEP, TotalEQ 
            FROM {table_name} 
            WHERE meter_number = {TARGET_ID} 
            ORDER BY ts ASC
        """
        df = pd.read_sql(query, conn)
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")
        return

    if df.empty:
        print("❌ CRITICAL: No data found in the Energy table either!")
        return

    # 2. Calculate Daily Consumption
    # Energy is cumulative, so Consumption = Max - Min
    start_kwh = df['TotalEP'].min()
    end_kwh = df['TotalEP'].max()
    daily_consumption = round(end_kwh - start_kwh, 2)
    
    # 3. Generate HTML Rows
    rows_html = ""
    # Show first 20 rows to verify data flow
    for idx, row in df.head(20).iterrows():
        rows_html += f"""
        <tr>
            <td>{row['ts']}</td>
            <td><b>{row['TotalEP']}</b></td>
            <td>{row['ImpEP']}</td>
            <td>{row['ExpEP']}</td>
            <td>{row['TotalEQ']}</td>
        </tr>
        """

    html = HTML_TEMPLATE.format(
        mid=TARGET_ID,
        table_name=table_name,
        total_kwh=daily_consumption,
        start_read=start_kwh,
        end_read=end_kwh,
        row_count=len(df),
        rows=rows_html
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Success! Energy Report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    analyze_source_energy()