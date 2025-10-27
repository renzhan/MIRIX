#!/bin/bash
##############################################################################
# 修复 PostgreSQL 字段查询语法，兼容所有版本（无需 GROUP BY）
# 100% 纯 Bash，无外部工具，免密导出指定用户数据
# 
# 修复内容：
# 1. PostgreSQL 关键字字段（如 limit）用双引号包围
# 2. VALUES 子句使用逗号分隔，而非管道符
##############################################################################

# -------------------------- 基础配置（无需修改）--------------------------
DB_HOST="127.0.0.1"
DB_PORT="5432"
DB_USER="postgres"
DB_PASS="aiop123456"
DB_NAME="mirix_pams"
TARGET_USER_ID="user-43a92772-e76b-4e5d-a1bd-3d32992580f9"
TABLES=("block" "episodic_memory" "knowledge_vault" "procedural_memory" "resource_memory" "semantic_memory")
OUTPUT_DIR="./user_data_export"
# -----------------------------------------------------------------------------------


# 1. 检查 psql
if ! command -v psql &> /dev/null; then
    echo "❌ 请先安装：sudo apt install postgresql-client"
    exit 1
fi

# 2. 免密配置
export PGPASSWORD="${DB_PASS}"

# 3. 创建目录
if [ ! -d "${OUTPUT_DIR}" ]; then
    mkdir -p "${OUTPUT_DIR}" || { echo "❌ 目录创建失败（权限不足）"; exit 1; }
fi
echo "✅ 导出目录：${OUTPUT_DIR}"


# 4. 处理每张表（核心：修复字段查询语法）
table_index=1
for table in "${TABLES[@]}"; do
    echo -e "\n======================================================"
    echo "[$table_index/6] 处理表：${table}"
    echo "======================================================"
    table_index=$((table_index + 1))

    # 4.1 修复：用子查询排序后再聚合，避免 GROUP BY 语法问题
    # 逻辑：先在子查询中按 ordinal_position 排序，再用 array_agg 拼接字段
    echo "🔍 获取 ${table} 表字段..."
    columns=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
        -t -A -c "
            SELECT array_to_string(
                array_agg(column_name),  -- 聚合字段为数组
                ','  -- 数组元素用逗号分隔
            )
            FROM (
                -- 子查询：按字段在表中的实际顺序排序（ordinal_position 是系统字段）
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = '${table}' 
                  AND table_schema = 'public' 
                ORDER BY ordinal_position
            ) AS sub_query;
        ")

    # 检查字段是否获取成功
    if [ -z "${columns}" ] || [ "${columns}" = "NULL" ]; then
        echo "❌ 未获取到字段（表不存在？或权限不足？）"
        continue
    fi
    echo "✅ 字段列表：${columns}"

    # 4.2 字段转为数组（纯 Bash 分割）
    IFS=',' read -r -a column_array <<< "${columns}"

    # 4.3 构建 INSERT 语句的字段和值处理部分
    insert_columns=""
    insert_values=""
    for col in "${column_array[@]}"; do
        # 处理关键字字段（如 limit，加双引号）
        if [[ "${col}" =~ ^(limit|order|where|select|from|group|having|union|join|inner|outer|left|right|full|cross|natural|on|using|case|when|then|else|end|distinct|all|any|some|exists|in|not|and|or|between|like|ilike|similar|is|null|true|false|unknown|cast|extract|interval|current_date|current_time|current_timestamp)$ ]]; then
            col_safe="\"${col}\""
        else
            col_safe="${col}"
        fi
        
        # 构建字段列表（用于INSERT语句）
        if [ -z "${insert_columns}" ]; then
            insert_columns="${col_safe}"
        else
            insert_columns="${insert_columns}, ${col_safe}"
        fi
        
        # 拼接值处理逻辑（COALESCE 处理 NULL 和转义）
        if [ -z "${insert_values}" ]; then
            insert_values="COALESCE(quote_literal(${col_safe}), 'NULL')"
        else
            insert_values="${insert_values} || ',' || COALESCE(quote_literal(${col_safe}), 'NULL')"
        fi
    done

    # 4.4 导出数据到文件
    data_file="${OUTPUT_DIR}/${table}_data.sql"
    echo "📤 导出数据中..."
    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
        -t -A -c "
            SELECT 'INSERT INTO public.${table} (${insert_columns}) VALUES (' || 
                   ${insert_values} || ');' 
            FROM public.${table} 
            WHERE user_id = '${TARGET_USER_ID}';
        " > "${data_file}"

    # 4.5 统计有效数据量（纯 Bash 循环）
    count=0
    while IFS= read -r line; do
        [[ "${line}" == INSERT* ]] && count=$((count + 1))
    done < "${data_file}"

    # 输出结果
    if [ "${count}" -gt 0 ]; then
        echo "✅ 成功导出 ${count} 条数据 → ${data_file}"
    else
        rm -f "${data_file}"
        echo "ℹ️  无匹配数据（user_id=${TARGET_USER_ID} 不存在）"
    fi
done


# 5. 收尾
unset PGPASSWORD
echo -e "\n======================================================"
echo "🎉 所有表处理完成！"
echo "✅ 有效文件路径：${OUTPUT_DIR}"
echo "💡 导入：psql -h 目标IP -U 目标用户 -d 目标库 -f 数据文件.sql"
echo "======================================================"

