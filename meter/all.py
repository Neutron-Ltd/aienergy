import os
import mysql.connector
import pandas as pd
import json
from datetime import datetime

# --- CONFIGURATION ---
DB_HOST = "localhost"
DB_PORT = 3307
DB_USER = "energy_user"
DB_PASS = "REDACTED"
DB_NAME = "unified"

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "all.html")
JSON_FILE = os.path.join(os.path.dirname(__file__), "../relation.json")

COLUMN_DEFINITIONS = {
    "id": "Unique system identifier for the meter.",
    "meter_name": "Human-readable label.",
    "mac_address": "Physical MAC address.",
    "meter_number": "Logical ID for linking data logs.",
    "ratio": "Multiplier: Real Value = Raw Reading * ratio.",
    "type_name": "Hardware model or protocol.",
}

def get_definition(column):
    return COLUMN_DEFINITIONS.get(column, "Standard data column.")

def parse_relation_groups():
    """Flattens the JSON hierarchy to map meter IDs to their top-level groups."""
    with open(JSON_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    mapping = {}
    
    def walk(node, group_name):
        if "id" in node:
            mapping[node["id"]] = group_name
        for sub in node.get("sub_nodes", []):
            walk(sub, group_name)

    # Use the immediate children of the root node as group headers
    for root_child in data["root_node"]["sub_nodes"]:
        walk(root_child, root_child["name"])
    
    return mapping

def fetch_meter_data(cursor):
    query = """
        SELECT m.id, m.meter_name, m.mac_address, m.meter_number, t.type_name, m.ratio
        FROM meter_info m
        LEFT JOIN meter_type t ON m.type_code = t.type_code
        ORDER BY m.id ASC;
    """
    cursor.execute(query)
    columns = [col[0] for col in cursor.description]
    return pd.DataFrame(cursor.fetchall(), columns=columns)

def generate_html_report(df, group_mapping):
    # Assign groups based on the JSON mapping
    df['group'] = df['id'].map(group_mapping).fillna("Uncategorized / Other")
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>EMS - Grouped Inventory</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            .tooltip-container {{ position: relative; display: inline-block; cursor: help; color: #3b82f6; }}
            .tooltip-text {{ visibility: hidden; width: 200px; background: #1e293b; color: #fff; border-radius: 6px; padding: 8px; position: absolute; z-index: 50; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.3s; font-size: 0.7rem; }}
            .tooltip-container:hover .tooltip-text {{ visibility: visible; opacity: 1; }}
            th {{ background-color: #f8fafc; font-weight: 600; color: #475569; padding: 10px; border-bottom: 2px solid #e2e8f0; }}
            td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; }}
        </style>
    </head>
    <body class="bg-slate-50 p-8">
        <div class="max-w-7xl mx-auto space-y-12">
            <header class="text-center">
                <h1 class="text-3xl font-bold text-slate-900">Hierarchical Device Inventory</h1>
                <p class="text-slate-500">Grouped by Power Grid Relationships | {datetime.now().strftime("%Y-%m-%d")}</p>
            </header>
    """

    for group, group_df in df.groupby('group', sort=False):
        html_content += f"""
            <section class="bg-white rounded-xl shadow-md border overflow-hidden">
                <div class="bg-slate-700 p-4 text-white flex justify-between items-center">
                    <h2 class="text-lg font-bold uppercase tracking-wide">{group}</h2>
                    <span class="bg-slate-600 px-3 py-1 rounded-full text-xs">{len(group_df)} Nodes</span>
                </div>
                <table class="w-full text-sm">
                    <thead><tr>
        """
        for col in [c for c in group_df.columns if c != 'group']:
            html_content += f"""
                        <th>{col}<div class="tooltip-container">
                            <span class="ml-1">ⓘ</span>
                            <span class="tooltip-text">{get_definition(col)}</span>
                        </div></th>"""
        
        html_content += "</tr></thead><tbody>"
        for _, row in group_df.iterrows():
            html_content += "<tr>" + "".join([f"<td>{row[c]}</td>" for c in group_df.columns if c != 'group']) + "</tr>"
        html_content += "</tbody></table></section>"

    html_content += "</div></body></html>"
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Success! Grouped report generated at: {OUTPUT_FILE}")

def main():
    try:
        conn = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME)
        df = fetch_meter_data(conn.cursor())
        group_mapping = parse_relation_groups()
        generate_html_report(df, group_mapping)
        conn.close()
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()