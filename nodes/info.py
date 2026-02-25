import os
import json

# --- DIRECTORY CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Look one level up for relation.json (in the root folder)
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
# Output to nodes/data/info.html
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "info.html")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_category_style(category):
    """Assigns specific AIENERGY UI badge colors based on node category."""
    cat = str(category).lower()
    if 'transformer' in cat: return "bg-purple-100 text-purple-700 border-purple-200"
    if 'distribution_hub' in cat: return "bg-blue-100 text-blue-700 border-blue-200"
    if 'pv_generation' in cat: return "bg-emerald-100 text-emerald-700 border-emerald-200"
    if 'safety' in cat: return "bg-rose-100 text-rose-700 border-rose-200"
    if 'lighting' in cat: return "bg-amber-100 text-amber-700 border-amber-200"
    if 'main_load' in cat: return "bg-indigo-100 text-indigo-700 border-indigo-200"
    if 'charging' in cat: return "bg-cyan-100 text-cyan-700 border-cyan-200"
    if 'residential' in cat: return "bg-teal-100 text-teal-700 border-teal-200"
    if 'branch' in cat: return "bg-slate-100 text-slate-700 border-slate-300"
    return "bg-slate-100 text-slate-600 border-slate-200" # Default

def generate_nested_table(node, level=0):
    """Recursively generates nested HTML tables for the JSON tree."""
    node_id = node.get('id', 'N/A')
    name = node.get('name', 'Unknown Node')
    category = node.get('category', 'root')
    sub_nodes = node.get('sub_nodes', [])
    
    # UI Styling based on depth
    bg_color = "bg-white" if level == 0 else "bg-slate-50/50"
    border_left = "border-l-4 border-l-blue-500" if level == 0 else "border-l-2 border-l-slate-300"
    cat_style = get_category_style(category)
    
    # Is it an End Meter (Leaf)?
    is_end_meter = len(sub_nodes) == 0
    meter_badge = "<span class='ml-2 px-2 py-0.5 text-[9px] bg-slate-800 text-white rounded uppercase tracking-widest'>End Meter</span>" if is_end_meter else ""
    
    html = f"""
    <table class="w-full text-sm text-left border border-slate-200 rounded-lg overflow-hidden mb-2 shadow-sm {bg_color}">
        <tbody>
            <tr class="hover:bg-slate-100 transition-colors">
                <td class="p-3 w-24 font-mono text-xs text-slate-500 {border_left}">ID: {node_id}</td>
                <td class="p-3 font-semibold text-slate-800">{name} {meter_badge}</td>
                <td class="p-3 w-48 text-right">
                    <span class="px-3 py-1 text-[10px] uppercase tracking-wider font-bold rounded-full border {cat_style}">
                        {category}
                    </span>
                </td>
            </tr>
    """
    
    # If there are sub_nodes, create a nested table spanning all columns
    if sub_nodes:
        html += """
            <tr>
                <td colspan="3" class="p-0 border-t border-slate-200">
                    <div class="pl-8 py-3 pr-3 bg-slate-50 border-l border-slate-200 inset-shadow">
                        <div class="text-[10px] font-bold text-slate-400 mb-2 uppercase tracking-widest flex items-center gap-2">
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                            Connected Sub-Nodes
                        </div>
        """
        for sub in sub_nodes:
            html += generate_nested_table(sub, level + 1)
        html += """
                    </div>
                </td>
            </tr>
        """
        
    html += """
        </tbody>
    </table>
    """
    return html

def main():
    print(f"Loading topology from {JSON_FILE}...")
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        root_node = data.get("root_node", {})
    except Exception as e:
        print(f"❌ Error loading JSON: {e}")
        return

    # Generate the recursive HTML body
    topology_html = generate_nested_table(root_node, level=0)

    # --- THE AIENERGY UI TEMPLATE ---
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>AIENERGY - Topology Map</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            .inset-shadow {{ box-shadow: inset 0 2px 4px 0 rgba(0, 0, 0, 0.02); }}
            /* Custom scrollbar */
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        
        <div class="max-w-7xl mx-auto mt-8 px-4">
            <header class="mb-8 flex justify-between items-end">
                <div>
                    <h1 class="text-3xl font-black text-slate-900">Hierarchical Network Info</h1>
                    <p class="text-slate-500 mt-2 text-sm">Dynamically generated from <code class="bg-slate-200 px-1 py-0.5 rounded text-slate-700">relation.json</code></p>
                </div>
                <div class="flex gap-2 text-xs font-bold uppercase tracking-widest">
                    <span class="px-3 py-1 bg-white border border-slate-200 rounded text-slate-600 shadow-sm">Tree Depth Matrix</span>
                </div>
            </header>

            <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                {topology_html}
            </div>
        </div>
        
    </body>
    </html>
    """

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"✅ AIENERGY UI Topology Report generated successfully at: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()