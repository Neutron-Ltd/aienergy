import os
import mysql.connector
import pandas as pd
from datetime import datetime

# --- CONFIGURATION ---
DB_HOST = "localhost"
DB_PORT = 3307
DB_USER = "energy_user"
DB_PASS = "REDACTED"
DB_NAME = "unified"

# Follows the project convention: output to meter/data/
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "all.html")

# --- DEFINITIONS & METADATA (For Tooltips) ---
COLUMN_DEFINITIONS = {
    "id": "Unique system identifier for the meter.",
    "meter_name": "Human-readable label (e.g., 'Factory 1 Main Incomer').",
    "mac_address": "Physical MAC address of the communication module.",
    "meter_number": "Unique logical number used to link data logs to this device.",
    "ratio": "CT/PT Multiplier. Critical for calculation: Real Value = Raw Reading * ratio.",
    "type_name": "The specific hardware model name or protocol standard.",
    "type_code": "System ID for the hardware model type."
}

def get_definition(column):
    """Retrieves tooltip text for a column."""
    return COLUMN_DEFINITIONS.get(column, "Standard data column.")

def fetch_all_meters(cursor):
    """Fetches all meters by joining meter_info and meter_type."""
    query = """
        SELECT 
            m.id, 
            m.meter_name, 
            m.mac_address, 
            m.meter_number, 
            m.type_code,
            t.type_name,
            m.ratio
        FROM meter_info m
        LEFT JOIN meter_type t ON m.type_code = t.type_code
        ORDER BY m.id ASC;
    """
    try:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        data = cursor.fetchall()
        return pd.DataFrame(data, columns=columns)
    except Exception as e:
        print(f"Warning: Could not fetch meter data: {e}")
        return pd.DataFrame()

def generate_html_report(df):
    """Generates the HTML file displaying all meters."""
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Unified EMS - All Meters</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            .tooltip-container {{
                position: relative;
                display: inline-block;
                cursor: help;
                margin-left: 4px;
                color: #3b82f6;
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
        <div class="max-w-7xl mx-auto space-y-8">
            
            <header class="text-center space-y-4">
                <h1 class="text-4xl font-extrabold text-slate-900">Registered Device Inventory</h1>
                <p class="text-slate-500">Comprehensive list of all smart meters across the network. Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            </header>

            <section class="bg-white rounded-xl shadow-sm border border-slate-200">
                <div class="bg-slate-800 rounded-t-xl p-4 text-white flex items-center justify-between z-10 relative">
                    <div class="flex items-center space-x-2">
                        <h3 class="text-lg font-bold font-mono">meter_info & meter_type Join</h3>
                    </div>
                    <span class="text-xs font-semibold px-2 py-1 bg-slate-700 rounded-full">Total Meters: {len(df)}</span>
                </div>
                
                <div class="overflow-x-auto relative z-0">
    """
    
    if df.empty:
        html_content += """
                    <div class="p-8 text-center text-slate-500">
                        No meter data found in the database.
                    </div>
        """
    else:
        html_content += """
                    <table class="w-full text-sm">
                        <thead>
                            <tr>
        """
        
        # Table Headers with Tooltips
        for col in df.columns:
            col_desc = get_definition(col)
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
        
        # Table Rows
        for _, row in df.iterrows():
            html_content += "<tr>"
            for val in row:
                html_content += f"<td>{str(val) if pd.notna(val) else '-'}</td>"
            html_content += "</tr>"
            
        html_content += """
                        </tbody>
                    </table>
        """

    html_content += """
                </div>
            </section>
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
        print("✅ Connection successful. Fetching meter data...")
        
        df = fetch_all_meters(cursor)
        
        cursor.close()
        conn.close()
        
        generate_html_report(df)
        
    except mysql.connector.Error as err:
        print(f"❌ Database Error: {err}")
    except Exception as e:
        print(f"❌ Application Error: {e}")

if __name__ == "__main__":
    main()