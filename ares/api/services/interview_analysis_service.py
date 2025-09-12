import cv2
import numpy as np
import math
import time
from datetime import datetime

# MediaPipe 모델과 드로잉 유틸리티를 유틸 파일에서 임포트
from ares.api.utils.mediapipe_init import face_mesh, pose, hands, mp_drawing, mp_face_mesh, mp_pose, mp_hands

# One Euro Filter를 유틸 파일에서 임포트
from ares.api.utils.one_euro_filter import OneEuroFilter


# 헤드 포즈 필터 인스턴스 생성
pitch_filter = OneEuroFilter(min_cutoff=0.1, beta=0.01)
yaw_filter = OneEuroFilter(min_cutoff=0.1, beta=0.01)
roll_filter = OneEuroFilter(min_cutoff=0.1, beta=0.01)


def calibrate_ear_threshold(metrics):
    """동적 EAR 임계값 보정"""
    if len(metrics.ear_history_for_calibration) >= metrics.ear_calibration_frames:
        ear_values = list(metrics.ear_history_for_calibration)
        mean_ear = np.mean(ear_values)
        std_ear = np.std(ear_values)

        metrics.dynamic_ear_threshold = max(0.15, mean_ear - 2 * std_ear)
        metrics.is_ear_calibrated = True

        # print(f"🎯 EAR 임계값 보정 완료: {metrics.dynamic_ear_threshold:.3f}")


def calculate_ear(landmarks, eye_indices):
    """눈 종횡비(EAR) 계산"""
    try:
        coords = np.array([[landmarks[i].x, landmarks[i].y] for i in eye_indices])
        A = np.linalg.norm(coords[1] - coords[5])
        B = np.linalg.norm(coords[2] - coords[4])
        C = np.linalg.norm(coords[0] - coords[3])
        return (A + B) / (2.0 * C) if C > 0 else 0
    except (IndexError, ZeroDivisionError):
        return 0


def calculate_head_pose(landmarks, img_size):
    """머리 자세 각도 계산 - One Euro Filter 적용"""
    try:
        h, w = img_size

        model_points = np.array(
            [
                (0.0, 0.0, 0.0),
                (0.0, -330.0, -65.0),
                (-225.0, 170.0, -135.0),
                (225.0, 170.0, -135.0),
                (-150.0, -150.0, -125.0),
                (150.0, -150.0, -125.0),
            ],
            dtype=np.float64,
        )

        image_points = np.array(
            [
                [landmarks[1].x * w, landmarks[1].y * h],
                [landmarks[175].x * w, landmarks[175].y * h],
                [landmarks[33].x * w, landmarks[33].y * h],
                [landmarks[263].x * w, landmarks[263].y * h],
                [landmarks[61].x * w, landmarks[61].y * h],
                [landmarks[291].x * w, landmarks[291].y * h],
            ],
            dtype=np.float64,
        )

        focal_length = w
        camera_matrix = np.array(
            [[focal_length, 0, w / 2], [0, focal_length, h / 2], [0, 0, 1]],
            dtype=np.float64,
        )

        dist_coeffs = np.zeros((4, 1))

        success, rotation_vector, translation_vector = cv2.solvePnP(
            model_points, image_points, camera_matrix, dist_coeffs
        )

        if success:
            rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
            sy = math.sqrt(rotation_matrix[0, 0] ** 2 + rotation_matrix[1, 0] ** 2)

            if sy > 1e-6:
                x = math.atan2(rotation_matrix[2, 1], rotation_matrix[2, 2])
                y = math.atan2(-rotation_matrix[2, 0], sy)
                z = math.atan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
            else:
                x = math.atan2(-rotation_matrix[1, 2], rotation_matrix[1, 1])
                y = math.atan2(-rotation_matrix[2, 0], sy)
                z = 0

            pitch, yaw, roll = math.degrees(x), math.degrees(y), math.degrees(z)

            current_time = time.time()
            filtered_pitch = pitch_filter.filter(pitch, current_time)
            filtered_yaw = yaw_filter.filter(yaw, current_time)
            filtered_roll = roll_filter.filter(roll, current_time)

            return filtered_pitch, filtered_yaw, filtered_roll

    except Exception as e:
        print(f"Head pose calculation error: {e}")
        return 0, 0, 0


def is_relaxed_nod_pattern(pitch_sequence, metrics):
    """완화된 끄덕임 패턴 검증"""
    if len(pitch_sequence) < metrics.nod_pattern_frames:
        return False
    pitch_range = max(pitch_sequence) - min(pitch_sequence)
    if pitch_range < 12:
        return False
    direction_changes = 0
    for i in range(1, len(pitch_sequence)):
        if (
            i > 1
            and (pitch_sequence[i] - pitch_sequence[i - 1])
            * (pitch_sequence[i - 1] - pitch_sequence[i - 2])
            < 0
        ):
            direction_changes += 1
    return direction_changes >= 1


def is_relaxed_shake_pattern(yaw_sequence, metrics):
    """완화된 흔들기 패턴 검증"""
    if len(yaw_sequence) < metrics.nod_pattern_frames:
        return False
    yaw_range = max(yaw_sequence) - min(yaw_sequence)
    if yaw_range < 15:
        return False
    direction_changes = 0
    for i in range(2, len(yaw_sequence)):
        if (yaw_sequence[i] - yaw_sequence[i - 1]) * (
            yaw_sequence[i - 1] - yaw_sequence[i - 2]
        ) < 0:
            direction_changes += 1
    return direction_changes >= 1


def detect_nod_shake(current_pitch, current_yaw, metrics):
    """개선된 끄덕임/흔들기 감지"""
    current_time = time.time()
    metrics.pitch_history.append(current_pitch)
    metrics.yaw_history.append(current_yaw)
    if len(metrics.pitch_history) < 5:
        return
    if current_time - metrics.last_nod_time > metrics.nod_cooldown:
        if len(metrics.head_pose_history) > metrics.head_min_frames:
            recent_pitches = [
                pose[0]
                for pose in list(metrics.head_pose_history)[-metrics.head_min_frames :]
            ]
            pitch_range = max(recent_pitches) - min(recent_pitches)
            if pitch_range > metrics.nod_threshold:
                pitch_seq = recent_pitches[-metrics.nod_pattern_frames :]
                if is_relaxed_nod_pattern(pitch_seq, metrics):
                    metrics.nod_count += 1
                    metrics.last_nod_time = current_time
                    # print(f"✅ 끄덕임 감지! {metrics.nod_count}회")
                    metrics.head_pose_history.clear()
    if current_time - metrics.last_shake_time > metrics.shake_cooldown:
        if len(metrics.head_pose_history) > metrics.head_min_frames:
            recent_yaws = [
                pose[1]
                for pose in list(metrics.head_pose_history)[-metrics.head_min_frames :]
            ]
            yaw_range = max(recent_yaws) - min(recent_yaws)
            if yaw_range > metrics.shake_threshold:
                yaw_seq = recent_yaws[-metrics.nod_pattern_frames :]
                if is_relaxed_shake_pattern(yaw_seq, metrics):
                    metrics.shake_count += 1
                    metrics.last_shake_time = current_time
                    # print(f"✅ 좌우 흔들기 감지! {metrics.shake_count}회")
                    metrics.head_pose_history.clear()


def calculate_smile_intensity(landmarks):
    """미소 강도 계산"""
    try:
        left_mouth = landmarks[61]
        right_mouth = landmarks[291]
        mouth_center = landmarks[13]
        left_lift = mouth_center.y - left_mouth.y
        right_lift = mouth_center.y - right_mouth.y
        smile_intensity = (left_lift + right_lift) / 2
        return max(0, smile_intensity * 1000)
    except (IndexError, AttributeError):
        return 0


def improved_posture_sway_detection(shoulder_landmarks, metrics):
    """개선된 자세 흔들림 감지"""
    current_time = time.time()
    if current_time - metrics.last_sway_time < metrics.posture_sway_cooldown:
        return
    if not shoulder_landmarks:
        return
    left_shoulder = shoulder_landmarks.get(11)
    right_shoulder = shoulder_landmarks.get(12)
    if left_shoulder and right_shoulder:
        center_x = (left_shoulder.x + right_shoulder.x) / 2
        center_y = (left_shoulder.y + right_shoulder.y) / 2
        metrics.shoulder_positions.append((center_x, center_y))
        if len(metrics.shoulder_positions) > metrics.posture_min_frames:
            recent_positions = list(metrics.shoulder_positions)[
                -metrics.posture_min_frames :
            ]
            x_positions = [pos[0] for pos in recent_positions]
            y_positions = [pos[1] for pos in recent_positions]
            x_range = max(x_positions) - min(x_positions)
            y_range = max(y_positions) - min(y_positions)
            if (
                x_range > metrics.movement_threshold
                or y_range > metrics.movement_threshold
            ):
                metrics.posture_sway_count += 1
                metrics.last_sway_time = current_time
                # print(f"📱 자세 흔들림 감지! {metrics.posture_sway_count}회")
                # 동일한 움직임이 재평가되는 것을 방지하기 위해 기록을 초기화합니다.
                metrics.shoulder_positions.clear()


def detect_hand_gestures_optimized(hand_results, metrics):
    """최적화된 손 제스쳐 감지 (주기적 실행)"""
    metrics.hand_frame_counter += 1
    if metrics.hand_frame_counter < metrics.hand_analysis_interval:
        if hand_results.multi_hand_landmarks:
            metrics.hands_visible_count += 1
        return
    metrics.hand_frame_counter = 0
    current_time = time.time()
    if hand_results.multi_hand_landmarks:
        metrics.hands_visible_count += 1
        for hand_landmarks in hand_results.multi_hand_landmarks:
            wrist = hand_landmarks.landmark[0]
            metrics.hand_positions.append((wrist.x, wrist.y, current_time))
        if current_time - metrics.last_gesture_time > metrics.hand_gesture_cooldown:
            if len(metrics.hand_positions) > metrics.hand_min_frames:
                recent_positions = list(metrics.hand_positions)[
                    -metrics.hand_min_frames :
                ]
                if len(recent_positions) > 1:
                    x_positions = [pos[0] for pos in recent_positions]
                    y_positions = [pos[1] for pos in recent_positions]
                    x_range = max(x_positions) - min(x_positions)
                    y_range = max(y_positions) - min(y_positions)
                    if (
                        x_range > metrics.hand_gesture_threshold
                        or y_range > metrics.hand_gesture_threshold
                    ):
                        metrics.hand_gesture_count += 1
                        metrics.last_gesture_time = current_time
                        # print(f"👋 손 제스쳐 감지! {metrics.hand_gesture_count}회")


def get_detailed_analysis_data(metrics):
    """AI 분석을 위한 상세 데이터 생성"""
    if metrics.analysis_start_time:
        if metrics.analysis_end_time:
            session_duration = metrics.analysis_end_time - metrics.analysis_start_time
        else:
            session_duration = time.time() - metrics.analysis_start_time
    else:
        session_duration = 0
    analysis_data = {
        "session_info": {
            "duration_seconds": round(session_duration, 1),
            "total_frames": metrics.frame_count,
            "analysis_timestamp": datetime.now().isoformat(),
            "start_time": datetime.fromtimestamp(
                metrics.analysis_start_time
            ).isoformat()
            if metrics.analysis_start_time
            else None,
            "end_time": datetime.fromtimestamp(metrics.analysis_end_time).isoformat()
            if metrics.analysis_end_time
            else None,
        },
        "behavioral_metrics": {
            "eye_contact": {
                "blink_count": metrics.blink_count,
                "blink_rate_per_minute": round(
                    metrics.blink_count / (session_duration / 60), 1
                )
                if session_duration > 0
                else 0,
                "average_ear": round(
                    np.mean(list(metrics.ear_history_for_calibration))
                    if metrics.ear_history_for_calibration
                    else 0,
                    3,
                ),
            },
            "facial_expressions": {
                "total_smile_time": round(metrics.total_smile_time, 1),
                "smile_percentage": round(
                    (metrics.total_smile_time / session_duration) * 100, 1
                )
                if session_duration > 0
                else 0,
                "average_smile_intensity": round(
                    np.mean(list(metrics.smile_intensity_history))
                    if metrics.smile_intensity_history
                    else 0,
                    2,
                ),
                "micro_expressions": metrics.micro_expression_count,
            },
            "head_movements": {
                "nod_count": metrics.nod_count,
                "shake_count": metrics.shake_count,
                "head_stability_score": calculate_head_stability(metrics),
            },
            "posture": {
                "sway_count": metrics.posture_sway_count,
                "stability_score": calculate_posture_stability(metrics),
            },
            "hand_gestures": {
                "gesture_count": metrics.hand_gesture_count,
                "hands_visible_seconds": round((metrics.hands_visible_count / 15), 1)
                if metrics.frame_count > 0
                else 0,
                "gesture_frequency_per_minute": round(
                    metrics.hand_gesture_count / (session_duration / 60), 1
                )
                if session_duration > 0
                else 0,
            },
        },
        "scientific_standards": {
            "normal_blink_rate_range": {"min": 12, "max": 20, "unit": "per_minute"},
            "optimal_smile_percentage_range": {"min": 25, "max": 40, "unit": "percent"},
            "appropriate_gesture_frequency_range": {
                "min": 2,
                "max": 8,
                "unit": "per_minute",
            },
            "head_movement_balance": {
                "minimal_nods": 3,
                "optimal_nods": 8,
                "excessive_nods": 15,
                "excessive_shake": 5,
            },
        },
    }
    return analysis_data


def calculate_head_stability(metrics):
    """머리 안정성 점수 계산 (0-100)"""
    if not metrics.head_pose_history:
        return 100
    recent_poses = list(metrics.head_pose_history)[-30:]
    if len(recent_poses) < 10:
        return 100
    pitches = [pose[0] for pose in recent_poses]
    yaws = [pose[1] for pose in recent_poses]
    pitch_std = np.std(pitches)
    yaw_std = np.std(yaws)
    stability = max(0, 100 - (pitch_std + yaw_std) * 2)
    return round(stability, 1)


def calculate_posture_stability(metrics):
    """자세 안정성 점수 계산 (0-100)"""
    if not metrics.shoulder_positions:
        return 100
    positions = list(metrics.shoulder_positions)[-100:]
    if len(positions) < 50:
        return 100
    x_coords = [pos[0] for pos in positions]
    y_coords = [pos[1] for pos in positions]
    x_std = np.std(x_coords)
    y_std = np.std(y_coords)
    stability = max(0, 100 - (x_std + y_std) * 800)
    return round(stability, 1)


def process_frame(frame, metrics):
    """프레임 처리 및 지표 계산 - 성능 최적화됨"""
    if metrics.enable_hand_analysis:
        metrics.skip_interval = metrics.frame_skip_with_hands
    else:
        metrics.skip_interval = metrics.frame_skip_base
    metrics.frame_skip += 1
    if metrics.frame_skip < metrics.skip_interval:
        return {}, frame
    metrics.frame_skip = 0
    try:
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = frame.shape
        current_metrics = {
            "timestamp": time.time(),
            "blink_count": metrics.blink_count,
            "ear": 0.0,
            "head_pose": {"pitch": 0, "yaw": 0, "roll": 0},
            "nod_count": metrics.nod_count,
            "shake_count": metrics.shake_count,
            "emotion": "neutral",
            "micro_expressions": metrics.micro_expression_count,
            "smile_intensity": 0,
            "smile_duration": metrics.smile_duration,
            "total_smile_time": metrics.total_smile_time,
            "posture_sway": metrics.posture_sway_count,
            "hand_gesture_count": metrics.hand_gesture_count,
            "hands_visible_count": metrics.hands_visible_count,
            "frame_count": metrics.frame_count,
        }
        face_results = face_mesh.process(rgb_frame)
        pose_results = pose.process(rgb_frame)
        hand_results = None
        if metrics.enable_hand_analysis:
            hand_results = hands.process(rgb_frame)
        if face_results.multi_face_landmarks:
            mp_drawing.draw_landmarks(
                frame,
                face_results.multi_face_landmarks[0],
                mp_face_mesh.FACEMESH_CONTOURS,
                mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=1, circle_radius=1),
                mp_drawing.DrawingSpec(color=(0, 0, 255), thickness=1),
            )
            landmarks = face_results.multi_face_landmarks[0].landmark
            left_ear = calculate_ear(landmarks, [33, 160, 158, 133, 153, 144])
            right_ear = calculate_ear(landmarks, [362, 385, 387, 263, 373, 380])
            ear = (left_ear + right_ear) / 2
            current_metrics["ear"] = round(ear, 3)
            if not metrics.is_ear_calibrated:
                metrics.ear_history_for_calibration.append(ear)
                if (
                    len(metrics.ear_history_for_calibration)
                    >= metrics.ear_calibration_frames
                ):
                    calibrate_ear_threshold(metrics)
            if metrics.analyzing:
                current_threshold = (
                    metrics.dynamic_ear_threshold
                    if metrics.is_ear_calibrated
                    else metrics.ear_threshold
                )
                if ear < current_threshold:
                    metrics.ear_closed_frames += 1
                    if (
                        not metrics.ear_below
                        and metrics.ear_closed_frames >= metrics.ear_consecutive_frames
                    ):
                        metrics.ear_below = True
                else:
                    if (
                        metrics.ear_below
                        and metrics.ear_closed_frames >= metrics.ear_consecutive_frames
                    ):
                        metrics.blink_count += 1
                        metrics.last_blink_time = time.time()
                        current_metrics["blink_count"] = metrics.blink_count
                        # print(f"✅ 깜빡임 #{metrics.blink_count}번!")
                    metrics.ear_below = False
                    metrics.ear_closed_frames = 0
                pitch, yaw, roll = calculate_head_pose(landmarks, (h, w))
                current_metrics["head_pose"] = {
                    "pitch": round(pitch, 1),
                    "yaw": round(yaw, 1),
                    "roll": round(roll, 1),
                }
                metrics.head_pose_history.append((pitch, yaw, roll))
                detect_nod_shake(pitch, yaw, metrics)
                current_metrics["nod_count"] = metrics.nod_count
                current_metrics["shake_count"] = metrics.shake_count
                smile_intensity = calculate_smile_intensity(landmarks)
                current_metrics["smile_intensity"] = round(smile_intensity, 2)
                metrics.smile_intensity_history.append(smile_intensity)
                if smile_intensity > metrics.smile_threshold:
                    if metrics.smile_start_time is None:
                        metrics.smile_start_time = time.time()
                    else:
                        metrics.smile_duration = time.time() - metrics.smile_start_time
                else:
                    if metrics.smile_start_time is not None:
                        metrics.total_smile_time += metrics.smile_duration
                        metrics.smile_start_time = None
                        metrics.smile_duration = 0
                current_metrics["smile_duration"] = round(metrics.smile_duration, 1)
                current_metrics["total_smile_time"] = round(metrics.total_smile_time, 1)
                if smile_intensity > metrics.smile_intensity_high:
                    emotion = "happy"
                elif abs(pitch) > 20 or abs(yaw) > 30:
                    emotion = "surprised"
                else:
                    emotion = "neutral"
                current_metrics["emotion"] = emotion
                if len(metrics.emotion_history) > 0:
                    if metrics.emotion_history[-1] != emotion:
                        metrics.micro_expression_count += 1
                metrics.emotion_history.append(emotion)
                current_metrics["micro_expressions"] = metrics.micro_expression_count
        if pose_results.pose_landmarks:
            mp_drawing.draw_landmarks(
                frame,
                pose_results.pose_landmarks,
                mp_pose.POSE_CONNECTIONS,
                mp_drawing.DrawingSpec(
                    color=(245, 117, 66), thickness=2, circle_radius=2
                ),
                mp_drawing.DrawingSpec(
                    color=(245, 66, 230), thickness=2, circle_radius=1
                ),
            )
            if metrics.analyzing:
                pose_landmarks = {}
                for idx, landmark in enumerate(pose_results.pose_landmarks.landmark):
                    pose_landmarks[idx] = landmark
                improved_posture_sway_detection(pose_landmarks, metrics)
                current_metrics["posture_sway"] = metrics.posture_sway_count
        if (
            metrics.enable_hand_analysis
            and hand_results
            and hand_results.multi_hand_landmarks
        ):
            for hand_landmarks in hand_results.multi_hand_landmarks:
                mp_drawing.draw_landmarks(
                    frame,
                    hand_landmarks,
                    mp_hands.HAND_CONNECTIONS,
                    mp_drawing.DrawingSpec(
                        color=(255, 255, 0), thickness=2, circle_radius=2
                    ),
                    mp_drawing.DrawingSpec(color=(0, 255, 255), thickness=2),
                )
            if metrics.analyzing:
                detect_hand_gestures_optimized(hand_results, metrics)
                current_metrics["hand_gesture_count"] = metrics.hand_gesture_count
                current_metrics["hands_visible_count"] = metrics.hands_visible_count
        if metrics.analyzing:
            metrics.frame_count += 1
            current_metrics["frame_count"] = metrics.frame_count
        return current_metrics, frame
    except Exception as e:
        print(f"Frame processing error: {e}")
        return {}, frame
