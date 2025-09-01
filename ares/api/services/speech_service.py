# services/speech_service.py
from __future__ import annotations
import os, time
from typing import Optional
from ares.api.utils.common_utils import ensure_dir as _ensure_dir

try:
    import azure.cognitiveservices.speech as speechsdk
except Exception:
    speechsdk = None

SPEECH_KEY    = os.getenv("SPEECH_KEY", "").strip()
SPEECH_REGION = os.getenv("SPEECH_REGION", "").strip()

def _speech_config():
    if not speechsdk or not (SPEECH_KEY and SPEECH_REGION):
        return None
    cfg = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    cfg.speech_recognition_language = "ko-KR"
    return cfg

def stt_from_file(path: str) -> str:
    if (not speechsdk) or (not path) or (not os.path.exists(path)):
        return ""
    cfg = _speech_config()
    if not cfg:
        return ""
    rec = speechsdk.SpeechRecognizer(speech_config=cfg, audio_config=speechsdk.AudioConfig(filename=path))
    res = rec.recognize_once()
    return res.text if res.reason == speechsdk.ResultReason.RecognizedSpeech else ""

def tts_play(text: str, voice: str = "ko-KR-HyunsuNeural") -> Optional[str]:
    if not speechsdk or not text:
        return None
    cfg = _speech_config()
    if not cfg:
        return None
    out_dir = os.path.join(os.getcwd(), "tts")
    _ensure_dir(out_dir)
    out_path = os.path.join(out_dir, f"tts_{int(time.time()*1000)}.wav")
    synth = speechsdk.SpeechSynthesizer(
        speech_config=cfg,
        audio_config=speechsdk.audio.AudioOutputConfig(filename=out_path),
    )
    res = synth.speak_text_async(text).get()
    return out_path if res.reason == speechsdk.ResultReason.SynthesizingAudioCompleted else None

if __name__ == "__main__":
    import argparse, sys, os
    from ares.api.services.speech_service import stt_from_file, tts_play

    p = argparse.ArgumentParser(description="Speech service test")
    p.add_argument("--mode", choices=["stt","tts"], default="tts")
    p.add_argument("--text", default="안녕하세요. 테스트 음성입니다.", help="mode=tts")
    p.add_argument("--audio", default="", help="mode=stt 파일 경로")
    args = p.parse_args()

    if args.mode == "stt":
        if not args.audio or not os.path.exists(args.audio):
            print("STT용 오디오 파일 경로가 필요합니다.")
            sys.exit(1)
        print(stt_from_file(args.audio))
    else:
        path = tts_play(args.text)
        print("생성 파일:", path if path else "생성 실패")
