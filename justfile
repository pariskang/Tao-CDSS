set shell := ["bash", "-cu"]

test:
    .venv/bin/python -m pytest -q

test-safety:
    .venv/bin/python -m pytest tests/safety -q \
      --cov=hermes_guard --cov=redflag_engine --cov=audit_chain \
      --cov-fail-under=100

replay skill:
    .venv/bin/python -m pytest "tests/replay/test_replay.py" -q -k {{skill}} && \
    PYTHONPATH=".:packages/contracts/src:packages/redflag-engine/src:packages/audit-chain/src:packages/eventstore/src:services/hermes-guard/src:services/hermes-loop/src:services/hermes-broker/src:services/hermes-compose/src:services/hermes-voice/src" \
    .venv/bin/python -m evals.simulation.replay --skill {{skill}}

redteam:
    PYTHONPATH=".:packages/contracts/src:packages/redflag-engine/src:packages/audit-chain/src:packages/eventstore/src:services/hermes-guard/src:services/hermes-loop/src:services/hermes-broker/src:services/hermes-compose/src:services/hermes-voice/src" \
    .venv/bin/python -m evals.redteam.run

schema-export:
    PYTHONPATH="packages/contracts/src" \
    .venv/bin/python -m hermes_contracts.export_json_schema

shanghan-stats:
    PYTHONPATH=".:packages/contracts/src:packages/redflag-engine/src:packages/audit-chain/src:packages/eventstore/src:packages/shanghan/src:packages/llm-backend/src:services/hermes-guard/src:services/hermes-loop/src:services/hermes-broker/src:services/hermes-compose/src:services/hermes-voice/src:services/hermes-agents/src" \
    .venv/bin/python -m shanghan.cli stats

shanghan-ask question:
    PYTHONPATH=".:packages/contracts/src:packages/redflag-engine/src:packages/audit-chain/src:packages/eventstore/src:packages/shanghan/src:packages/llm-backend/src:services/hermes-guard/src:services/hermes-loop/src:services/hermes-broker/src:services/hermes-compose/src:services/hermes-voice/src:services/hermes-agents/src" \
    .venv/bin/python -m shanghan.cli ask "{{question}}"

mcp-server:
    bash scripts/run_shanghan_mcp.sh

setup:
    uv venv .venv && uv pip install --python .venv/bin/python \
      pydantic pyyaml litellm pytest pytest-asyncio pytest-cov
