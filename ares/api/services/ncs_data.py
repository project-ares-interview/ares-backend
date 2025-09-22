# ares/api/services/ncs_data.py
import pandas as pd
from functools import lru_cache
import os

def _load_ncs_dataframe() -> pd.DataFrame:
    """로컬에서 NCS 중분류 데이터를 로드합니다."""
    try:
        # Django 프로젝트의 루트 경로를 기준으로 파일을 찾습니다.
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        file_path = os.path.join(base_dir, 'data', 'NCS_middle.csv')
        df = pd.read_csv(file_path)
        # 코드들을 문자열로 통일하여 앞의 0이 사라지는 문제 방지
        df['대분류코드'] = df['대분류코드'].astype(str).str.zfill(2)
        df['중분류코드'] = df['중분류코드'].astype(str).str.zfill(2)
        return df
    except Exception as e:
        print(f"🚨 NCS 데이터 로드 실패: {e}.")
        return pd.DataFrame()

NCS_DF = _load_ncs_dataframe()

@lru_cache(maxsize=1)
def get_ncs_categories() -> list[str]:
    """JD 분류에 사용할 '대분류명-중분류명' 문자열 리스트를 반환합니다."""
    if NCS_DF.empty:
        return []
    
    # 중복을 제거한 '대분류코드명'과 '중분류코드명'을 합쳐 리스트 생성
    categories = NCS_DF[['대분류코드명', '중분류코드명']].drop_duplicates()
    return [f"{row['대분류코드명']}-{row['중분류코드명']}" for _, row in categories.iterrows()]

def get_ncs_codes(category_name: str) -> dict[str, str] | None:
    """'대분류명-중분류명' 문자열로부터 코드들을 찾아서 반환합니다."""
    if NCS_DF.empty or '-' not in category_name:
        return None
    
    parts = category_name.split('-', 1)
    major_name, middle_name = parts[0].strip(), parts[1].strip()
    
    result = NCS_DF[(NCS_DF['대분류코드명'] == major_name) & (NCS_DF['중분류코드명'] == middle_name)]
    
    if not result.empty:
        return {
            "major_code": result.iloc[0]['대분류코드'],
            "middle_code": result.iloc[0]['중분류코드']
        }
    return None
