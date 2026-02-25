import os
import argparse
import mysql.connector
import json
from datetime import datetime  # <-- Added missing import

# --- CONFIGURATION ---
DB_CONFIG = {
    "host": "localhost",
    "port": 3307,
    "user": "energy_user",
    "password": "REDACTED",
    "database": "unified"
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Read from root
RELATION_JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
# Output to root
OUTPUT_JSON_FILE = os.path.join(SCRIPT_DIR, "..", "column.json")

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
        ignore = ['id', 'meter_number', 'mac_address', 'mac_index', 'address_485_dec']
        columns = [desc[0] for desc in cursor.description if desc[0] not in ignore]
        cursor.fetchall() # Clear buffer
        return columns
    except Exception as e:
        print(f"Error fetching columns: {e}")
        return []

def get_active_columns(cursor, table_name, node_id, columns, date_suffix):
    """Fetches one bounded row and returns a list of columns that have actual data."""
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

    active_cols = []
    
    if row:
        for col in columns:
            val = row.get(col)
            # Only append if value is meaningful (Not None, Not blank, Not exactly 0.0)
            if val is not None and val != "" and not (isinstance(val, (int, float)) and float(val) == 0.0):
                active_cols.append(col)
                
    return active_cols

def main(date_suffix):
    print(f"🔍 Scanning telemetry for 20{date_suffix} to build column dictionary...")

    # 1. Load Topology
    try:
        with open(RELATION_JSON_FILE, 'r', encoding='utf-8') as f:
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
        
        telemetry_map = {
            "metadata": {
                "generated_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_table": power_table,
                "total_nodes_scanned": len(all_nodes)
            },
            "nodes": {}
        }

        print(f"⚙️ Pinging {len(all_nodes)} nodes. Please wait...")
        for node in all_nodes:
            nid = str(node['id']) # Use string for JSON keys
            active = get_active_columns(cursor, power_table, node['id'], power_cols, date_suffix)
            
            telemetry_map["nodes"][nid] = {
                "name": node['name'],
                "category": node['category'],
                "active_columns": active,
                "is_online": len(active) > 0
            }
                
        conn.close()
    except Exception as e:
        print(f"❌ Database Error: {e}")
        return

    # 3. Export to JSON
    try:
        with open(OUTPUT_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(telemetry_map, f, indent=4, ensure_ascii=False)
        print(f"✅ Success! Telemetry dictionary saved to: {OUTPUT_JSON_FILE}")
    except Exception as e:
        print(f"❌ Error saving JSON: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="260201", help="Target date (YYMMDD)")
    args = parser.parse_args()
    main(args.date)