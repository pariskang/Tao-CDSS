---
name: safety-review
description: 在合并任何 PR 前对当前 diff 执行医疗安全宪法核查
---
对 `git diff main` 逐条核对 CLAUDE.md 硬规则 1-6:
1. 搜索 diff 中是否出现 剂量单位正则(mg|ml|μg|片|粒|滴|单位/次|tid|bid|qd)
   与数字组合且来源不是 drug_safety 回填路径;
2. 检查 redflag-engine 是否新增 import 指向 llm/ 或网络客户端;
3. 检查是否出现对 audit_ledger 的 UPDATE/DELETE;
4. 检查 build_system_prompt 调用点是否拼接了 transcript/tool_result;
5. 检查 clinical_state 写入是否绕过 contracts validator;
6. 检查 psych_scripts 是否被 LLM 调用替代。
输出: 每条 PASS/FAIL + 证据行号;任一 FAIL 则给出回退建议,禁止自行修复
硬规则相关代码后直接提交。
