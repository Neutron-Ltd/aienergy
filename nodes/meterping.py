import os
import argparse
import webbrowser
from pathlib import Path
import mysql.connector
import json
from datetime import datetime

# --- CONFIGURATION ---
DB_CONFIG = {
    "host": "localhost",
    "port": 3307,
    "user": "energy_user",
    "password": "REDACTED",
    "database": "unified"
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def flatten_json_tree(node, node_list=None):
    """Recursively flattens relation.json into a flat list of nodes."""
    if node_list is None:
        node_list = []
    
    if "id" in node:
        node_list.append({
            "id": node["id"],
            "name": node.get("name", f"Node {node['id']}"),
            "category": node.get("category", "Unknown")
        })
        
    for sub in node.get("sub_nodes", []):
        flatten_json_tree(sub, node_list)
        
    return node_list

def get_table_columns(cursor, table_name):
    """Fetches dynamic column names and clears the cursor."""
    try:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
        # Ignore noisy system columns, focus on telemetry
        ignore = ['id', 'meter_number', 'mac_address', 'mac_index', 'address_485_dec']
        columns = [desc[0] for desc in cursor.description if desc[0] not in ignore]
        
        # CRITICAL FIX: We MUST fetch the row to clear the MySQL cursor buffer
        cursor.fetchall() 
        return columns
    except Exception as e:
        print(f"Error fetching columns: {e}")
        return []

def check_node_data(cursor, table_name, node_id, columns, date_suffix):
    """Fetches one bounded row to determine the Active/Inactive status of each column."""
    try:
        query = f"""
            SELECT * FROM {table_name} 
            WHERE meter_number = %s 
            AND ts >= '20{date_suffix[0:2]}-{date_suffix[2:4]}-{date_suffix[4:6]} 00:00:00'
            AND ts <= '20{date_suffix[0:2]}-{date_suffix[2:4]}-{date_suffix[4:6]} 23:59:59'
            ORDER BY ts DESC 
            LIMIT 1
        """
        cursor.execute(query, (node_id,))
        row = cursor.fetchone()
    except Exception as e:
        print(f"Error checking node {node_id}: {e}")
        row = None

    status_map = {}
    row_exists = bool(row)
    
    for col in columns:
        if not row_exists:
            status_map[col] = "MISSING"
            continue
            
        val = row.get(col)
        # Check if the value is meaningful (Not None, Not exactly 0)
        if val is None or val == "" or (isinstance(val, (int, float)) and float(val) == 0.0):
            status_map[col] = "INACTIVE"
        else:
            status_map[col] = "ACTIVE"
            
    return status_map, row_exists

def build_status_ui(status):
    """Returns the AIENERGY UI badge for a given status."""
    if status == "ACTIVE":
        return '<div class="mx-auto w-5 h-5 rounded bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold text-[10px] shadow-inner">✓</div>'
    elif status == "INACTIVE":
        return '<div class="mx-auto w-5 h-5 rounded bg-rose-50 text-rose-500 flex items-center justify-center font-bold text-[10px] border border-rose-100">0</div>'
    else:
        return '<div class="mx-auto w-5 h-5 rounded bg-slate-100 text-slate-300 flex items-center justify-center font-bold text-[10px]">-</div>'

def main(date_suffix):
    report_file = os.path.join(OUTPUT_DIR, f"meterping-{date_suffix}.html")
    print(f"Starting Universal Data Matrix for 20{date_suffix}...")

    # 1. Load Topology
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            tree_data = json.load(f)
        all_nodes = flatten_json_tree(tree_data.get("root_node", {}))
        
        # Ensure Root Node is included
        root_id = tree_data.get("root_node", {}).get("id")
        if root_id and root_id not in [n["id"] for n in all_nodes]:
            all_nodes.insert(0, {
                "id": root_id, 
                "name": tree_data.get("root_node", {}).get("name", f"Node {root_id}"), 
                "category": "Root"
            })
    except Exception as e:
        print(f"❌ JSON Error: {e}")
        return

    # 2. Database Processing
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        power_table = f"site_power_{date_suffix}"
        power_cols = get_table_columns(cursor, power_table)
        
        results = []

        print(f"Scanning {len(all_nodes)} nodes against {power_table}...")
        for node in all_nodes:
            status_map, exists = check_node_data(cursor, power_table, node['id'], power_cols, date_suffix)
            results.append((node, status_map, exists))
                
        conn.close()
    except Exception as e:
        print(f"❌ Database Error: {e}")
        return

    # --- UI GENERATION ---
    headers = "".join([f"<th class='px-2 py-3 border-b border-slate-200 text-center font-mono text-[9px] uppercase text-slate-500 tracking-tighter cursor-help' title='Column: {col}'>{col}</th>" for col in power_cols])
    
    rows = ""
    for node, status_map, exists in results:
        bg_color = "bg-white" if exists else "bg-slate-50/50"
        text_color = "text-slate-800" if exists else "text-slate-400"
        
        cells = "".join([f"<td class='px-1 py-2 border-b border-slate-100'>{build_status_ui(status_map[col])}</td>" for col in power_cols])
        
        rows += f"""
        <tr class="hover:bg-slate-50 transition-colors {bg_color}">
            <td class="px-4 py-2 border-b border-slate-100 font-mono text-xs text-slate-500 w-16">ID:{node['id']}</td>
            <td class="px-4 py-2 border-b border-slate-100 text-xs {text_color} font-medium whitespace-nowrap overflow-hidden text-ellipsis max-w-[200px]" title="{node['name']}">{node['name']}</td>
            <td class="px-4 py-2 border-b border-slate-100 text-[10px] uppercase text-slate-400 tracking-widest">{node['category']}</td>
            {cells}
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>AIENERGY - Data Matrix</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
            /* Print settings */
            @media print {{
                @page {{ size: landscape; margin: 10mm; }}
                body {{ background: white; }}
                .shadow-sm {{ box-shadow: none; border-color: #e2e8f0; }}
            }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        
        <div class="max-w-[1600px] mx-auto mt-8 px-6">
            <header class="mb-8">
                <h1 class="text-3xl font-black text-slate-900">Universal Column Ping Test</h1>
                <p class="text-slate-500 mt-2 text-sm">Target Table: <code class="bg-slate-200 px-2 py-1 rounded">site_power_{date_suffix}</code> | No exceptions. All JSON nodes scanned.</p>
            </header>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-8">
                <div class="bg-slate-900 p-4 flex justify-between items-center text-white">
                    <div>
                        <h2 class="text-lg font-bold tracking-widest">Hardware Telemetry Status</h2>
                    </div>
                    <div class="flex gap-4 text-xs">
                        <span class="flex items-center gap-1"><div class="w-3 h-3 rounded bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold text-[8px]">✓</div> Active Data</span>
                        <span class="flex items-center gap-1"><div class="w-3 h-3 rounded bg-rose-50 text-rose-500 flex items-center justify-center font-bold text-[8px]">0</div> Zero / Null</span>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse min-w-max">
                        <thead class="bg-slate-50 sticky top-0 shadow-sm z-10">
                            <tr>
                                <th class="px-4 py-3 border-b border-slate-200 text-xs font-bold text-slate-600 w-16">Node</th>
                                <th class="px-4 py-3 border-b border-slate-200 text-xs font-bold text-slate-600">Hardware Name</th>
                                <th class="px-4 py-3 border-b border-slate-200 text-xs font-bold text-slate-600">Category</th>
                                {headers}
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-50">
                            {rows}
                        </tbody>
                    </table>
                </div>
            </section>
        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ AIENERGY Data Matrix generated successfully at: {report_file}")
    webbrowser.open(Path(report_file).resolve().as_uri())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="260201", help="Target date (YYMMDD)")
    args = parser.parse_args()
    main(args.date)