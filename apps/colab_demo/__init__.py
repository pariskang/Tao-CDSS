"""Colab/本地演示端(Gradio 语音问诊 UI)。

分层约束:
  - session.py: 纯会话逻辑,只依赖核心包(pydantic/pyyaml),进 CI 测试;
  - voice.py / app.py: 依赖 demo 可选组(gradio/faster-whisper/edge-tts/
    pyngrok),仅在演示环境安装,核心包与安全路径禁止 import 本目录。
"""
