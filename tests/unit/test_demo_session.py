"""演示会话层(apps/colab_demo/session.py): 纯逻辑,零 UI/语音依赖。"""
from apps.colab_demo import session as S
from hermes_contracts import Phase


class TestPatientFlow:
    def test_new_session_and_benign_flow(self):
        sess = S.new_session("emergency_triage")
        r = S.patient_turn(sess, "咳嗽两天,流鼻涕")
        assert r.reply_text and not r.terminal
        assert r.state["phase"] == "HPI"
        for answer in ["前天开始的", "三分吧", "没有过敏", "没有别的", "在吃感冒冲剂"]:
            r = S.patient_turn(sess, answer)
        assert r.terminal
        assert r.state["phase"] == "DOCTOR_REVIEW"
        assert "医生审核" in r.reply_text

    def test_escalation_locks_session(self):
        sess = S.new_session("emergency_triage")
        r = S.patient_turn(sess, "突然剧烈头痛,炸开一样")
        assert r.banner.startswith("⚠️")
        assert "急诊" in r.reply_text
        r2 = S.patient_turn(sess, "我还想继续聊")
        assert r2.terminal and "人工" in r2.reply_text

    def test_empty_input_handled(self):
        sess = S.new_session("emergency_triage")
        r = S.patient_turn(sess, "   ")
        assert "再说一次" in r.reply_text

    def test_clarify_prompt_rendered(self):
        sess = S.new_session("emergency_triage")
        r = S.patient_turn(sess, "咳了三天,心口有点痛")
        assert "确认" in r.reply_text and "「心口」" in r.reply_text

    def test_state_view_fields(self):
        sess = S.new_session("oncology_bone_metastasis")
        S.patient_turn(sess, "腰背痛两周了")
        view = S.state_view(sess)
        assert set(view) >= {"phase", "question_count", "escalation",
                             "red_flag_hits", "must_not_miss", "disclaimer"}


class TestDoctorFlow:
    def _to_review(self):
        sess = S.new_session("oncology_bone_metastasis")
        S.patient_turn(sess, "腰背痛一个月")
        S.patient_turn(sess, "好像没有吧")
        for _ in range(12):
            if sess.engine.state.phase != Phase.HPI:
                break
            S.patient_turn(sess, "没有")
        assert sess.engine.state.phase == Phase.DOCTOR_REVIEW
        return sess

    def test_doctor_view_and_dose_advisory(self):
        sess = self._to_review()
        view = S.doctor_view(sess, dose_drugs=("ibuprofen",))
        assert view["channel"] == "doctor"
        assert view["dose_advisories"][0]["filled"]

    def test_doctor_decide_with_resolution(self):
        sess = self._to_review()
        assert S.doctor_decide(sess)["withheld"]
        result = S.doctor_decide(
            sess, resolutions={"spinal_cord_compression": "excluded"})
        assert not result["withheld"]
        assert result["summary"]["channel"] == "patient"


class TestShanghanAndEval:
    def test_shanghan_ask_roles(self):
        out = S.shanghan_ask("桂枝汤和麻黄汤怎么鉴别")
        assert out["handler"] == "differential"
        patient = S.shanghan_ask("恶寒发热,用什么方?", role="patient")
        assert patient["governance"]["role"] == "patient"

    def test_run_eval_kinds(self):
        assert S.run_eval("redteam")["dose_leak_count"] == 0
        assert S.run_eval("selfplay:emergency_triage")["failed"] == []
        assert S.run_eval("shanghan_stats")["released"] == 39
