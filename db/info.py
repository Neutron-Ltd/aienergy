import os
import sys
from datetime import datetime
from pathlib import Path

import mysql.connector
import pandas as pd

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
from db.mysql_env import mysql_connect_kwargs

# --- CONFIGURATION ---
_c = mysql_connect_kwargs()
DB_HOST = _c["host"]
DB_PORT = _c["port"]
DB_USER = _c["user"]
DB_PASS = _c["password"]
DB_NAME = _c["database"]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "info.html")

TARGET_TABLES = [
    "meter_info", 
    "meter_type", 
    "power_factor_penalty", 
    "power_factor_reduction", 
    "site_power_260223", 
    "site_sessions", 
    "site_users", 
    "electricity_pricing"
]

# --- DEFINITIONS & METADATA (For Tooltips) ---
DEFINITIONS = {
    "meter_info": {
        "_desc": "The central inventory of all smart meters in the network.",
        "id": "Unique system identifier for the meter.",
        "meter_name": "Human-readable label (e.g., 'Factory 1 Main Incomer').",
        "mac_address": "Physical MAC address of the communication module.",
        "meter_number": "Unique logical number used to link data logs to this device.",
        "type_code": "Links to meter_type to define hardware model/protocol.",
        "ratio": "CT/PT Multiplier. Real Value = Raw Reading * ratio."
    },
    "meter_type": {
        "_desc": "Lookup table defining supported hardware models.",
        "type_code": "Primary key referenced by meter_info.",
        "type_name": "The specific model name or protocol standard."
    },
    "site_power_260223": {
        "_desc": "High-frequency telemetry log for Power Quality (Snapshots).",
        "ts": "Timestamp of the reading.",
        "meter_number": "Foreign key linking back to meter_info.",
        "Uan": "Phase A Voltage (Line-to-Neutral).",
        "Ubn": "Phase B Voltage (Line-to-Neutral).",
        "Ucn": "Phase C Voltage (Line-to-Neutral).",
        "Uab": "Line Voltage (Phase A to Phase B).",
        "Ubc": "Line Voltage (Phase B to Phase C).",
        "Uca": "Line Voltage (Phase C to Phase A).",
        "Ia": "Current for Phase A.",
        "Ib": "Current for Phase B.",
        "Ic": "Current for Phase C.",
        "Pt": "Total Active Power (kW). The actual work being done.",
        "Qt": "Total Reactive Power (kvar). Magnetic/wasted power.",
        "St": "Apparent Power (kVA). Total grid capacity utilized.",
        "FPt": "Power Factor (0.0 - 1.0). Overall efficiency score.",
        "freq": "Grid Frequency in Hz (Standard is 50.00Hz)."
    },
    "electricity_pricing": {
        "_desc": "Hardcoded pricing rules enabling 'Shadow Billing' verification.",
        "user_category": "Customer type (e.g., 'Large Industrial').",
        "voltage_level": "Service voltage tier (e.g., '1-10kV').",
        "peak_period_price": "Rate during highest grid demand hours.",
        "valley_period_price": "Rate during night/low-demand hours.",
        "deep_valley_price": "Discounted super-off-peak rate.",
        "max_demand_charge": "Fee based on highest peak power (kW) hit in the month."
    },
    "power_factor_penalty": {
        "_desc": "Defines fines applied for poor electrical efficiency.",
        "power_factor": "Threshold below which penalties trigger.",
        "penalty_percentage": "Multiplier added to the final bill."
    },
    "power_factor_reduction": {
        "_desc": "Defines financial rewards for high electrical efficiency.",
        "power_factor": "Threshold above which rewards trigger.",
        "reduction_percentage": "Discount subtracted from the final bill."
    },
    "site_users": {
        "_desc": "Standard user accounts for the dashboard application.",
        "id": "Unique user ID.",
        "username": "Login credential.",
        "password_hash": "Encrypted password string.",
        "roles": "User permission level (admin, regular)."
    },
    "site_sessions": {
        "_desc": "Active session tracking for security auditing.",
        "session_id": "Unique token for the active session.",
        "user_id": "Links to site_users.",
        "expires_at": "Timestamp when the session token invalidates.",
        "ip_address": "IP address of the user device."
    }
}

def get_definition(table, column=None):
    """Retrieves tooltip text for a table or column."""
    table_defs = DEFINITIONS.get(table, {})
    if column is None:
        return table_defs.get("_desc", "Table containing system records.")
    return table_defs.get(column, "Standard data column.")

def fetch_table_data(cursor, table_name, limit=5):
    """Fetches sample data from a given table."""
    try:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
        columns = [col[0] for col in cursor.description]
        data = cursor.fetchall()
        return pd.DataFrame(data, columns=columns)
    except Exception as e:
        print(f"Warning: Could not fetch data for {table_name}: {e}")
        return pd.DataFrame()

def generate_html_report(tables_data):
    """Generates the comprehensive HTML file."""
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Unified EMS - Database Info</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script type="module">
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
            mermaid.initialize({{ startOnLoad: true }});
        </script>
        <style>
            .tooltip-container {{
                position: relative;
                display: inline-block;
                cursor: help;
                margin-left: 4px;
                color: #3b82f6; /* Tailwind blue-500 */
            }}
            .tooltip-container .tooltip-text {{
                visibility: hidden;
                width: max-content;
                max-width: 250px;
                background-color: #1e293b;
                color: #fff;
                text-align: left;
                border-radius: 6px;
                padding: 8px 12px;
                position: absolute;
                z-index: 50;
                left: 50%;
                transform: translateX(-50%);
                opacity: 0;
                transition: opacity 0.3s;
                font-size: 0.75rem;
                font-weight: normal;
                box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
                white-space: normal;
            }}
            .tooltip-container:hover .tooltip-text {{
                visibility: visible;
                opacity: 1;
            }}

            /* TOOLTIP UP (For the dark header) */
            .tooltip-up .tooltip-text {{
                bottom: 125%;
            }}
            .tooltip-up .tooltip-text::after {{
                content: "";
                position: absolute;
                top: 100%;
                left: 50%;
                margin-left: -5px;
                border-width: 5px;
                border-style: solid;
                border-color: #1e293b transparent transparent transparent;
            }}

            /* TOOLTIP DOWN (For table columns to avoid clipping and overlap) */
            .tooltip-down .tooltip-text {{
                top: 125%;
            }}
            .tooltip-down .tooltip-text::after {{
                content: "";
                position: absolute;
                bottom: 100%;
                left: 50%;
                margin-left: -5px;
                border-width: 5px;
                border-style: solid;
                border-color: transparent transparent #1e293b transparent;
            }}

            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
            th {{ background-color: #f8fafc; font-weight: 600; color: #475569; }}
            tr:hover {{ background-color: #f1f5f9; }}
        </style>
    </head>
    <body class="bg-slate-50 text-slate-800 font-sans p-8">
        <div class="max-w-7xl mx-auto space-y-12">
            
            <header class="text-center space-y-4">
                <h1 class="text-4xl font-extrabold text-slate-900">Unified EMS Database Explorer</h1>
                <p class="text-slate-500">Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            </header>

            <section class="bg-white p-8 rounded-xl shadow-sm border border-slate-200">
                <h2 class="text-2xl font-bold mb-6 text-slate-800 border-b pb-2">Table Relationships Map</h2>
                <div class="flex justify-center">
                    <div class="mermaid">
                        erDiagram
                            meter_info ||--|| meter_type : "type_code"
                            site_power_260223 }}|--|| meter_info : "meter_number"
                            site_sessions }}|--|| site_users : "user_id"
                            
                            electricity_pricing {{
                                string user_category
                                string voltage_level
                            }}
                            power_factor_penalty {{
                                float power_factor
                            }}
                            power_factor_reduction {{
                                float power_factor
                            }}
                    </div>
                </div>
            </section>

            <div class="space-y-8">
    """

    for table_name, df in tables_data.items():
        if df.empty:
            continue
            
        table_desc = get_definition(table_name)
        
        html_content += f"""
                <section class="bg-white rounded-xl shadow-sm border border-slate-200">
                    <div class="bg-slate-800 rounded-t-xl p-4 text-white flex items-center justify-between z-10 relative">
                        <div class="flex items-center space-x-2">
                            <h3 class="text-lg font-bold font-mono">{table_name}</h3>
                            <div class="tooltip-container tooltip-up text-slate-300 hover:text-white">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
                                </svg>
                                <span class="tooltip-text">{table_desc}</span>
                            </div>
                        </div>
                        <span class="text-xs font-semibold px-2 py-1 bg-slate-700 rounded-full">{len(df)} Sample Rows</span>
                    </div>
                    
                    <div class="overflow-x-auto relative z-0">
                        <table class="w-full text-sm">
                            <thead>
                                <tr>
        """
        
        # Headers with downward tooltips
        for col in df.columns:
            col_desc = get_definition(table_name, col)
            html_content += f"""
                                    <th class="whitespace-nowrap">
                                        {col}
                                        <div class="tooltip-container tooltip-down">
                                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-4 h-4 inline-block align-text-bottom">
                                              <path stroke-linecap="round" stroke-linejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
                                            </svg>
                                            <span class="tooltip-text font-normal">{col_desc}</span>
                                        </div>
                                    </th>
            """
        
        html_content += """
                                </tr>
                            </thead>
                            <tbody>
        """
        
        # Data Rows
        for _, row in df.iterrows():
            html_content += "<tr>"
            for val in row:
                html_content += f"<td>{str(val)}</td>"
            html_content += "</tr>"
            
        html_content += """
                            </tbody>
                        </table>
                    </div>
                </section>
        """

    html_content += """
            </div>
        </div>
    </body>
    </html>
    """
    
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Save file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"✅ Success! Report generated at: {OUTPUT_FILE}")

def main():
    print(f"Connecting to {DB_NAME} at {DB_HOST}...")
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        cursor = conn.cursor()
        print("✅ Connection successful. Fetching data...")
        
        tables_data = {}
        for table in TARGET_TABLES:
            df = fetch_table_data(cursor, table, limit=5)
            if not df.empty:
                tables_data[table] = df
            else:
                tables_data[table] = pd.DataFrame([{"Message": f"No data found in {table} or table does not exist."}])
                
        cursor.close()
        conn.close()
        
        generate_html_report(tables_data)
        
    except mysql.connector.Error as err:
        print(f"❌ Database Error: {err}")
    except Exception as e:
        print(f"❌ Application Error: {e}")

if __name__ == "__main__":
    main()