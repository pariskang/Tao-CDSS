"""语音层: faster-whisper ASR(GPU 加速)+ edge-tts 语音合成。

需 demo 可选依赖组;全部懒加载,导入本模块不触发模型下载。
ASR 置信度: whisper 段级 avg_logprob 经 exp 映射到 (0,1],
喂给引擎的 asr_confidence 门控(<0.65 复述,0.65-0.85 不排除危重鉴别)。
"""
from __future__ import annotations

import asyncio
import math
import tempfile

_WHISPER = None
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def _whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel

        try:  # GPU 优先(A100/A6000: float16),失败回退 CPU int8
            _WHISPER = WhisperModel("large-v3", device="cuda",
                                    compute_type="float16")
        except Exception:
            _WHISPER = WhisperModel("small", device="cpu",
                                    compute_type="int8")
    return _WHISPER


def transcribe(audio_path: str) -> tuple[str, float]:
    """返回 (转写文本, 置信度∈(0,1])。无语音内容返回 ("", 0.0)。"""
    if not audio_path:
        return "", 0.0
    segments, _info = _whisper().transcribe(
        audio_path, language="zh", vad_filter=True, beam_size=5,
    )
    texts, probs = [], []
    for seg in segments:
        texts.append(seg.text.strip())
        probs.append(math.exp(min(0.0, seg.avg_logprob)))
    if not texts:
        return "", 0.0
    return "".join(texts), round(sum(probs) / len(probs), 3)


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> str | None:
    """文本 → mp3 文件路径(edge-tts);空文本或失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        import edge_tts

        out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        out.close()

        async def _run() -> None:
            await edge_tts.Communicate(text, voice).save(out.name)

        asyncio.run(_run())
        return out.name
    except Exception:
        return None  # TTS 是增强通道,失败不阻断文本回复
