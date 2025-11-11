# -*- coding: utf-8 -*-
"""
查询数据库表结构
"""
import os
from dotenv import load_dotenv
import pymysql
from pymysql.cursors import DictCursor

load_dotenv('.env')

def check_table_structure():
    db_config = {
        'host': os.getenv('DEV_DB_HOST'),
        'port': int(os.getenv('DEV_DB_PORT', 3306)),
        'user': os.getenv('DEV_DB_USERNAME'),
        'password': os.getenv('DEV_DB_PASSWORD'),
        'database': os.getenv('DEV_DB_DATABASE'),
        'charset': 'utf8mb4',
        'cursorclass': DictCursor
    }
    
    print("查询 email_basic 表结构...")
    
    connection = pymysql.connect(**db_config)
    
    with connection.cursor() as cursor:
        # 查询表结构
        cursor.execute("DESCRIBE email_basic")
        columns = cursor.fetchall()
        
        print("\nemail_basic 表字段：")
        print("=" * 60)
        for col in columns:
            print(f"  {col['Field']:30s} {col['Type']}")
        
        print("\n" + "=" * 60)
        print("\nemail_body 表字段：")
        print("=" * 60)
        cursor.execute("DESCRIBE email_body")
        columns = cursor.fetchall()
        for col in columns:
            print(f"  {col['Field']:30s} {col['Type']}")
    
    connection.close()

if __name__ == "__main__":
    check_table_structure()

