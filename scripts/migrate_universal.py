import sys
import os
from sqlalchemy import text, inspect

# Add parent directory to path to allow importing app.db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine

def migrate():
    print(f"Connecting to database using engine: {engine.url}")
    with engine.connect() as conn:
        inspector = inspect(engine)
        try:
            columns = [c['name'] for c in inspector.get_columns('projects')]
        except Exception as e:
            print(f"Error inspecting table: {e}")
            return

        if 'type' in columns:
             print("Column 'type' already exists in 'projects' table.")
             return

        print("Column 'type' missing. Adding it now...")
        try:
            # Universal SQL for adding a column with default
            # Note: SQLite supports ADD COLUMN. MySQL supports ADD COLUMN.
            conn.execute(text("ALTER TABLE projects ADD COLUMN type VARCHAR(32) NOT NULL DEFAULT 'report'"))
            conn.commit()
            print("Migration successful: Added 'type' column.")
        except Exception as e:
            print(f"Migration failed: {e}")
            conn.rollback()

if __name__ == "__main__":
    migrate()
