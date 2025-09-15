# ares/api/consumers.py
import json
import base64
import time
import threading
import asyncio
import uuid
import wave
import os

import numpy as np
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from ares.api.services.interview_metrics import InterviewMetrics
from ares.api.services.interview_analysis_service import get_detailed_analysis_data, process_frame
from ares.api.services.speech_service import SpeechToTextFromStream
from ares.api.services.voice_analysis_service import analyze_voice_from_buffer

# ==============================================================================
# Session Management
# ==============================================================================

client_sessions = {}
session_lock = threading.Lock()

# ==============================================================================
# Analysis Worker Thread
# ==============================================================================

def analysis_worker(sid, consumer_instance, audio_buffer, full_transcript):
    """Runs the heavy analysis (voice and video) in a background thread."""
    print(f"[{sid}] 백그라운드 분석 시작...")
    loop = consumer_instance.loop

    try:
        # 1. Voice Analysis
        # Convert byte array to numpy array for analysis
        if len(audio_buffer) % 2 != 0:
            print(f"[{sid}] 오디오 버퍼의 길이가 홀수입니다: {len(audio_buffer)}. 마지막 바이트를 제거합니다.")
            audio_buffer = audio_buffer[:-1]
        
        audio_data = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = 16000  # As defined in the consumer
        
        voice_scores = analyze_voice_from_buffer(audio_data, sample_rate, full_transcript)
        if voice_scores:
            asyncio.run_coroutine_threadsafe(
                consumer_instance.send_json({"event": "voice_scores_update", "data": voice_scores}),
                loop
            )
        else:
            asyncio.run_coroutine_threadsafe(
                consumer_instance.send_json({"event": "error", "data": {"message": "음성 점수 분석에 실패했습니다."}}),
                loop
            )

        # 2. Video Analysis
        with session_lock:
            session = client_sessions.get(sid)
            if session:
                video_analysis = get_detailed_analysis_data(session["metrics"])
                asyncio.run_coroutine_threadsafe(
                    consumer_instance.send_json({"event": "video_analysis_update", "data": video_analysis}),
                    loop
                )

    except Exception as e:
        print(f"[{sid}] 분석 워커 오류: {e}")
        asyncio.run_coroutine_threadsafe(
            consumer_instance.send_json({"event": "error", "data": {"message": f"분석 중 오류 발생: {e}"}}),
            loop
        )
    finally:
        print(f"[{sid}] 백그라운드 분석 종료.")

# ==============================================================================
# Interview Consumer
# ==============================================================================

class InterviewConsumer(AsyncJsonWebsocketConsumer):
    """Handles WebSocket connections for the AI Interview Coach."""

    async def connect(self):
        self.sid = self.channel_name
        self.loop = asyncio.get_running_loop()
        await self.accept()

        def stt_callback(text, duration_sec, event_type):
            with session_lock:
                session = client_sessions.get(self.sid)
                if session and session["metrics"].analyzing:
                    session["full_transcript"].append(text)
            
            asyncio.run_coroutine_threadsafe(
                self.send_json({"event": "speech_update", "data": {"text": text}}),
                self.loop
            )

        with session_lock:
            client_sessions[self.sid] = {
                "metrics": InterviewMetrics(),
                "processing_active": False,
                "stt_recognizer": SpeechToTextFromStream(recognized_callback=stt_callback),
                "audio_buffer": bytearray(),
                "full_transcript": [],
            }
        await self.send_json({"event": "connection_status", "data": {"status": "connected"}})

    async def disconnect(self, close_code):
        with session_lock:
            session = client_sessions.pop(self.sid, None)
            if session:
                if session.get("stt_recognizer"): session["stt_recognizer"].stop()
        print(f"[{self.sid}] 클라이언트 연결 끊김.")

    async def receive_json(self, content):
        event = content.get("event")
        data = content.get("data", {})
        handler = getattr(self, f"handle_{event}", None)
        if handler: await handler(data)

    async def handle_video_frame(self, data):
        session = client_sessions.get(self.sid)
        if not session or not session["metrics"].analyzing: return
        try:
            image_data = data.get("image")
            if image_data:
                _, encoded = image_data.split(",", 1)
                frame = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    video_metrics, _ = process_frame(frame, session["metrics"])

                    print(video_metrics)
                    # Optionally send real-time non-verbal metrics if needed
                    # await self.send_json({"event": "realtime_metrics", "data": video_metrics})
        except Exception as e:
            print(f"[{self.sid}] 비디오 프레임 처리 오류: {e}")

    async def handle_audio_chunk(self, data):
        session = client_sessions.get(self.sid)
        if not session or not session["metrics"].analyzing: return
        try:
            audio_data = base64.b64decode(data.get("audio"))
            session["audio_buffer"].extend(audio_data)
            if session.get("stt_recognizer"): 
                session["stt_recognizer"].write_chunk(audio_data)
        except Exception as e:
            print(f"[{self.sid}] 오디오 청크 처리 오류: {e}")

    async def handle_toggle_analysis(self, data):
        session = client_sessions.get(self.sid)
        if not session: return
        is_analyzing = data.get("analyze", False)
        session["metrics"].analyzing = is_analyzing
        if is_analyzing:
            session["metrics"].analysis_start_time = time.time()
            session["audio_buffer"].clear()
            session["full_transcript"].clear()
        await self.send_json({"event": "analysis_status", "data": {"analyzing": is_analyzing}})

    async def handle_finish_analysis(self, data):
        await self.send_json({"event": "analysis_pending", "data": {"message": "분석을 시작합니다..."}})
        session = client_sessions.get(self.sid)
        if not session: return

        # Stop real-time processing
        session["metrics"].analyzing = False
        session["metrics"].analysis_end_time = time.time()

        # Get audio buffer and transcript
        audio_buffer = session["audio_buffer"]
        full_transcript = " ".join(session["full_transcript"])

        # Start background thread for heavy analysis
        threading.Thread(
            target=analysis_worker, 
            args=(self.sid, self, audio_buffer, full_transcript),
            daemon=True
        ).start()
