import sys

def test_connection():
    host = "115.120.248.123"
    user = "root"
    password = "rootpassword"
    database = "niao_test"
    
    print(f"Attempting to connect to {host}...")
    
    # Try pymysql
    try:
        import pymysql
        print("Using pymysql...")
        conn = pymysql.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            connect_timeout=10
        )
        print("Success! Connected via pymysql.")
        with conn.cursor() as cursor:
            cursor.execute("SELECT DATABASE(), VERSION()")
            result = cursor.fetchone()
            print(f"Current Database: {result[0]}, Version: {result[1]}")
            
            # Show tables
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            print(f"Tables found ({len(tables)}):")
            for t in tables[:5]:
                print(f" - {t[0]}")
            if len(tables) > 5:
                print(" ...")
                
        conn.close()
        return True
    except ImportError:
        print("pymysql not installed.")
    except Exception as e:
        print(f"pymysql connection failed: {e}")

    # Try mysql-connector
    try:
        import mysql.connector
        print("Using mysql-connector-python...")
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            connection_timeout=10
        )
        print("Success! Connected via mysql-connector.")
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE(), VERSION()")
        result = cursor.fetchone()
        print(f"Current Database: {result[0]}, Version: {result[1]}")
        conn.close()
        return True
    except ImportError:
        print("mysql-connector-python not installed.")
    except Exception as e:
        print(f"mysql-connector connection failed: {e}")

    return False

if __name__ == "__main__":
    if test_connection():
        print("\nTest PASSED.")
    else:
        print("\nTest FAILED. No suitable driver found or connection refused.")
