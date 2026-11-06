import re
import uuid
from pathlib import Path

import httpx


class SpeechService:
    """Explicitly configured compatible audio service, separate from DeepSeek chat."""
    def __init__(self, settings, repo):
        self.settings, self.repo = settings, repo
        self.directory = Path(settings.database).parent / "audio"
        self.directory.mkdir(parents=True, exist_ok=True)

    def headers(self):
        return {"Authorization": f"Bearer {self.settings.speech_api_key}"} if self.settings.speech_api_key else {}

    async def transcribe(self, data, filename, content_type):
        if not self.settings.speech_url or not self.settings.asr_model:
            raise ValueError("ASR 尚未配置，请设置 SPEECH_BASE_URL 和 ASR_MODEL；也可直接输入文字")
        if not data or len(data) > 20_000_000:
            raise ValueError("音频必须非空且不超过 20 MB")
        if content_type.split(";")[0] not in {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/webm", "video/webm", "audio/mp4", "audio/ogg"}:
            raise ValueError("不支持的音频类型")
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(self.settings.speech_url + "/audio/transcriptions", headers=self.headers(),
                files={"file": (Path(filename).name, data, content_type)}, data={"model": self.settings.asr_model, "response_format": "json"})
            response.raise_for_status()
            text = response.json().get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("ASR 返回文本无效")
        return self.repo.create("transcript", {"text": text, "filename": Path(filename).name,
            "status": "needs_review", "critical_fields": re.findall(r"P\d{4}|\d+(?:\.\d+)?", text),
            "note": "请核对项目编号和金额，再将修正后的文字发送到问答或任务。"})

    async def speak(self, body):
        if not self.settings.speech_url or not self.settings.tts_model:
            raise ValueError("TTS 尚未配置，请设置 SPEECH_BASE_URL 和 TTS_MODEL；文本回答仍可使用")
        message = self.repo.message(body.message_id)
        if message["role"] != "assistant" or message["status"] != "complete":
            raise ValueError("仅可朗读已完成的助手回答")
        text = re.sub(r"\[\d+\]", "", message["content"])
        if len(text) > 4000:
            raise ValueError("当前朗读上限为 4000 字符，请缩短回答")
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(self.settings.speech_url + "/audio/speech", headers=self.headers(),
                json={"model": self.settings.tts_model, "input": text, "voice": body.voice, "response_format": "wav"})
            response.raise_for_status()
        if not response.content or len(response.content) > 20_000_000 or not response.content.startswith(b"RIFF"):
            raise ValueError("TTS 未返回有效 WAV 音频")
        rid = uuid.uuid4().hex
        path = self.directory / f"{rid}.wav"
        path.write_bytes(response.content)
        try:
            return self.repo.create("audio", {"message_id": message["id"], "text": text, "voice": body.voice,
                "path": str(path.resolve()), "url": f"/v1/audio/{rid}", "mime": "audio/wav"}, rid)
        except Exception:
            path.unlink(missing_ok=True)
            raise
