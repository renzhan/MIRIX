# 日志配置说明

## 环境变量配置

### LOG_OUTPUT
控制日志输出位置：
- `console` - 仅输出到控制台
- `file` - 仅输出到文件
- `both` - 同时输出到控制台和文件（默认）

### LOG_LEVEL
设置日志级别：
- `DEBUG` - 调试级别（最详细）
- `INFO` - 信息级别（默认）
- `WARNING` - 警告级别
- `ERROR` - 错误级别
- `CRITICAL` - 严重错误级别

### LOG_DIR
设置日志文件目录：
- 默认：`/app/logs`
- 示例：`/var/log/mirix`

## 使用示例

### 1. 仅控制台输出（开发环境）
```bash
export LOG_OUTPUT=console
export LOG_LEVEL=DEBUG
```

### 2. 仅文件输出（生产环境）
```bash
export LOG_OUTPUT=file
export LOG_LEVEL=INFO
export LOG_DIR=/var/log/mirix
```

### 3. 同时输出（默认配置）
```bash
export LOG_OUTPUT=both
export LOG_LEVEL=INFO
export LOG_DIR=/app/logs
```

### 4. Docker环境配置
```dockerfile
ENV LOG_OUTPUT=file
ENV LOG_LEVEL=INFO
ENV LOG_DIR=/app/logs
```

### 5. Docker Compose配置
```yaml
services:
  mirix:
    environment:
      - LOG_OUTPUT=both
      - LOG_LEVEL=INFO
      - LOG_DIR=/app/logs
    volumes:
      - ./logs:/app/logs
```

### 6. Kubernetes配置
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: mirix-config
data:
  LOG_OUTPUT: "file"
  LOG_LEVEL: "INFO"
  LOG_DIR: "/app/logs"
```

## 日志文件分割

当 `LOG_OUTPUT` 为 `file` 或 `both` 时：
- 文件名：`api_backend.log`
- 最大大小：50MB
- 备份数量：10个
- 总存储：约550MB

## 日志格式

### 控制台输出格式（简化）
```
2025-10-24 12:00:00,123 - INFO - 邮件回复任务已排队: abc123
```

### 文件输出格式（详细）
```
2025-10-24 12:00:00,123 - INFO - mirix.server.fastapi_server - fastapi_server.py:123 - 邮件回复任务已排队: abc123
```

## 常见使用场景

### 开发环境
```bash
LOG_OUTPUT=console
LOG_LEVEL=DEBUG
```
- 快速查看日志
- 详细调试信息
- 不占用磁盘空间

### 测试环境
```bash
LOG_OUTPUT=both
LOG_LEVEL=INFO
```
- 实时查看 + 文件记录
- 便于调试和分析

### 生产环境
```bash
LOG_OUTPUT=file
LOG_LEVEL=INFO
LOG_DIR=/var/log/mirix
```
- 持久化日志记录
- 便于日志分析和监控
- 不影响控制台输出

### 容器化部署
```bash
LOG_OUTPUT=console
LOG_LEVEL=INFO
```
- 配合容器日志收集系统
- 如 ELK Stack、Fluentd 等