# Codex 接入

1. 项目宪法: 仓库根 `AGENTS.md`(Codex 自动读取)。
2. MCP server 注册(`~/.codex/config.toml`):

```toml
[mcp_servers.shanghan-hermes]
command = "bash"
args = ["/绝对路径/Tao-CDSS/scripts/run_shanghan_mcp.sh"]
```

3. CLI 直调(无 MCP 时的备选):

```bash
cd Tao-CDSS
PYTHONPATH="$(bash -c 'source /dev/stdin <<< "$(grep PYTHONPATH scripts/run_shanghan_mcp.sh)"; echo $PYTHONPATH')" \
  .venv/bin/python -m shanghan.cli ask "桂枝汤和麻黄汤怎么鉴别?"
```
