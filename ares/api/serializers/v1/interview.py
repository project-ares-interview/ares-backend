# ares/api/serializers/v1/interview.py
from __future__ import annotations
from rest_framework import serializers


# ===== 공통 =====
class MetaIn(serializers.Serializer):
    company = serializers.CharField(required=False, allow_blank=True, default="")
    division = serializers.CharField(required=False, allow_blank=True, default="")
    role = serializers.CharField(required=False, allow_blank=True, default="")
    skills = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    jd_kpis = serializers.ListField(child=serializers.CharField(), required=False, default=list)


# ===== 내부 유틸: 난이도/언어 정규화 =====
_DIFFICULTY_MAP = {
    "쉬움": "easy",
    "보통": "normal",
    "어려움": "hard",
    "medium": "normal",  # 혼동 방지용
}
def _norm_difficulty(v: str) -> str:
    v = (v or "normal").strip().lower()
    return _DIFFICULTY_MAP.get(v, v)

def _norm_language(v: str) -> str:
    v = (v or "ko").strip().lower()
    return "en" if v == "en" else "ko"


# ===== Start =====
class InterviewStartIn(serializers.Serializer):
    # ✅ 필수 + 빈문자열 불가(요구사항 유지)
    jd_context = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)
    resume_context = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)

    # 선택
    research_context = serializers.CharField(required=False, allow_blank=True, default="")
    research_bias = serializers.BooleanField(required=False, default=True)
    mode = serializers.ChoiceField(
        choices=["온디맨드", "프리플랜", "혼합형(추천)"],
        required=False,
        default="혼합형(추천)",
    )

    difficulty = serializers.ChoiceField(
        choices=["easy", "normal", "hard", "medium", "쉬움", "보통", "어려움"],
        required=False,
        default="normal",
    )
    language = serializers.ChoiceField(choices=["ko", "en"], required=False, default="ko")

    meta = MetaIn(required=False, default=dict)
    ncs_query = serializers.CharField(required=False, allow_blank=True, default="")

    # ---- 방어적 검증(길이/트림/정규화) ----
    def validate_jd_context(self, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 20:
            raise serializers.ValidationError("JD 텍스트는 최소 20자 이상 입력하세요.")
        return v

    def validate_resume_context(self, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 20:
            raise serializers.ValidationError("이력서/경험 요약은 최소 20자 이상 입력하세요.")
        return v

    def validate_difficulty(self, v: str) -> str:
        return _norm_difficulty(v)

    def validate_language(self, v: str) -> str:
        return _norm_language(v)


class InterviewStartOut(serializers.Serializer):
    message = serializers.CharField()
    question = serializers.CharField()
    session_id = serializers.UUIDField()
    turn_index = serializers.IntegerField()
    # NCS 컨텍스트 포함 (뷰에서 session.context 반환)
    context = serializers.JSONField(required=False)
    # 🔹 프런트 동기화를 위해 노출(뷰에서 이미 세션에 저장함)
    language = serializers.CharField(required=False, default="ko")
    difficulty = serializers.CharField(required=False, default="normal")


# ===== Next (세션 기반 + 구버전 호환) =====
class InterviewNextIn(serializers.Serializer):
    # 세션 기반 권장
    session_id = serializers.UUIDField(required=False)

    # 구버전 호환 필드
    context = serializers.CharField(required=False, allow_blank=True, default="")
    prev_questions = serializers.ListField(child=serializers.CharField(), required=False, default=list)

    # 한/영 난이도 + language
    difficulty = serializers.ChoiceField(
        choices=["easy", "normal", "hard", "medium", "쉬움", "보통", "어려움"],
        required=False,
        default="normal",
    )
    language = serializers.ChoiceField(choices=["ko", "en"], required=False, default="ko")

    meta = serializers.DictField(required=False, default=dict)
    ncs_query = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_difficulty(self, v: str) -> str:
        return _norm_difficulty(v)

    def validate_language(self, v: str) -> str:
        return _norm_language(v)


class InterviewNextOut(serializers.Serializer):
    # 변경: 메인질문이 아닌 꼬리질문 세트 반환
    followups = serializers.ListField(child=serializers.CharField())
    session_id = serializers.UUIDField(required=False)
    turn_index = serializers.IntegerField(required=False)


# ===== Answer =====
class InterviewAnswerIn(serializers.Serializer):
    # 세션 기반
    session_id = serializers.UUIDField(required=False)
    turn_index = serializers.IntegerField(required=False)  # 직전 interviewer 질문 index

    # 구버전 호환 필드
    context = serializers.CharField(required=False, allow_blank=True, default="")
    meta = serializers.DictField(required=False, default=dict)

    # 공통
    question = serializers.CharField(required=False, allow_blank=True, default="")
    answer = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)

    # 언어/난이도
    language = serializers.ChoiceField(choices=["ko", "en"], required=False, default="ko")
    difficulty = serializers.ChoiceField(
        choices=["easy", "normal", "hard", "medium", "쉬움", "보통", "어려움"],
        required=False,
        default="normal",
    )

    def validate_answer(self, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 5:
            raise serializers.ValidationError("답변은 최소 5자 이상 입력하세요.")
        return v

    def validate_difficulty(self, v: str) -> str:
        return _norm_difficulty(v)

    def validate_language(self, v: str) -> str:
        return _norm_language(v)


class InterviewAnswerOut(serializers.Serializer):
    ok = serializers.BooleanField()
    session_id = serializers.UUIDField(required=False)
    turn_index = serializers.IntegerField(required=False)
    feedback = serializers.CharField(required=False, allow_blank=True)
    # ✅ 점수는 nullable 허용
    scores = serializers.DictField(required=False, allow_null=True)
    tips = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    # ✅ 상태 노출
    scoring_status = serializers.ChoiceField(
        choices=["ok", "pending", "failed"], required=False, default="ok"
    )
    scoring_error = serializers.CharField(required=False, allow_blank=True, default="")



# ===== Finish =====
class InterviewFinishIn(serializers.Serializer):
    session_id = serializers.UUIDField(required=False)  # 세션 기반 권장
    # 구버전 호환
    context = serializers.CharField(required=False, allow_blank=True, default="")
    meta = serializers.DictField(required=False, default=dict)


class InterviewFinishOut(serializers.Serializer):
    report_id = serializers.CharField()
    status = serializers.CharField()
