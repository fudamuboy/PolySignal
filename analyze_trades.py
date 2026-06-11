import sqlite3
import pandas as pd
from datetime import datetime
import json

conn = sqlite3.connect('storage/database.db')

print("=== DB SCHEMA ===")
for row in conn.execute("SELECT sql FROM sqlite_master WHERE type='table'"):
    print(row[0])

try:
    print("\n=== RECENT TRADES ===")
    query = "SELECT * FROM trades ORDER BY entry_time DESC LIMIT 50"
    trades_df = pd.read_sql_query(query, conn)
    print(f"Loaded {len(trades_df)} trades.")
    if len(trades_df) > 0:
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        print(trades_df.head(20).to_string())
        
        # Stats on yesterday and today
        # Just grab the last 24h
        if 'pnl_usd' in trades_df.columns:
            completed = trades_df[trades_df['status'] == 'CLOSED']
            print("\n=== CLOSED TRADES STATS ===")
            print(f"Count: {len(completed)}")
            if len(completed) > 0:
                print(f"Total PNL: {completed['pnl_usd'].sum():.4f}")
                print(f"Win Rate: {(completed['pnl_usd'] > 0).mean()*100:.2f}%")
                
except Exception as e:
    print("Error analyzing trades:", e)

conn.close()
