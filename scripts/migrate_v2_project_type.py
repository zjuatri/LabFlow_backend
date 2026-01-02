import sqlite3
import os

# Database path (relative to the script location)
DB_PATH = os.path.join(os.path.dirname(__file__), "../labflow.db")

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        print("Checking if 'type' column exists in 'projects' table...")
        cursor.execute("PRAGMA table_info(projects)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if "type" in columns:
            print("'type' column already exists.")
        else:
            print("Adding 'type' column to 'projects' table...")
            # Add the column with a default value of 'report'
            cursor.execute("ALTER TABLE projects ADD COLUMN type TEXT NOT NULL DEFAULT 'report'")
            conn.commit()
            print("Migration successful.")
            
    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
