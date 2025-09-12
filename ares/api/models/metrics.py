from collections import deque
import time

class InterviewMetrics:
    def __init__(self):
        # ================================
        # 🎯 조정 가능한 임계값들 (수정금지 - 최적화됨)
        # ================================
        self.ear_threshold = 0.12  # EAR 임계값 (낮을수록 민감, 0.15~0.25 권장)
        self.ear_consecutive_frames = 1  # 깜빡임 인정을 위한 연속 프레임 수 (1~3 권장)
        self.ear_calibration_frames = 30  # 개인별 보정을 위한 프레임 수 (20~50 권장)
        self.nod_threshold = 15  # 끄덕임 감지 각도 임계값 (15~25도 권장)
        self.shake_threshold = 25  # 좌우 흔들기 감지 각도 임계값 (20~35도 권장)
        self.nod_cooldown = 1.0  # 끄덕임 감지 후 대기시간(초) (0.5~2.0 권장)
        self.shake_cooldown = 1.0  # 흔들기 감지 후 대기시간(초) (0.5~2.0 권장)
        self.head_min_frames = 8  # 머리 움직임 판단 최소 프레임 수 (5~15 권장)
        self.nod_pattern_frames = 4  # 끄덕임 패턴 분석 프레임 수 (3~6 권장)
        self.movement_threshold = (
            0.06  # 어깨 움직임 임계값 (낮을수록 민감, 0.02~0.1 권장)
        )
        self.posture_min_frames = 10  # 자세 판단 최소 프레임 수 (5~20 권장)
        self.posture_sway_cooldown = 2.0  # 자세 흔들림 감지 후 대기시간(초) (1~5 권장)
        self.smile_threshold = 5  # 미소 인정 임계값 (3~10 권장)
        self.smile_intensity_high = 10  # 강한 미소 임계값 (8~15 권장)
        self.hand_gesture_threshold = 0.03  # 손 움직임 임계값 (약간 높게 설정)
        self.hand_gesture_cooldown = 1.5  # 제스쳐 감지 후 대기시간 (길게 설정)
        self.hand_min_frames = 6  # 제스쳐 판단 최소 프레임 수
        self.hand_analysis_interval = 5  # 5프레임마다 손 분석 (성능 최적화)
        self.enable_hand_analysis = True  # 손 분석 활성화/비활성화
        self.frame_skip_base = 1  # 모든 프레임 분석 (기존 0 -> 1)
        self.frame_skip_with_hands = 1  # 모든 프레임 분석 (기존 1 -> 1)

        # ================================
        # 🕒 AI 면접 코치 전용 변수들
        # ================================
        self.analysis_start_time = None  # 분석 시작 시간
        self.analysis_end_time = None  # 분석 종료 시간
        self.session_duration = 0  # 총 세션 시간

        # ================================
        # 시스템 변수들 (건드리지 마세요!)
        # ================================
        self.analyzing = False
        self.frame_skip = 0
        self.skip_interval = self.frame_skip_base
        self.blink_count = 0
        self.last_blink_time = 0
        self.ear_below = False
        self.ear_closed_frames = 0
        self.ear_history_for_calibration = deque(maxlen=self.ear_calibration_frames)
        self.is_ear_calibrated = False
        self.dynamic_ear_threshold = self.ear_threshold
        self.head_pose_history = deque(maxlen=15)
        self.nod_count = 0
        self.shake_count = 0
        self.last_nod_time = 0
        self.last_shake_time = 0
        self.pitch_history = deque(maxlen=5)
        self.yaw_history = deque(maxlen=5)
        self.emotion_history = deque(maxlen=5)
        self.micro_expression_count = 0
        self.smile_start_time = None
        self.smile_duration = 0
        self.total_smile_time = 0
        self.smile_intensity_history = deque(maxlen=30)
        self.shoulder_positions = deque(maxlen=30)
        self.posture_sway_count = 0
        self.last_sway_time = 0
        self.hand_positions = deque(maxlen=15)  # 크기 축소
        self.hand_gesture_count = 0
        self.last_gesture_time = 0
        self.hands_visible_count = 0
        self.hand_frame_counter = 0  # 손 분석 주기 카운터
        self.frame_count = 0
        self.start_time = time.time()
        self.processing_active = True
