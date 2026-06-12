"""导出 JSON Schema 到 schemas/(CI 做契约漂移检测)。"""
from __future__ import annotations

import json
from pathlib import Path

from hermes_contracts.clinical_state import (
    CDSSClaim,
    ClinicalState,
    ConsentFlags,
    Differential,
    Symptom,
    UtteranceEvent,
)
from hermes_contracts.paths import repo_root
from hermes_contracts.tool_call import ToolCallEnvelope

MODELS = {
    "clinical_state": ClinicalState,
    "differential": Differential,
    "symptom": Symptom,
    "utterance": UtteranceEvent,
    "tool_call": ToolCallEnvelope,
    "consent": ConsentFlags,
    "cdss_output": CDSSClaim,
}


def export(out_dir: Path | None = None) -> list[Path]:
    out = out_dir or (repo_root() / "schemas")
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in MODELS.items():
        path = out / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2,
                       sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def main() -> None:  # pragma: no cover
    for p in export():
        print(p)


if __name__ == "__main__":  # pragma: no cover
    main()
