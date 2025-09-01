# services/speech_service.py
from __future__ import annotations
import os, time
from typing import Optional, TYPE_CHECKING
from ares.api.utils.common_utils import ensure_dir, get_logger

_log = get_logger("speech")

# 타입 전용 임포트 (실행시 로드되지 않음)
if TYPE_CHECKING:
    import azure.cognitiveservices.speech as speechsdk_t
    from azure.cognitiveservices.speech import SpeechConfig  # <- 타입 심볼만 사용

try:
    import azure.cognitiveservices.speech as speechsdk
except Exception:
    speechsdk = None  # type: ignore[assignment]
    _log.warning("azure-cognitiveservices-speech 미설치 또는 로딩 실패")

SPEECH_KEY    = os.getenv("SPEECH_KEY", "").strip()
SPEECH_REGION = os.getenv("SPEECH_REGION", "").strip()

def _speech_config() -> Optional["SpeechConfig"]:
    if not speechsdk or not (SPEECH_KEY and SPEECH_REGION):
        return None
    cfg = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
    )
    return cfg

def stt_from_file(wav_path: str, locale: str = "ko-KR") -> Optional[str]:
    cfg = _speech_config()
    if not cfg or not speechsdk:
        _log.error("Speech 설정 누락")
        return None
    cfg.speech_recognition_language = locale
    audio_cfg = speechsdk.audio.AudioConfig(filename=wav_path)
    rec = speechsdk.SpeechRecognizer(speech_config=cfg, audio_config=audio_cfg)
    res = rec.recognize_once_async().get()
    if res.reason == speechsdk.ResultReason.RecognizedSpeech:
        return res.text
    _log.error(f"STT 실패: {res.reason}")
    return None

def tts_play(text: str, voice: str = "ko-KR-SunHiNeural", out_dir: str = "./outputs/audio") -> Optional[str]:
    cfg = _speech_config()
    if not cfg or not speechsdk:
        _log.error("Speech 설정 누락")
        return None
    ensure_dir(out_dir)
    cfg.speech_synthesis_voice_name = voice
    out_path = os.path.join(out_dir, f"tts_{int(time.time()*1000)}.wav")
    synth = speechsdk.SpeechSynthesizer(
        speech_config=cfg,
        audio_config=speechsdk.audio.AudioOutputConfig(filename=out_path),
    )
    res = synth.speak_text_async(text).get()
    if res.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return out_path
    _log.error(f"TTS 실패: {res.reason}")
    return None
