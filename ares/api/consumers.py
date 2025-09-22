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
def analysis_worker(sid, audio_buffer, full_transcript, user_gender, total_speaking_time):
    """백그라운드에서 최종 음성/영상/텍스트 분석을 실행하고 결과를 그룹으로 전송"""
    # Django 설정 로딩이 끝난 후, 함수 내에서 import
    from ares.api.models import InterviewSession, InterviewTurn
    from ares.api.services import resume_service
    from ares.api.services.rag.final_interview_rag import RAGInterviewBot

    group_name = f"session_{sid}"
    channel_layer = get_channel_layer()
    print(f"[{sid}] 백그라운드 분석 시작...")

    # 1. Voice Analysis
    if audio_buffer and len(audio_buffer) > 1000:
        try:
            if len(audio_buffer) % 2 != 0:
                audio_buffer = audio_buffer[:-1]
            
            audio_data = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
            sample_rate = 16000
            voice_scores = analyze_voice_from_buffer(audio_data, sample_rate, full_transcript, gender=user_gender, total_speaking_time=total_speaking_time)
            
            message_data = {"event": "voice_scores_update", "data": voice_scores} if voice_scores else {"event": "error", "data": {"message": "음성 점수 분석에 실패했습니다."}}
            async_to_sync(channel_layer.group_send)(group_name, {"type": "analysis.event", "data": message_data})
        except Exception as e:
            print(f"[{sid}] 음성 분석 워커 오류: {e}")

    # 2. Video Analysis
    with session_lock:
        session_data = client_sessions.get(sid)
        if session_data:
            video_analysis = get_detailed_analysis_data(session_data["metrics"])
            async_to_sync(channel_layer.group_send)(group_name, {"type": "analysis.event", "data": {"event": "video_analysis_update", "data": video_analysis}})

    # 3. Final Text Analysis (RAG-based Report)
    try:
        print(f"[{sid}] 텍스트 분석: DB에서 세션 조회 시작...")
        # 로그인 세션이 없으므로 sid로 직접 조회 (프로필 포함)
        session = InterviewSession.objects.select_related('user__profile').get(id=sid)
        print(f"[{sid}] 텍스트 분석: 세션 조회 완료. RAG 컨텍스트 확인 시작...")
        rag_context = session.rag_context or {}
        
        if not rag_context.get("company_name") or not rag_context.get("job_title"):
            raise ValueError("RAG 컨텍스트에 회사/직무 정보가 없습니다.")
        
        # --- 컨텍스트 로드 (Profile 우선) ---
        jd_context = ""
        resume_context = ""
        research_context = ""
        if hasattr(session.user, 'profile'):
            jd_context = getattr(session.user.profile, 'jd_context', '')
            resume_context = getattr(session.user.profile, 'resume_context', '')
            research_context = getattr(session.user.profile, 'research_context', '')

        print(f"[{sid}] 텍스트 분석: RAG 봇 인스턴스화 시작...")
        rag_bot = RAGInterviewBot(
            company_name=rag_context.get("company_name", ""),
            job_title=rag_context.get("job_title", ""),
            container_name=rag_context.get("container_name", ""),
            index_name=rag_context.get("index_name", ""),
            interviewer_mode=session.interviewer_mode,
            resume_context=resume_context or (session.resume_context or ""),
            ncs_context=session.context or {},
            jd_context=jd_context or (session.jd_context or ""),
        )
        print(f"[{sid}] 텍스트 분석: RAG 봇 인스턴스화 완료. 대화 기록 조회 시작...")

        turns = session.turns.order_by("turn_index").all()
        transcript = []
        structured_scores = []
        for t in turns:
            role_str = "interviewer" if t.role == InterviewTurn.Role.INTERVIEWER else "candidate"
            text = t.question if t.role == InterviewTurn.Role.INTERVIEWER else (t.answer or "")
            transcript.append({
                "role": role_str,
                "text": text,
                "id": t.turn_label,
            })
            if t.role == InterviewTurn.Role.CANDIDATE and t.scores:
                structured_scores.append(t.scores)

        print(f"[{sid}] 텍스트 분석: 대화 기록 조회 완료. 최종 리포트 생성 시작 (LLM 호출)...")

        # --- 이력서 분석 수행 (뷰 로직과 동일) ---
        resume_feedback = {}
        try:
            company_meta = {
                "company_name": rag_context.get("company_name", ""),
                "job_title": rag_context.get("job_title", ""),
            }
            full_resume_analysis = resume_service.analyze_all(
                jd_text=jd_context,
                resume_text=resume_context,
                research_text=research_context,
                company_meta=company_meta,
            )
            resume_feedback = full_resume_analysis.get("resume_feedback", {})
        except Exception as e:
            print(f"[{sid}] 이력서 분석 실패: {e}")
            resume_feedback = {"error": f"Resume analysis failed: {e}"}

        interview_plan = (rag_context.get("interview_plans", {}) or {}).get("raw_v2_plan", {})
        full_contexts = {
            "jd_context": jd_context,
            "resume_context": resume_context,
            "research_context": research_context,
        }

        final_report = rag_bot.build_final_report(
            transcript=transcript,
            structured_scores=structured_scores,
            interview_plan=interview_plan,
            resume_feedback=resume_feedback,
            full_contexts=full_contexts,
        )
        print(f"[{sid}] 텍스트 분석: 최종 리포트 생성 완료.")

        async_to_sync(channel_layer.group_send)(group_name, {
            "type": "analysis.event",
            "data": {"event": "text_analysis_update", "data": final_report}
        })

    except Exception as e:
        print(f"[{sid}] 최종 텍스트 분석 리포트 생성 오류: {e}")
        async_to_sync(channel_layer.group_send)(group_name, {
            "type": "analysis.event",
            "data": {"event": "error", "data": {"message": f"텍스트 분석 리포트 생성 중 오류 발생: {e}"}}
        })
    
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

        with session_lock:
            session = client_sessions.get(self.sid)
            if not session:
                return

            if event == 'toggle_analysis':
                is_analyzing = data.get("analyze", False)
                session["metrics"].analyzing = is_analyzing
                if is_analyzing:
                    session["metrics"].analysis_start_time = time.time()
                asyncio.create_task(self.send_json({"event": "analysis_status", "data": {"analyzing": is_analyzing}}))

            elif event == 'finish_analysis_signal':
                asyncio.create_task(self.send_json({"event": "analysis_pending", "data": {"message": "분석을 시작합니다..."}}))
                
                session["metrics"].analyzing = False
                session["metrics"].analysis_end_time = time.time()

                asyncio.create_task(self.start_analysis_after_delay(session))

    async def start_analysis_after_delay(self, session):
        await asyncio.sleep(1.5)

        with session_lock:
            final_audio_buffer = session.get("final_audio_buffer", bytearray())
            full_transcript = " ".join(session.get("full_transcript", []))
            # total_speaking_time은 현재 사용되지 않으므로 제거 또는 주석 처리 가능
            total_speaking_time = session.get("total_speaking_time", 0.0)

            user = self.scope['user']
            user_gender = 'unknown'
            if user.is_authenticated and hasattr(user, 'gender') and user.gender:
                user_gender = user.gender.upper()

        await asyncio.to_thread(analysis_worker, self.sid, final_audio_buffer, full_transcript, user_gender, total_speaking_time)

    async def analysis_event(self, event):
        await self.send_json(event['data'])

# ==============================================================================
# Audio Consumer (실시간 바이너리 오디오 수신)
# ==============================================================================
class InterviewAudioConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.sid = self.scope['url_route']['kwargs']['sid']
        self.group_name = f"session_{self.sid}"
        self.channel_layer = get_channel_layer()
        self.stt_queue = queue.Queue()

        def stt_callback(text, duration_sec, event_type):
            self.stt_queue.put({"text": text, "type": event_type})
            
        with session_lock:
            if self.sid not in client_sessions:
                client_sessions[self.sid] = {"metrics": InterviewMetrics()}
            
            session = client_sessions.get(self.sid)
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

                    message_data = {"event": "speech_update", "data": stt_result}
                    await self.channel_layer.group_send(self.group_name, {"type": "analysis.event", "data": message_data})
                    
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
                    await self.channel_layer.group_send(
                        self.group_name,
                        {
                            "type": "analysis.event",
                            "data": {"event": "realtime_video_update", "data": video_metrics}
                        }
                    )
        except Exception as e:
            print(f"[{self.sid}] 비디오 프레임 처리 오류: {e}")
