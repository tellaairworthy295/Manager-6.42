from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import urllib.parse

# Database configuration
db_config = {
    "DB_HOST": "10.29.88.63",
    "DB_PORT": 3306,
    "DB_USER": "lmh",
    "DB_PASSWORD": "lmh%40123456",
    "DB_NAME": "selfdev_test"
}

def test_db_connection_sqlalchemy():
    try:
        # Decode password if needed
        password = db_config["DB_PASSWORD"].replace("%40", "@")
        
        # Create connection string
        connection_string = f"mysql+pymysql://{db_config['DB_USER']}:{urllib.parse.quote_plus(password)}@{db_config['DB_HOST']}:{db_config['DB_PORT']}/{db_config['DB_NAME']}"
        
        print(f"Connection string: mysql+pymysql://{db_config['DB_USER']}:****@{db_config['DB_HOST']}:{db_config['DB_PORT']}/{db_config['DB_NAME']}")
        print("\nAttempting to connect...")
        
        # Create engine and test connection
        engine = create_engine(connection_string, connect_args={'connect_timeout': 10})
        
        with engine.connect() as connection:
            # Test connection with a simple query
            result = connection.execute(text("SELECT 1"))
            print("✅ Connection successful!")
            
            # Get database version
            version_result = connection.execute(text("SELECT VERSION()"))
            version = version_result.fetchone()[0]
            print(f"MySQL Version: {version}")
            
            # List all tables
            tables_result = connection.execute(text("SHOW TABLES"))
            tables = tables_result.fetchall()
            print(f"\nNumber of tables: {len(tables)}")
            
            if tables:
                print("Tables:")
                for table in tables:
                    print(f"  - {table[0]}")
                    
    except Exception as e:
        print(f"❌ Connection failed: {e}")

if __name__ == "__main__":
    test_db_connection_sqlalchemy()