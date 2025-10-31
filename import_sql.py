#!/usr/bin/env python3
"""
SQL 文件导入工具
用法: python import_sql.py user_data_export/procedural_memory_data.sql
"""
import sys
import os

os.environ["MIRIX_PG_URI"] = "postgresql+pg8000://postgres:aiop123456@192.168.30.16:5432/mirix_pams"

from sqlalchemy import create_engine, text

def import_sql_file(sql_file_path):
    """导入 SQL 文件到数据库"""
    
    # 创建数据库连接
    engine = create_engine("postgresql+pg8000://postgres:aiop123456@192.168.30.16:5432/mirix_pams")
    
    print(f"=" * 80)
    print(f"开始导入 SQL 文件: {sql_file_path}")
    print(f"=" * 80)
    
    # 读取 SQL 文件（逐行读取，处理完整的INSERT语句）
    try:
        with open(sql_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"❌ 文件不存在: {sql_file_path}")
        return False
    
    # 合并成完整的SQL语句（每个INSERT是一行）
    sql_statements = []
    current_statement = ""
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        current_statement += line + " "
        
        # 检查是否是完整的INSERT语句（以分号结尾）
        if line.endswith(';'):
            sql_statements.append(current_statement.strip())
            current_statement = ""
    
    # 如果还有未完成的语句
    if current_statement.strip():
        sql_statements.append(current_statement.strip())
    
    print(f"\n共有 {len(sql_statements)} 条 SQL 语句")
    
    # 执行 SQL 语句
    with engine.connect() as conn:
        success_count = 0
        error_count = 0
        errors_detail = []
        
        for i, statement in enumerate(sql_statements, 1):
            try:
                # 开启新事务
                trans = conn.begin()
                conn.execute(text(statement))
                trans.commit()
                success_count += 1
                
                if i % 10 == 0:  # 每10条打印一次进度
                    print(f"进度: {i}/{len(sql_statements)} ({success_count} 成功, {error_count} 失败)")
                    
            except Exception as e:
                # 回滚失败的事务
                try:
                    trans.rollback()
                except:
                    pass
                
                error_count += 1
                error_msg = str(e)
                
                # 保存错误详情
                errors_detail.append({
                    'index': i,
                    'error': error_msg,
                    'statement_preview': statement[:200] + '...' if len(statement) > 200 else statement
                })
                
                print(f"\n❌ 第 {i} 条语句执行失败:")
                print(f"   错误类型: {type(e).__name__}")
                print(f"   错误信息: {error_msg[:200]}")
                print(f"   SQL 预览: {statement[:150]}...")
                print()
                
                # 继续执行下一条
                continue
        
        print(f"\n" + "=" * 80)
        print(f"✅ 导入完成!")
        print(f"   成功: {success_count} 条")
        print(f"   失败: {error_count} 条")
        print(f"=" * 80)
        
        # 如果有错误，输出错误汇总
        if errors_detail:
            print(f"\n⚠️  错误汇总 (显示前 10 个):")
            print("=" * 80)
            
            # 统计错误类型
            error_types = {}
            for err in errors_detail:
                # 提取主要错误信息
                error_key = err['error'].split('\n')[0][:100]
                error_types[error_key] = error_types.get(error_key, 0) + 1
            
            print("\n错误类型统计:")
            for error_msg, count in sorted(error_types.items(), key=lambda x: -x[1])[:5]:
                print(f"  • [{count}次] {error_msg}")
            
            print("\n详细错误 (前10个):")
            for err in errors_detail[:10]:
                print(f"\n  第 {err['index']} 条:")
                print(f"  错误: {err['error'][:200]}")
                print(f"  SQL: {err['statement_preview'][:150]}...")
                print("-" * 80)
        
        return error_count == 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python import_sql.py <sql_file_path>")
        print("示例: python import_sql.py user_data_export/procedural_memory_data.sql")
        sys.exit(1)
    
    sql_file = sys.argv[1]
    success = import_sql_file(sql_file)
    sys.exit(0 if success else 1)

