import os
import sys
import argparse
import webbrowser
from pathlib import Path
import mysql.connector
import json
from datetime import datetime

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from db.mysql_env import TABLE_PREFIX, mysql_connect_kwargs

DB_CONFIG = mysql_connect_kwargs()

# IDs to explicitly exclude from End Meter reports (Solar and Storage)
EXCLUDED_IDS = [56, 29, 31, 30]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_end_meters(node, end_meter_list=None):
    """Recursively traverses relation.json and extracts ONLY nodes with no sub_nodes, 
    excluding specific IDs related to power sources/storage."""
    if end_meter_list is None:
        end_meter_list = []
    
    sub_nodes = node.get("sub_nodes", [])
    node_id = node.get("id")
    
    # If the node has NO sub_nodes, it is a Leaf Node
    if not sub_nodes:
        # Only add to the list if an ID exists and it is NOT in our exclusion list
        if node_id is not None and node_id not in EXCLUDED_IDS:
            end_meter_list.append({
                "id": node_id,
                "name": node.get("name", f"Node {node_id}"),
                "category": node.get("category", "Unknown")
            })
    else:
        # If it has sub_nodes, it's a Branch. Recurse down.
        # We also check if the Branch itself is in the exclusion list (rare, but safe)
        for sub in sub_nodes:
            get_end_meters(sub, end_meter_list)
        
    return end_meter_list

def get_table_columns(cursor, table_name):
    """Fetches dynamic column names and clears the cursor."""
    try:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
        ignore = ['id', 'meter_number', 'mac_address', 'mac_index', 'address_485_dec']
        columns = [desc[0] for desc in cursor.description if desc[0] not in ignore]
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

def main(date_suffix, open_browser=True):
    report_file = os.path.join(OUTPUT_DIR, f"endmeters-{date_suffix}.html")
    print(f"Starting Filtered End Meter Data Matrix for 20{date_suffix}...")

    # 1. Load Topology & Filter for End Meters (Excluding Solar/Storage)
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            tree_data = json.load(f)
        end_nodes = get_end_meters(tree_data.get("root_node", {}))
    except Exception as e:
        print(f"❌ JSON Error: {e}")
        return

    # 2. Database Processing
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        power_table = f"{TABLE_PREFIX}_power_{date_suffix}"
        power_cols = get_table_columns(cursor, power_table)
        
        results = []
        print(f"Scanning {len(end_nodes)} filtered end meters against {power_table}...")
        for node in end_nodes:
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
        <meta charset="UTF-8"><title>AIENERGY - End Meters Matrix / 末端电表矩阵</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
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
                <h1 class="text-3xl font-black text-slate-900">Filtered End Meters / 末端电表矩阵</h1>
                <p class="text-slate-500 mt-2 text-sm">Target Table / 数据表: <code class="bg-slate-200 px-2 py-1 rounded">site_power_{date_suffix}</code> | <strong>Note / 说明:</strong> Solar PV &amp; storage excluded / 已排除光伏与储能节点.</p>
            </header>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-8">
                <div class="bg-slate-900 p-4 flex justify-between items-center text-white">
                    <div><h2 class="text-lg font-bold tracking-widest">Machine Telemetry / 设备遥测状态</h2></div>
                    <div class="flex gap-4 text-xs">
                        <span class="flex items-center gap-1"><div class="w-3 h-3 rounded bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold text-[8px]">✓</div> Active / 有效</span>
                        <span class="flex items-center gap-1"><div class="w-3 h-3 rounded bg-rose-50 text-rose-500 flex items-center justify-center font-bold text-[8px]">0</div> Zero / Null / 零或空</span>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse min-w-max">
                        <thead class="bg-slate-50 sticky top-0 shadow-sm z-10">
                            <tr>
                                <th class="px-4 py-3 border-b border-slate-200 text-xs font-bold text-slate-600 w-16">Node / 节点</th>
                                <th class="px-4 py-3 border-b border-slate-200 text-xs font-bold text-slate-600">Hardware Name / 设备名称</th>
                                <th class="px-4 py-3 border-b border-slate-200 text-xs font-bold text-slate-600">Category / 类别</th>
                                {headers}
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-50">{rows}</tbody>
                    </table>
                </div>
            </section>
            <footer class="text-center text-slate-400 text-[10px] py-6">
                Generated by AIENERGY System / AI能源系统生成 • End Meters Matrix / 末端电表矩阵
            </footer>
        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Filtered Matrix generated successfully at: {report_file}")
    if open_browser:
        webbrowser.open(Path(report_file).resolve().as_uri())
    return report_file

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="260201", help="Target date (YYMMDD)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser after generating")
    args = parser.parse_args()
    main(args.date, open_browser=not args.no_browser)