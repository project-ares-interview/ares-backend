# ares/api/consumers.py
import base64
import time
import threading
import asyncio
import io
import cv2
import wave
import json
import queue

from channels.generic.websocket import AsyncWebsocketConsumer, AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from pydub import AudioSegment
import numpy as np

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
# Analysis Worker Thread (수정됨)
# ==============================================================================
def analysis_worker(sid, audio_buffer, full_transcript, user_gender):
    """백그라운드에서 최종 음성/영상 분석을 실행하고 결과를 그룹으로 전송"""
    group_name = f"session_{sid}"
    channel_layer = get_channel_layer()
    print(f"[{sid}] 백그라운드 분석 시작...")

    # 1. Voice Analysis (오디오 데이터가 있을 때만 실행)
    if audio_buffer and len(audio_buffer) > 1000: # 최소 데이터 길이 보장
        try:
            if len(audio_buffer) % 2 != 0:
                audio_buffer = audio_buffer[:-1]
            
            audio_data = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
            sample_rate = 16000
            voice_scores = analyze_voice_from_buffer(audio_data, sample_rate, full_transcript, gender=user_gender)
            
            message_data = {"event": "voice_scores_update", "data": voice_scores} if voice_scores else {"event": "error", "data": {"message": "음성 점수 분석에 실패했습니다."}}
            async_to_sync(channel_layer.group_send)(group_name, {"type": "analysis.event", "data": message_data})
        except Exception as e:
            print(f"[{sid}] 음성 분석 워커 오류: {e}")
            async_to_sync(channel_layer.group_send)(group_name, {"type": "analysis.event", "data": {"event": "error", "data": {"message": f"음성 분석 중 오류 발생: {e}"}}})
    else:
        print(f"[{sid}] 오디오 데이터가 너무 짧거나 없어 음성 분석을 건너뜁니다.")
        async_to_sync(channel_layer.group_send)(group_name, {"type": "analysis.event", "data": {"event": "voice_scores_update", "data": {}}})

    # 2. Video Analysis
    with session_lock:
        session = client_sessions.get(sid)
        if session:
            video_analysis = get_detailed_analysis_data(session["metrics"])
            async_to_sync(channel_layer.group_send)(group_name, {"type": "analysis.event", "data": {"event": "video_analysis_update", "data": video_analysis}})

    print(f"[{sid}] 백그라운드 분석 종료.")

# ==============================================================================
# Results Consumer (제어 및 결과 수신)
# ==============================================================================
class InterviewResultsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.sid = self.scope['url_route']['kwargs']['sid']
        self.group_name = f"session_{self.sid}"
        
        with session_lock:
            if self.sid not in client_sessions:
                client_sessions[self.sid] = {"metrics": InterviewMetrics()}
        
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"event": "connection_status", "data": {"status": "connected", "sid": self.sid}})

    async def disconnect(self, close_code):
        with session_lock:
            client_sessions.pop(self.sid, None)
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content):
        event = content.get("event")
        data = content.get("data", {})

        if event == 'toggle_analysis':
            with session_lock:
                session = client_sessions.get(self.sid)
                if session:
                    is_analyzing = data.get("analyze", False)
                    session["metrics"].analyzing = is_analyzing
                    if is_analyzing:
                        session["metrics"].analysis_start_time = time.time()
            await self.send_json({"event": "analysis_status", "data": {"analyzing": is_analyzing}})

        elif event == 'finish_analysis_signal':
            await self.send_json({"event": "analysis_pending", "data": {"message": "분석을 시작합니다..."}})
            
            with session_lock:
                session = client_sessions.get(self.sid)
                if not session: return

                # AudioConsumer가 최종 오디오를 처리할 수 있도록 상태를 먼저 변경
                session["metrics"].analyzing = False
                session["metrics"].analysis_end_time = time.time()

            # AudioConsumer가 최종 오디오 Blob을 처리하고 버퍼에 저장할 시간을 줌
            await asyncio.sleep(1.5) # 네트워크 지연 등을 고려하여 대기 시간 확보

            with session_lock:
                session = client_sessions.get(self.sid) # 최신 세션 정보 다시 로드
                final_audio_buffer = session.get("final_audio_buffer", bytearray())
                full_transcript = " ".join(session.get("full_transcript", []))

                        # Get user gender from scope
            user = self.scope['user']
            user_gender = 'unknown'
            if user.is_authenticated and hasattr(user, 'gender') and user.gender:
                user_gender = user.gender.upper() # Ensure it's 'MALE' or 'FEMALE'
            
            threading.Thread(target=analysis_worker, args=(self.sid, final_audio_buffer, full_transcript, user_gender), daemon=True).start()

    async def analysis_event(self, event):
        """다른 consumer나 worker로부터 받은 분석 결과를 클라이언트에 전송"""
        await self.send_json(event['data'])

# ==============================================================================
# Audio Consumer (실시간 바이너리 오디오 수신)
# ==============================================================================
class InterviewAudioConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.sid = self.scope['url_route']['kwargs']['sid']
        self.group_name = f"session_{self.sid}"
        self.channel_layer = get_channel_layer()
        self.stt_queue = queue.Queue() # 스레드 안전 큐

        def stt_callback(text, duration_sec, event_type):
            self.stt_queue.put({"text": text, "type": event_type})
            
        with session_lock:
            # 세션이 없으면 생성 (ResultsConsumer보다 먼저 연결될 경우 대비)
            if self.sid not in client_sessions:
                client_sessions[self.sid] = {"metrics": InterviewMetrics()}
            
            session = client_sessions.get(self.sid)
            # Raw PCM 스트림 포맷 정의
            try:
                import azure.cognitiveservices.speech as speechsdk
                pcm_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
                session["stt_recognizer"] = SpeechToTextFromStream(recognized_callback=stt_callback, stream_format=pcm_format)
            except ImportError:
                print("Azure SDK가 설치되지 않아 STT를 비활성화합니다.")
                session["stt_recognizer"] = None
            
            session["final_audio_buffer"] = bytearray()
            if "full_transcript" not in session:
                session["full_transcript"] = []

        await self.accept()
        # 큐를 주기적으로 확인하는 백그라운드 작업 시작
        self.queue_checker_task = asyncio.create_task(self.check_stt_queue())

    async def disconnect(self, close_code):
        if not self.queue_checker_task.done():
            self.queue_checker_task.cancel()
        with session_lock:
            session = client_sessions.get(self.sid)
            if session and session.get("stt_recognizer"):
                session["stt_recognizer"].stop()

    async def check_stt_queue(self):
        while True:
            await asyncio.sleep(0.1)
            try:
                while not self.stt_queue.empty():
                    stt_result = self.stt_queue.get_nowait()
                    if not stt_result or not stt_result.get("text"): continue

                    # 1. STT 결과를 실시간으로 ResultsConsumer에 전송
                    message_data = {"event": "speech_update", "data": stt_result}
                    await self.channel_layer.group_send(self.group_name, {"type": "analysis.event", "data": message_data})
                    
                    # 2. 최종 분석을 위해 transcript를 세션에 저장 (최종 인식 결과만)
                    if stt_result.get("type") == "recognized":
                        with session_lock:
                            session = client_sessions.get(self.sid)
                            if session:
                                session["full_transcript"].append(stt_result["text"])
            except Exception as e:
                print(f"[{self.sid}] STT 큐 처리 오류: {e}")

    async def receive(self, bytes_data):
        with session_lock:
            session = client_sessions.get(self.sid)
            if not session: return
            
            # 들어오는 모든 바이너리 데이터는 Raw PCM으로 간주
            if session["metrics"].analyzing:
                if session.get("stt_recognizer"):
                    session["stt_recognizer"].write_chunk(bytes_data)
                session["final_audio_buffer"].extend(bytes_data)

# ==============================================================================
# Video Consumer (실시간 바이너리 비디오 수신)
# ==============================================================================
class InterviewVideoConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.sid = self.scope['url_route']['kwargs']['sid']
        self.group_name = f"session_{self.sid}"
        await self.accept()

    async def receive(self, bytes_data):
        with session_lock:
            session = client_sessions.get(self.sid)
            if not session or not session["metrics"].analyzing: return
        
        try:
            frame = cv2.imdecode(np.frombuffer(bytes_data, np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                video_metrics, _ = process_frame(frame, session["metrics"])
                if video_metrics:
                    # 비동기 Consumer에서는 channel_layer를 직접 await
                    await self.channel_layer.group_send(
                        self.group_name,
                        {
                            "type": "analysis.event",
                            "data": {"event": "realtime_video_update", "data": video_metrics}
                        }
                    )
        except Exception as e:
            print(f"[{self.sid}] 비디오 프레임 처리 오류: {e}")
