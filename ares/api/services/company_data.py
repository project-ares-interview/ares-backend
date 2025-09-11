import pandas as pd
from functools import lru_cache
from .blob_storage import BlobStorage


@lru_cache(maxsize=1)
def get_company_dataframe() -> pd.DataFrame:
    """Azure Blob Storage 또는 로컬에서 회사 데이터를 로드하고 캐싱합니다."""
    try:
        blob_storage = BlobStorage()
        df = blob_storage.read_csv('companies_updated.csv')
        print("✅ Blob Storage에서 회사 데이터를 성공적으로 로드했습니다.")
        return df
    except Exception as e:
        print(f"🚨 Blob Storage 로드 실패: {e}. 로컬 CSV를 시도합니다.")
        try:
            # Django 프로젝트의 루트 경로를 기준으로 파일을 찾도록 경로를 설정할 수 있습니다.
            # 여기서는 우선 기존과 동일하게 상대 경로를 사용합니다.
            return pd.read_csv('companies_updated.csv')
        except Exception as e2:
            print(f"🚨 모든 데이터 로드 실패: {e2}.")
            return pd.DataFrame()


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
