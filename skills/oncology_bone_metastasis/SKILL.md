# oncology_bone_metastasis(肺癌骨转移随访/预问诊旗舰 Skill)

- 目的: 骨转移疼痛与并发症的结构化预问诊;脊髓压迫三联征为首要安全指标。
- 适应证: 确诊肺癌骨转移患者的随访与症状评估。
- 激活条件: 医生端排班/患者档案带 oncology_bone_metastasis 标记。
- 禁止行为: 不输出诊断、不输出剂量;NRS/ECOG 评分走 calculator server 确定性计算。
- 升级路径: 三联征(鞍区麻木/二便障碍/进行性下肢无力)任一阳性 → E1 立即急诊。
- must-not-miss 闭环: 三联征任一未排除禁止进入 SUMMARY(不变量B)。

PENDING_PHYSICIAN_REVIEW: 全部规则须经肿瘤科/骨科执业医师审定。
