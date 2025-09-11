# ares/api/config.py
import os

# =============================================================================
# ARES RAG/INTERVIEW SETTINGS
# =============================================================================

# ------------------------------------------------------------------------------
# AI (Azure OpenAI)
# ------------------------------------------------------------------------------
# AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY 등은 .env 파일에서 관리
AI_CONFIG = {
    "CHAT_DEPLOYMENT": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o-mini"),
    "EMBED_DEPLOYMENT": (
        os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
        or os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT_NAME")
        or "text-embedding-3-small"
    ),
    "EMBED_DIMENSIONS": int(os.getenv("NCS_EMBED_DIM", "1536")),
    "API_VERSION": os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
}

# ------------------------------------------------------------------------------
# Search (Azure Cognitive Search)
# ------------------------------------------------------------------------------
# AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY 등은 .env 파일에서 관리
SEARCH_CONFIG = {
    "NCS_INDEX": os.getenv("NCS_INDEX", "ncs-index"),
    "NCS_VECTOR_FIELD": os.getenv("NCS_VECTOR_FIELD", "content_vector"),
    "NCS_FALLBACK_QUERY": "핵심 직무능력 기준",
}

# ------------------------------------------------------------------------------
# Interview Service
# ------------------------------------------------------------------------------
INTERVIEW_CONFIG = {
    "TEMPERATURE_OUTLINE": 0.4,
    "TEMPERATURE_MAIN": 0.5,
    "TEMPERATURE_FOLLOW": 0.3,
    "TEMPERATURE_SCORE": 0.2,
    "MAX_TOKENS_OUTLINE": 220,
    "MAX_TOKENS_MAIN": 160,
    "MAX_TOKENS_FOLLOW": 260,
    "MAX_TOKENS_SCORE": 520,
    "CONTEXT_MAX_CHARS": 10000,
    "ANSWER_MAX_CHARS": 6000,
    "NCS_TOP_OUTLINE": 6,
    "NCS_TOP_MAIN": 6,
    "NCS_TOP_FOLLOW": 4,
    "NCS_TOP_SCORE": 4,
    "NCS_CTX_MAX_LEN": 1800,
    "MAX_FOLLOW_K": 8,
    "DEBUG_LOG_PROMPTS": os.getenv("ARES_DEBUG_PROMPTS", "0") == "1",
}

# ------------------------------------------------------------------------------
# System Prompts (Template candidates)
# ------------------------------------------------------------------------------
PROMPTS = {
    "SYS_OUTLINE": (
        "너는 Fortune 500 제조·IT 기업의 시니어 면접관이다. "
        "컨텍스트를 바탕으로 면접 '섹션 아웃라인'만 작성한다. "
        "규칙: (1) 불릿/번호 금지 (2) 한 줄에 하나 (3) 8~24자 (4) 중복·유사 금지. "
        "제조/설비/반도체 컨텍스트면 OEE, TPM, MTBF/MTTR, FDC/예지보전 고려."
    ),
    "SYS_MAIN_Q": (
        "너는 대기업 기술직 면접관이다. 새로운 주제의 '메인 질문' 1개만 작성한다. "
        "제약: (1) 이미 한 질문과 중복 금지 (2) 한국어 한 문장 (3) 끝은 물음표 (4) 70자 이내. "
        "난이도: 쉬움=경험 개요, 보통=역할·결과 수치, 어려움=가설/리스크/사후학습. "
        "제조/설비/반도체면 OEE/TPM/MTBF/MTTR/불량률/가동률·FDC/예지보전 지표 고려."
    ),
    "SYS_FOLLOW": (
        "너는 집요한 시니어 면접관이다. 메인 질문·답변을 바탕으로 '파고드는 꼬리질문' k개를 만든다. "
        "카테고리 분산: [지표/수치], [본인역할/의사결정], [리스크/대안], [협업/갈등], [학습/회고]. "
        "규칙: (1) 한국어 한 문장 (2) 60자 이내 (3) 중복 금지 (4) '수치/기간/범위' 포함 시도. "
        "금지어: '열심히', '많이', '최대한', '중요했다'."
    ),
    "SYS_STARC": (
        "너는 시니어 면접관이다. STAR-C(상황·과제·행동·결과·성찰)로 평가한다. "
        "JSON만 출력. 다른 텍스트 금지.\n"
        '{ \"scores\":{\"S\":0-5,\"T\":0-5,\"A\":0-5,\"R\":0-5,\"C\":0-5}, ' 
        '\"weighted_total\":number, \"grade\":\"A|B|C|D\", ' 
        '\"comments\":{\"S\":\"\",\"T\":\"\",\"A\":\"\",\"R\":\"\",\"C\":\"\"}, ' 
        '\"summary\":[\"- 강점 ...\", '\
        "- 보완점 ...\",\n- 추가 제안 ...\"] }\n"
        "A≥22.5, B≥18.0, C≥13.0, else D."
    ),
}
