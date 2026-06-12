# OpenClaw 接入

OpenClaw 支持 MCP 工具接入,两种方式:

## 方式一: MCP(推荐)
在 OpenClaw 的 MCP 配置中注册 stdio server:

```json
{
  "mcpServers": {
    "shanghan-hermes": {
      "command": "bash",
      "args": ["/绝对路径/Tao-CDSS/scripts/run_shanghan_mcp.sh"]
    }
  }
}
```

## 方式二: 命令行工具
将以下命令注册为 OpenClaw 自定义工具(子进程调用,输出 JSON):

```bash
.venv/bin/python -m shanghan.cli ask "<question>" --role doctor|researcher|patient
```

## 安全注意
- 患者角色(`--role patient`)输出经递归脱敏: 无方剂推荐、无组成/剂量/煎服法;
- 所有输出仅供参考,临床规则标注 PENDING_PHYSICIAN_REVIEW。
