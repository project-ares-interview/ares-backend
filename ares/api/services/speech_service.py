# services/speech_service.py
from __future__ import annotations
import os, time
from typing import Optional, TYPE_CHECKING, List, Tuple
from ares.api.utils.common_utils import ensure_dir, get_logger

_log = get_logger("speech")

# 타입 전용 임포트(런타임 로드 안 됨)
if TYPE_CHECKING:
    import azure.cognitiveservices.speech as speechsdk_t
    from azure.cognitiveservices.speech import SpeechConfig, AudioDataStream, AudioStreamFormat

# 런타임 의존성
try:
    import azure.cognitiveservices.speech as speechsdk
except Exception:
    speechsdk = None  # type: ignore[assignment]
    _log.warning("azure-cognitiveservices-speech 미설치 또는 로딩 실패")

# ===== ENV =====
SPEECH_KEY    = os.getenv("SPEECH_KEY", "").strip()
SPEECH_REGION = os.getenv("SPEECH_REGION", "").strip()
DEFAULT_LOCALE = os.getenv("SPEECH_DEFAULT_LOCALE", "ko-KR").strip()
DEFAULT_VOICE  = os.getenv("SPEECH_DEFAULT_VOICE", "ko-KR-SunHiNeural").strip()
AUDIO_OUT_DIR  = os.getenv("SPEECH_OUT_DIR", "./outputs/audio").strip()

def _ensure_sdk_and_env() -> bool:
    if not speechsdk:
        _log.error("Azure Speech SDK 로드 실패")
        return False
    if not (SPEECH_KEY and SPEECH_REGION):
        _log.error("Speech 환경변수 누락(SPEECH_KEY, SPEECH_REGION)")
        return False
    return True

def _speech_config() -> Optional["SpeechConfig"]:
    if not _ensure_sdk_and_env():
        return None
    cfg = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    # 기본 합성 포맷(16kHz PCM WAV)
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
    )
    return cfg

# ===== STT =====

class SpeechToTextFromStream:
    """
    오디오 스트림으로부터 실시간 STT를 수행하는 클래스.
    세션 기반으로 동작하며, 콜백을 통해 결과를 비동기적으로 전달합니다.
    """
    def __init__(self, recognized_callback, locale: str = DEFAULT_LOCALE, stream_format: "AudioStreamFormat" = None):
        if not _ensure_sdk_and_env() or not speechsdk:
            raise RuntimeError("Azure Speech SDK가 설정되지 않았습니다.")

        self.locale = locale
        self.recognized_callback = recognized_callback
        
        # 스트림 포맷이 제공되면 해당 포맷을 사용하고, 아니면 기본 WAV 헤더를 기대하는 포맷을 사용
        if stream_format:
            self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
        else:
            self.push_stream = speechsdk.audio.PushAudioInputStream()
        
        self.speech_config = _speech_config()
        if not self.speech_config:
            raise RuntimeError("Speech 구성을 초기화할 수 없습니다.")
        
        self.speech_config.speech_recognition_language = self.locale
        
        self.audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)
        self.speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=self.speech_config, 
            audio_config=self.audio_config
        )

        # 이벤트 핸들러 연결
        self.speech_recognizer.recognized.connect(self.recognized_handler)
        self.speech_recognizer.session_stopped.connect(self.session_stopped_handler)
        self.speech_recognizer.canceled.connect(self.canceled_handler)

        # 연속 인식 시작
        self.speech_recognizer.start_continuous_recognition()
        _log.info(f"실시간 STT 세션 시작 (언어: {self.locale})")

    def recognized_handler(self, event: speechsdk.SpeechRecognitionEventArgs):
        """인식 완료 이벤트 핸들러"""
        if event.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            duration_sec = event.result.duration / 10_000_000  # 100ns ticks to seconds
            self.recognized_callback(event.result.text, duration_sec, "recognized")

    def session_stopped_handler(self, event: speechsdk.SessionEventArgs):
        """세션 중지 이벤트 핸들러"""
        _log.info(f"STT 세션 중지됨: {event}")
        self.stop()

    def canceled_handler(self, event: speechsdk.SpeechRecognitionCanceledEventArgs):
        """취소 이벤트 핸들러"""
        _log.warning(f"STT 취소됨: {event.reason}, {event.error_details}")
        self.stop()

    def write_chunk(self, chunk: bytes):
        """오디오 청크를 스트림에 씁니다."""
        self.push_stream.write(chunk)

    def stop(self):
        """인식을 중지하고 리소스를 정리합니다."""
        try:
            self.speech_recognizer.stop_continuous_recognition()
            self.push_stream.close()
            _log.info("실시간 STT 세션 정리 완료")
        except Exception as e:
            _log.error(f"STT 세션 중지 중 오류 발생: {e}")


def stt_from_file(
    wav_path: str,
    locale: str = DEFAULT_LOCALE,
    *,
    enable_auto_language: bool = False,
    initial_silence_timeout_ms: int = 5000,
) -> Optional[str]:
    """
    단발 STT(짧은 파일 권장: 수십초~1분 내).
    - enable_auto_language=True면 기본 ko-KR에 en-US 등 자동감지 혼합 사용(권장: ko/e n 2~3개).
    """
    cfg = _speech_config()
    if not cfg or not speechsdk:
        _log.error("Speech 설정 누락")
        return None

    # 언어 설정
    if enable_auto_language:
        # 자동 언어감지(ko-KR/en-US 샘플) — 필요 시 확장
        langs = [locale, "en-US"]
        auto_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(langs)
    else:
        cfg.speech_recognition_language = locale
        auto_config = None

    # 인식기 생성
    audio_cfg = speechsdk.audio.AudioConfig(filename=wav_path)
    rec = speechsdk.SpeechRecognizer(
        speech_config=cfg, audio_config=audio_cfg,
        auto_detect_source_language_config=auto_config
    )
    # 잡음/무음 설정(옵션)
    try:
        rec.properties.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs,
            str(initial_silence_timeout_ms)
        )
    except Exception:
        pass

    # 1회 인식 + 가벼운 재시도
    for attempt in range(2):
        res = rec.recognize_once_async().get()
        if res.reason == speechsdk.ResultReason.RecognizedSpeech and res.text:
            return res.text
        _log.warning(f"STT 실패({attempt+1}/2): {getattr(res, 'reason', 'Unknown')}")
        time.sleep(0.5)
    return None

# ===== TTS =====
def _pick_output_filename(out_dir: str, ext: str = "wav") -> str:
    ts = int(time.time() * 1000)
    return os.path.join(out_dir, f"tts_{ts}.{ext}")

def list_voices(locale_prefix: str = "ko-") -> List[str]:
    """사용 가능한 음성명 나열(간단 버전, 네트워크 호출 없이 SDK 내장 목록 기반)."""
    # SDK에서 음성 나열 API가 지역/권한에 따라 다름. 간단히 알려진 일부를 반환.
    known = [
        "ko-KR-SunHiNeural", "ko-KR-BongJinNeural", "ko-KR-HyunsuNeural",
        "en-US-JennyNeural", "en-US-GuyNeural"
    ]
    return [v for v in known if v.startswith(locale_prefix)]

def tts_play(
    text: str,
    voice: str = DEFAULT_VOICE,
    out_dir: str = AUDIO_OUT_DIR,
    *,
    format_ext: str = "wav"  # "wav" | "mp3"(지원 지역/계정에 따라 다름)
) -> Optional[str]:
    """
    텍스트 → 오디오 파일 저장 후 경로 반환.
    기본은 WAV(PCM 16kHz). MP3가 필요하면 format_ext="mp3"로 시도.
    """
    cfg = _speech_config()
    if not cfg or not speechsdk:
        _log.error("Speech 설정 누락")
        return None

    ensure_dir(out_dir)
    cfg.speech_synthesis_voice_name = voice

    # 포맷 전환(가능한 경우)
    if format_ext.lower() == "mp3":
        try:
            cfg.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
            )
        except Exception:
            _log.warning("MP3 포맷 미지원 → WAV로 대체")

    out_path = _pick_output_filename(out_dir, "mp3" if format_ext.lower()=="mp3" else "wav")
    synth = speechsdk.SpeechSynthesizer(
        speech_config=cfg,
        audio_config=speechsdk.audio.AudioOutputConfig(filename=out_path),
    )
    res = synth.speak_text_async(text).get()
    if res.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return out_path
    _log.error(f"TTS 실패: {getattr(res, 'reason', 'Unknown')}")
    return None

# ===== 추가 유틸(선택) =====
def tts_ssml_to_file(
    ssml: str,
    out_dir: str = AUDIO_OUT_DIR,
    file_ext: str = "wav",
    voice: Optional[str] = None
) -> Optional[str]:
    """
    SSML 직접 합성(발음/속도/간격 제어). voice가 주어지면 SSML 내 voice 태그 없이 설정.
    """
    cfg = _speech_config()
    if not cfg or not speechsdk:
        _log.error("Speech 설정 누락")
        return None
    ensure_dir(out_dir)
    if voice:
        cfg.speech_synthesis_voice_name = voice
    out_path = _pick_output_filename(out_dir, file_ext)
    synth = speechsdk.SpeechSynthesizer(
        speech_config=cfg,
        audio_config=speechsdk.audio.AudioOutputConfig(filename=out_path),
    )
    res = synth.speak_ssml_async(ssml).get()
    if res.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return out_path
    _log.error(f"TTS(SSML) 실패: {getattr(res, 'reason', 'Unknown')}")
    return None
