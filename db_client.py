import os
import pyodbc
import struct
from azure.identity import DefaultAzureCredential
from contextlib import contextmanager

def get_connection_string():
    server   = os.getenv("SQL_SERVER", "sqs-capacity-d-01.database.windows.net")
    database = os.getenv("SQL_DATABASE", "sqd-capacity-d-01")

    return (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
    )

def get_token():
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net/.default")
    token_bytes = token.token.encode("UTF-16-LE")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    return token_struct

@contextmanager
def get_connection():
    attrs_before = {1256: get_token()}
    conn = pyodbc.connect(get_connection_string(), attrs_before=attrs_before)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
