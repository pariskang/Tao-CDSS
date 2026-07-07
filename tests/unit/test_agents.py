"""HermesAgents: 复杂度路由 / 受限会诊 / LLM 兜底 / 多评审共识。"""
import pytest

from audit_chain import AuditLedger
from hermes_agents import (
    IntakeAgent,
    ManagerAgent,
    ReviewPanel,
    SafetyTriageAgent,
    classify_complexity,
)
from hermes_agents.manager import MAX_CONSULT_SPECIALISTS
from hermes_llm import LLMGateway, StubLLMBackend


class TestComplexityRouter:
    def test_simple(self):
        assert classify_complexity({"chief_complaints": ["咳嗽"]}) == "simple"

    def test_moderate(self):
        assert classify_complexity(
            {"chief_complaints": ["咳嗽"], "specialty": "respiratory"}
        ) == "moderate"

    def test_complex_multi_complaint(self):
        assert classify_complexity(
            {"chief_complaints": ["咳嗽", "腰痛"]}
        ) == "complex"

    def test_complex_tcm_requested(self):
        assert classify_complexity(
            {"chief_complaints": ["腰痛"], "tcm_requested": True}
        ) == "complex"

    def test_red_flag_is_complex(self):
        assert classify_complexity(
            {"chief_complaints": ["胸痛"], "red_flag_level": "E1"}
        ) == "complex"


class TestSafetyTriageAgent:
    def test_pure_rule_no_llm_role(self):
        """硬规则2: 急症接管 Agent 无 LLM 路径。"""
        agent = SafetyTriageAgent()
        assert agent.role == ""
        assert agent._gateway is None

    def test_red_flag_detection(self):
        out = SafetyTriageAgent().run({"texts": ["胸口正中压着痛,出冷汗"]})
        assert out.output["level"] == "E1"


class TestIntakeAgent:
    def test_llm_path(self):
        backend = StubLLMBackend(
            responses=['{"symptoms": ["恶寒", "发热"], "onset": "三天前", '
                       '"medications": []}']
        )
        gw = LLMGateway(backend, ledger=AuditLedger())
        out = IntakeAgent(gw).run({"texts": ["怕冷发烧三天"]})
        assert out.used_llm
        assert out.output["symptoms"] == ["恶寒", "发热"]

    def test_llm_failure_falls_back_to_dictionary(self):
        backend = StubLLMBackend(default="永远不是JSON")
        gw = LLMGateway(backend, ledger=AuditLedger(), max_retries=0)
        out = IntakeAgent(gw).run({"texts": ["恶寒,发热,头痛"]})
        assert not out.used_llm
        assert out.notes and out.notes[0].startswith("llm_fallback")
        assert "恶寒" in out.output["symptoms"]

    def test_deterministic_without_gateway(self):
        out = IntakeAgent().run({"texts": ["恶寒,无汗"]})
        assert not out.used_llm
        assert set(out.output["symptoms"]) >= {"恶寒", "无汗"}


class TestManagerConsult:
    @pytest.fixture()
    def manager(self):
        return ManagerAgent()

    def test_safety_always_first(self, manager):
        result = manager.consult(
            {"texts": ["叫不醒了"], "chief_complaints": ["意识障碍"]}
        )
        assert result.merged["escalation"] == "E0"
        assert result.specialist_outputs[0].agent == "safety_triage"

    def test_simple_route_intake_only(self, manager):
        result = manager.consult(
            {"texts": ["咳嗽两天"], "chief_complaints": ["咳嗽"]}
        )
        assert result.complexity == "simple"
        assert result.merged["specialists_consulted"] == ["safety_triage", "intake"]

    def test_complex_bounded_consult(self, manager):
        result = manager.consult(
            {"texts": ["腰背痛,怕冷"], "chief_complaints": ["腰痛", "怕冷"],
             "tcm_requested": True}
        )
        assert result.complexity == "complex"
        consulted = result.merged["specialists_consulted"]
        # safety + intake + ≤3 specialists,且各自独立输出
        assert len(consulted) <= 2 + MAX_CONSULT_SPECIALISTS
        assert "tcm_reasoning" in consulted

    def test_moderate_specialty_registry_routing(self, manager):
        """专科注册表: respiratory → medication_safety,不再固定取第一个。"""
        result = manager.consult(
            {"texts": ["咳嗽两天"], "chief_complaints": ["咳嗽"],
             "specialty": "respiratory"}
        )
        assert result.complexity == "moderate"
        consulted = result.merged["specialists_consulted"]
        assert "medication_safety" in consulted
        assert "tcm_reasoning" not in consulted

    def test_moderate_tcm_specialty(self, manager):
        result = manager.consult(
            {"texts": ["腰痛"], "chief_complaints": ["腰痛"], "specialty": "tcm"}
        )
        assert result.merged["specialists_consulted"][-1] == "tcm_reasoning"

    def test_medications_signal_adds_med_safety(self, manager):
        result = manager.consult(
            {"texts": ["腰痛", "怕冷"], "chief_complaints": ["腰痛", "怕冷"],
             "medications": ["warfarin", "aspirin"]}
        )
        assert result.complexity == "complex"
        consulted = result.merged["specialists_consulted"]
        assert "medication_safety" in consulted
        med = result.merged["sections"]["medication_safety"]
        assert med["interactions"]

    def test_merged_output_is_doctor_channel(self, manager):
        result = manager.consult(
            {"texts": ["咳嗽"], "chief_complaints": ["咳嗽"]}
        )
        assert result.merged["channel"] == "doctor"


class TestReviewPanel:
    def test_any_fail_is_fail(self):
        panel = ReviewPanel({
            "a": lambda d: {"verdict": "pass", "problems": []},
            "b": lambda d: {"verdict": "fail", "problems": ["bad"]},
        })
        verdict = panel.review({})
        assert verdict.verdict == "fail"
        assert verdict.votes == {"a": "pass", "b": "fail"}

    def test_warn_propagates(self):
        panel = ReviewPanel({
            "a": lambda d: {"verdict": "pass", "problems": []},
            "b": lambda d: {"verdict": "warn", "problems": []},
        })
        assert panel.review({}).verdict == "warn"

    def test_reviewer_crash_degrades_to_warn(self):
        def broken(d):
            raise RuntimeError("reviewer down")

        panel = ReviewPanel({
            "a": lambda d: {"verdict": "pass", "problems": []},
            "b": broken,
        })
        verdict = panel.review({})
        assert verdict.verdict == "warn"
        assert any("reviewer_error" in p for p in verdict.problems)

    def test_with_llm_reviewers(self):
        backend = StubLLMBackend(
            responses=['{"verdict": "pass", "problems": []}',
                       '{"verdict": "fail", "problems": ["llm质疑"]}']
        )
        gw = LLMGateway(backend, ledger=AuditLedger())
        panel = ReviewPanel.with_llm(gw)
        verdict = panel.review({"rule": "x"})
        assert verdict.verdict == "fail"
        assert "llm质疑" in verdict.problems


class TestDoctorConsultOrchestration:
    """ManagerAgent 接入医生端主链路(外部审计 P0): 采集完成→会诊→审计。"""

    def _engine(self, skill="emergency_triage", utterances=()):
        from eventstore import EventStore
        from hermes_contracts import ConsentFlags
        from hermes_loop.engine import LoopEngine
        from hermes_loop.skill_loader import load_skill

        eng = LoopEngine("orch_enc", skill=load_skill(skill),
                         store=EventStore())
        eng.begin(ConsentFlags(recording=True, retention=True))
        for u in utterances:
            eng.step(u)
        return eng

    def test_consult_merges_specialists_and_audits(self):
        from hermes_agents.orchestrator import run_doctor_consult

        eng = self._engine(utterances=["咳嗽两天", "前天", "三分",
                                       "没有过敏", "没有", "没有"])
        consult = run_doctor_consult(eng)
        assert consult["channel"] == "doctor"
        assert "safety_triage" in consult["sections"]
        assert "intake" in consult["sections"]
        assert consult["complexity"] in ("simple", "moderate", "complex")
        actions = [e["action"] for e in eng.ledger.entries("orch_enc")]
        assert "agent_consult" in actions

    def test_oncology_skill_routes_specialty(self):
        from hermes_agents.orchestrator import build_task

        eng = self._engine(skill="oncology_bone_metastasis",
                           utterances=["腰背痛两周了"])
        task = build_task(eng.state, eng.texts,
                          skill_name="oncology_bone_metastasis")
        assert task["specialty"] == "oncology"
        assert task["chief_complaints"] == ["腰背痛两周了"]
        assert "spinal_cord_compression" in task["unexcluded"]

    def test_escalation_crosscheck_matches_engine(self):
        from hermes_agents.orchestrator import run_doctor_consult

        eng = self._engine(utterances=["突然剧烈头痛,炸开一样"])
        consult = run_doctor_consult(eng)
        assert consult["escalation_crosscheck"] == "E1"
        assert consult["complexity"] == "complex"

    def test_deterministic_without_llm(self):
        from hermes_agents.orchestrator import run_doctor_consult

        eng1 = self._engine(utterances=["咳嗽两天"])
        eng2 = self._engine(utterances=["咳嗽两天"])
        c1, c2 = run_doctor_consult(eng1), run_doctor_consult(eng2)
        assert c1["sections"].keys() == c2["sections"].keys()
        assert not any(c1["used_llm"].values())
