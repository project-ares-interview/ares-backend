import pandas as pd
from functools import lru_cache
from .blob_storage import BlobStorage

def _load_company_dataframe() -> pd.DataFrame:
    """Azure Blob Storage 또는 로컬에서 회사 데이터를 로드합니다."""
    try:
        blob_storage = BlobStorage()
        df = blob_storage.read_csv('companies_updated.csv')
        print("✅ Blob Storage에서 회사 데이터를 성공적으로 로드했습니다.")
        return df
    except Exception as e:
        print(f"🚨 Blob Storage 로드 실패: {e}. 로컬 CSV를 시도합니다.")
        try:
            return pd.read_csv('data/companies_updated.csv')
        except Exception as e2:
            print(f"🚨 모든 데이터 로드 실패: {e2}.")
            return pd.DataFrame()

# 모듈이 임포트될 때 데이터를 한 번만 로드하여 캐싱합니다.
COMPANY_DF = _load_company_dataframe()

def get_company_dataframe() -> pd.DataFrame:
    """캐시된 회사 데이터프레임을 반환합니다."""
    return COMPANY_DF

def find_affiliates_by_keyword(keyword: str) -> list:
    """
    캐시된 DataFrame에서 키워드로 회사 이름을 검색합니다.
    """
    df = get_company_dataframe()
    if df.empty or 'company_name' not in df.columns or not keyword:
        return []
    
    matching_df = df[df['company_name'].str.contains(keyword, case=False, na=False)]
    return matching_df['company_name'].tolist()

def get_company_description(company_name: str) -> str:
    """
    캐시된 DataFrame에서 회사 이름으로 상세 설명을 찾습니다.
    """
    df = get_company_dataframe()
    if df.empty or company_name not in df['company_name'].values:
        return "인재상 정보 없음"
        
    company_info = df[df['company_name'] == company_name].iloc[0]
    return company_info.get('detailed_description', '인재상 정보 없음')

def get_company_dart_name_map() -> dict:
    """
    회사 이름과 DART API에서 사용하는 공식 명칭을 매핑하는 딕셔너리를 반환합니다.
    """
    df = get_company_dataframe()
    if df.empty or 'company_name' not in df.columns:
        return {}
    
    # 'dart_name'이 없는 경우 'company_name'을 사용
    df['dart_name'] = df.get('dart_name', pd.Series(df['company_name'], index=df.index))
    df['dart_name'] = df['dart_name'].fillna(df['company_name'])
    
    return pd.Series(df.dart_name.values, index=df.company_name).to_dict()
