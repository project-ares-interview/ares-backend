import mediapipe as mp

# MediaPipe 초기화 (성능 최적화)
mp_face_mesh = mp.solutions.face_mesh
mp_pose = mp.solutions.pose
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils

face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=False,
    min_detection_confidence=0.3,
    min_tracking_confidence=0.3,
)

pose = mp_pose.Pose(
    static_image_mode=False,
    model_complexity=0,  # 최소 복잡도
    min_detection_confidence=0.3,
    min_tracking_confidence=0.3,
)

# 손 제스쳐는 선택적 활성화 (성능 고려)
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.7,  # 임계값 높여서 연산 감소
    min_tracking_confidence=0.7,
)