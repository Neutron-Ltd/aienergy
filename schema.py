import mysql.connector
import os

# 1. Configuration
config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

OUTPUT_FILE = "full_schema_python.sql"

def dump_schema():
    print(f"Connecting to {config['host']}...")
    
    try:
        conn = mysql.connector.connect(**config)
        cursor = conn.cursor()
        
        # Open file to write
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            # Header
            f.write(f"-- Schema Dump for database: {config['database']}\n")
            f.write("-- Generated via Python Script\n")
            f.write("-- ------------------------------------------------------\n\n")

            # 2. Get List of All Tables and Views
            # SHOW FULL TABLES returns: [Name, Table_Type]
            cursor.execute("SHOW FULL TABLES")
            all_objects = cursor.fetchall()
            
            total_count = len(all_objects)
            print(f"Found {total_count} objects (Tables & Views). Starting export...")

            for index, (obj_name, obj_type) in enumerate(all_objects, 1):
                print(f"[{index}/{total_count}] Processing {obj_type}: {obj_name}")
                
                try:
                    # 3. Get the CREATE statement
                    # This works for both TABLES and VIEWS
                    cursor.execute(f"SHOW CREATE TABLE `{obj_name}`")
                    result = cursor.fetchone()
                    
                    if result:
                        # result[1] contains the actual SQL 'CREATE TABLE ...' string
                        create_statement = result[1]
                        
                        f.write(f"-- Structure for {obj_type}: {obj_name}\n")
                        f.write(f"DROP {obj_type} IF EXISTS `{obj_name}`;\n")
                        f.write(f"{create_statement};\n\n")
                        
                except mysql.connector.Error as err:
                    print(f"  Warning: Could not fetch schema for {obj_name}. Error: {err}")
                    f.write(f"-- Error fetching {obj_name}: {err}\n\n")

        print(f"\nSuccess! Full schema saved to: {os.path.abspath(OUTPUT_FILE)}")

    except mysql.connector.Error as err:
        print(f"Critical Error: {err}")
    
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    dump_schema()