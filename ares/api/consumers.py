# ares/api/consumers.py
import json
import base64
import time
import threading
import asyncio
from queue import Queue, Empty

import cv2
import numpy as np
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from ares.api.services.interview_metrics import InterviewMetrics
from ares.api.services.interview_analysis_service import get_detailed_analysis_data, process_frame
from ares.api.services.openai_advisor import InterviewAdvisor

# ==============================================================================
# Session and AI Advisor Management
# ==============================================================================

# Thread-safe session management
client_sessions = {}
session_lock = threading.Lock()

# Global AI advisor instance
advisor = None

def init_ai_advisor():
    """Initializes the AI advisor instance."""
    global advisor
    try:
        advisor = InterviewAdvisor()
        print("🤖 AI 조언 시스템 초기화 완료")
        return True
    except Exception as e:
        print(f"❌ AI 조언 시스템 초기화 실패: {e}")
        advisor = None
        return False

# Initialize the advisor when the module is loaded
init_ai_advisor()

# ==============================================================================
# Worker Threads
# ==============================================================================

def process_frame_worker(sid, consumer_instance):
    """Processes video frames in a separate thread."""
    print(f"[{sid}] 프레임 처리 워커 시작")
    while True:
        with session_lock:
            session = client_sessions.get(sid)
            if not session or not session["processing_active"]:
                break
        
        try:
            frame = session["frame_queue"].get(timeout=1)
            if frame is None: # Poison pill
                break

            current_metrics, _ = process_frame(frame, session["metrics"])

            if not session["metrics_queue"].full():
                session["metrics_queue"].put(current_metrics)

        except Empty:
            continue
        except Exception as e:
            print(f"[{sid}] 프레임 처리 워커 오류: {e}")
            time.sleep(0.1)
            
    print(f"[{sid}] 프레임 처리 워커 종료")

def metrics_sender_worker(sid, consumer_instance):
    """Sends metrics to the client via WebSocket in a separate thread."""
    print(f"[{sid}] 메트릭 전송 워커 시작")
    loop = consumer_instance.loop

    while True:
        with session_lock:
            session = client_sessions.get(sid)
            if not session or not session["processing_active"]:
                break
        
        try:
            metrics = session["metrics_queue"].get(timeout=1)
            if metrics:
                # Schedule the send_json coroutine on the consumer's event loop
                asyncio.run_coroutine_threadsafe(
                    consumer_instance.send_json({"event": "metrics_update", "data": metrics}),
                    loop
                )
        except Empty:
            continue
        except Exception as e:
            print(f"[{sid}] 메트릭 전송 워커 오류: {e}")
            time.sleep(0.1)

    print(f"[{sid}] 메트릭 전송 워커 종료")

# ==============================================================================
# Interview Consumer
# ==============================================================================

class InterviewConsumer(AsyncJsonWebsocketConsumer):
    """Handles WebSocket connections for the AI Interview Coach."""

    async def connect(self):
        """Handles a new WebSocket connection."""
        self.sid = self.channel_name
        self.loop = asyncio.get_running_loop()
        await self.accept()

        with session_lock:
            print(f"[{self.sid}] 새로운 세션 생성 중...")
            client_sessions[self.sid] = {
                "metrics": InterviewMetrics(),
                "frame_queue": Queue(maxsize=2),
                "metrics_queue": Queue(maxsize=5),
                "processing_active": True,
                "processing_thread": None,
                "metrics_thread": None,
            }
            print(f"[{self.sid}] 세션 생성 완료. 현재 활성 세션: {len(client_sessions)}")

        # Start worker threads
        session = client_sessions[self.sid]
        session["processing_thread"] = threading.Thread(target=process_frame_worker, args=(self.sid, self), daemon=True)
        session["metrics_thread"] = threading.Thread(target=metrics_sender_worker, args=(self.sid, self), daemon=True)
        session["processing_thread"].start()
        session["metrics_thread"].start()

        await self.send_json({"event": "connection_status", "data": {"status": "connected", "timestamp": time.time()}})

    async def disconnect(self, close_code):
        """Handles a WebSocket disconnection."""
        print(f"[{self.sid}] 클라이언트 연결 끊김 (코드: {close_code})")
        with session_lock:
            session = client_sessions.pop(self.sid, None)
            if session:
                session["processing_active"] = False
                # Poison pill to stop frame worker if it's waiting on the queue
                try:
                    session["frame_queue"].put_nowait(None)
                except Exception:
                    pass
            print(f"[{self.sid}] 세션 정리 완료. 현재 활성 세션: {len(client_sessions)}")

    async def receive_json(self, content):
        """Receives a message from the WebSocket and dispatches it."""
        event = content.get("event")
        data = content.get("data", {})

        handler_map = {
            "video_frame": self.handle_video_frame,
            "toggle_analysis": self.handle_toggle_analysis,
            "reset_metrics": self.handle_reset_metrics,
            "get_summary": self.handle_get_summary,
            "generate_ai_advice": self.handle_generate_ai_advice,
        }

        handler = handler_map.get(event)
        if handler:
            await handler(data)
        else:
            print(f"[{self.sid}] 알 수 없는 이벤트 수신: {event}")

    async def handle_video_frame(self, data):
        """Handles incoming video frames."""
        session = client_sessions.get(self.sid)
        if not session:
            return

        try:
            image_data = data.get("image")
            if image_data:
                header, encoded = image_data.split(",", 1)
                decoded_image = base64.b64decode(encoded)
                nparr = np.frombuffer(decoded_image, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is not None:
                    if not session["frame_queue"].full():
                        session["frame_queue"].put(frame)
        except Exception as e:
            print(f"[{self.sid}] 비디오 프레임 처리 오류: {e}")

    async def handle_toggle_analysis(self, data):
        """Toggles the analysis state."""
        session = client_sessions.get(self.sid)
        if not session:
            return
        
        metrics = session["metrics"]
        analyzing = data.get("analyze", False)
        session_duration = 0
        
        if analyzing:
            metrics.analysis_start_time = time.time()
            metrics.analysis_end_time = None
        else:
            metrics.analysis_end_time = time.time()
            if metrics.analysis_start_time:
                session_duration = metrics.analysis_end_time - metrics.analysis_start_time
        
        metrics.analyzing = analyzing
        
        await self.send_json({
            "event": "analysis_status",
            "data": {
                "analyzing": metrics.analyzing,
                "timestamp": time.time(),
                "session_duration": session_duration if not analyzing and metrics.analysis_end_time else 0,
            }
        })

    async def handle_reset_metrics(self, data):
        """Resets the metrics for the current session."""
        session = client_sessions.get(self.sid)
        if not session:
            return

        old_analyzing = session["metrics"].analyzing
        session["metrics"] = InterviewMetrics()
        session["metrics"].analyzing = old_analyzing
        
        await self.send_json({
            "event": "reset_metrics_response",
            "data": {"status": "success", "message": "Metrics reset successfully"}
        })

    async def handle_get_summary(self, data):
        """Sends a summary of the current metrics."""
        session = client_sessions.get(self.sid)
        if not session:
            return
        
        metrics = session["metrics"]
        # This is a simplified summary. The detailed one is in get_detailed_analysis_data.
        summary_data = {
            "total_frames": metrics.frame_count,
            "blink_count": metrics.blink_count,
            "nod_count": metrics.nod_count,
            "shake_count": metrics.shake_count,
            "total_smile_time": round(metrics.total_smile_time, 1),
            "posture_sway_count": metrics.posture_sway_count,
            "hand_gesture_count": metrics.hand_gesture_count,
        }
        await self.send_json({"event": "get_summary_response", "data": {"status": "success", "data": summary_data}})

    async def handle_generate_ai_advice(self, data):
        """Generates and sends AI-based advice."""
        session = client_sessions.get(self.sid)
        if not session:
            await self.send_json({"event": "generate_ai_advice_response", "data": {"status": "error", "message": "Session not found"}})
            return

        if not advisor:
            await self.send_json({
                "event": "generate_ai_advice_response",
                "data": {
                    "status": "error",
                    "message": "AI 조언 시스템이 초기화되지 않았습니다.",
                    "fallback_advice": "기본 조언을 사용합니다. Azure OpenAI 설정을 확인해주세요.",
                }
            })
            return

        analysis_data = get_detailed_analysis_data(session["metrics"])
        
        # Run the blocking IO call in a separate thread
        advice_result = await asyncio.to_thread(advisor.generate_advice, analysis_data)
        
        response_data = {}
        if advice_result["status"] == "success":
            response_data = {
                "status": "success",
                "advice": advice_result["advice"],
                "analysis_summary": advice_result.get("analysis_summary", {}),
                "timestamp": advice_result["timestamp"],
            }
        else:
            response_data = {
                "status": "error",
                "message": advice_result.get("message", "AI 조언 생성 실패"),
                "fallback_advice": advice_result.get("fallback_advice", "기본 조언을 생성할 수 없습니다."),
            }
            
        await self.send_json({"event": "generate_ai_advice_response", "data": response_data})
