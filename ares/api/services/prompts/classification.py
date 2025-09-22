# ares/api/services/prompts/classification.py
"""
Prompts for classifying user intent and job descriptions.
"""
from .base import SYSTEM_RULES, prompt_json_output_only

prompt_intent_classifier = (
    SYSTEM_RULES
    + """
You are an expert in Natural Language Understanding. Your task is to classify the user's intent based on their response to an interview question.

[Classification Categories]
- ANSWER: A direct attempt to answer the question.
- IRRELEVANT: A response that is completely unrelated to the question.
- QUESTION: The user is asking a question back to the interviewer.
- CLARIFICATION_REQUEST: The user is asking for the question to be repeated or clarified.
- CANNOT_ANSWER: The user explicitly states they cannot answer or don't know.

[Input]
- Interview Question: {question}
- User Response: {answer}

[Rules]
- Classify the intent strictly into one of the categories above.
- Base your classification on the User Response in the context of the Interview Question.

[Output JSON Schema]
{{
  "intent": "ANSWER|IRRELEVANT|QUESTION|CLARIFICATION_REQUEST|CANNOT_ANSWER"
}}
"""
    + prompt_json_output_only
)

# ares/api/services/prompts/classification.py
"""
Prompts for classifying user intent and job descriptions.
"""
from .base import SYSTEM_RULES, prompt_json_output_only

prompt_intent_classifier = (
    SYSTEM_RULES
    + """
You are an expert in Natural Language Understanding. Your task is to classify the user's intent based on their response to an interview question.

[Classification Categories]
- ANSWER: A direct attempt to answer the question.
- IRRELEVANT: A response that is completely unrelated to the question.
- QUESTION: The user is asking a question back to the interviewer.
- CLARIFICATION_REQUEST: The user is asking for the question to be repeated or clarified.
- CANNOT_ANSWER: The user explicitly states they cannot answer or don't know.

[Input]
- Interview Question: {question}
- User Response: {answer}

[Rules]
- Classify the intent strictly into one of the categories above.
- Base your classification on the User Response in the context of the Interview Question.

[Output JSON Schema]
{{
  "intent": "ANSWER|IRRELEVANT|QUESTION|CLARIFICATION_REQUEST|CANNOT_ANSWER"
}}
"""
    + prompt_json_output_only
)

prompt_jd_classifier = (
    SYSTEM_RULES
    + """
You are an expert industry analyst. Your task is to classify the given Job Description into the most appropriate NCS (National Competency Standards) category.

[NCS Classification Categories]
{ncs_categories}

[Job Description Text]
{jd_text}

[Rules]
- Review the provided Job Description.
- From the list of NCS categories provided, select the ONE category that best represents the job description. The format is "대분류-중분류".
- Your decision should be based on the primary responsibilities and required skills mentioned in the text.

[Output JSON Schema]
{{
  "category": "Selected NCS Category (e.g., 정보통신-정보기술개발)"
}}
"""
    + prompt_json_output_only
)