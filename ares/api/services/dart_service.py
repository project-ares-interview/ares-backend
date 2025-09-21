import os
import requests
import zipfile
import io
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

class DartService:
    """
    DART API와 통신하여 기업 정보를 가져오는 서비스 클래스
    """
    BASE_URL = "https://opendart.fss.or.kr/api"

    def __init__(self):
        self.api_key = os.getenv("OPENDART_API_KEY")
        if not self.api_key:
            raise ValueError("OPENDART_API_KEY가 설정되지 않았습니다.")
        self.corp_codes = self._load_corp_codes()

    def _load_corp_codes(self) -> Dict[str, Any]:
        """
        DART에서 제공하는 기업 고유번호 zip 파일을 다운로드하고 압축을 해제하여
        기업 이름과 코드를 매핑하는 딕셔너리를 생성합니다.
        상장된 회사만 대상으로 합니다.
        """
        url = f"{self.BASE_URL}/corpCode.xml"
        params = {'crtfc_key': self.api_key}
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                with zf.open('CORPCODE.xml') as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    corp_code_map = {}
                    for item in root.findall('.//list'):
                        corp_name = item.find('corp_name').text
                        corp_code = item.find('corp_code').text
                        stock_code = item.find('stock_code').text
                        # 주식 코드가 있는 상장사만 대상으로 함
                        if stock_code and stock_code.strip():
                            corp_code_map[corp_name] = corp_code
                    return corp_code_map
        except requests.exceptions.RequestException as e:
            print(f"DART 기업 코드 다운로드 실패: {e}")
            return {}
        except Exception as e:
            print(f"기업 코드 처리 중 오류 발생: {e}")
            return {}

    def get_corp_code(self, corp_name: str) -> Optional[str]:
        """
        기업 이름으로 기업 코드를 조회합니다.
        """
        return self.corp_codes.get(corp_name)

    def get_latest_business_report_info(self, corp_code: str) -> Optional[Dict[str, Any]]:
        """
        기업 코드를 사용하여 가장 최근의 사업보고서 정보를 가져옵니다.
        (정기공시, 사업보고서(A001) 기준)
        """
        url = f"{self.BASE_URL}/list.json"
        end_date = datetime.now()
        start_date = end_date - timedelta(days=5*365) # 최근 5년치 검색
        
        params = {
            'crtfc_key': self.api_key,
            'corp_code': corp_code,
            'bgn_de': start_date.strftime('%Y%m%d'),
            'end_de': end_date.strftime('%Y%m%d'),
            'pblntf_ty': 'A',       # 정기공시
            'pblntf_detail_ty': 'A001', # 사업보고서
            'page_no': 1,
            'page_count': 10, # 최신순으로 정렬되므로 10개면 충분
        }
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == '000' and data.get('list'):
                # 가장 최근 (첫 번째) 보고서 반환
                return data['list'][0]
            elif data.get('status') == '013':
                print(f"{corp_code}: 검색된 데이터가 없습니다.")
                return None
            else:
                print(f"API 오류: {data.get('message')}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"최신 사업보고서 조회 실패: {e}")
            return None

    def download_document(self, rcept_no: str) -> Optional[bytes]:
        """
        접수번호(rcept_no)에 해당하는 공시 서류 원본 파일(XML)을 다운로드합니다.
        """
        url = f"{self.BASE_URL}/document.xml"
        params = {'crtfc_key': self.api_key, 'rcept_no': rcept_no}
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            
            # 응답이 zip 파일이므로 압축 해제 후 첫 번째 파일(보고서 본문)을 반환
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                file_list = zf.namelist()
                if file_list:
                    # 보통 zip안에 xml이나 xhtml 파일이 하나 들어있음
                    return zf.read(file_list[0])
            return None
        except requests.exceptions.RequestException as e:
            print(f"문서 다운로드 실패: {e}")
            return None

    def download_document_as_text(self, rcept_no: str) -> Optional[str]:
        """
        접수번호(rcept_no)에 해당하는 공시 서류를 다운로드하여
        내용에서 텍스트만 추출하여 반환합니다.
        """
        try:
            document_bytes = self.download_document(rcept_no)
            if document_bytes:
                # 다운로드한 바이트를 파싱하여 텍스트 추출
                soup = BeautifulSoup(document_bytes, 'lxml')
                return soup.get_text(separator='\n', strip=True)
            return None
        except Exception as e:
            print(f"문서 텍스트 변환 중 오류 발생: {e}")
            return None

# 사용 예시 (테스트용)
if __name__ == "__main__":
    # 로컬 테스트를 위해 .env 파일 로드 (python-dotenv 필요)
    try:
        from dotenv import load_dotenv
        # 프로젝트 루트의 .env.development 파일을 로드하도록 경로 수정
        dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env.development')
        load_dotenv(dotenv_path=dotenv_path)
    except ImportError:
        print("python-dotenv가 설치되지 않아 .env 파일을 로드할 수 없습니다.")
        print("테스트를 위해서는 OPENDART_API_KEY 환경변수를 직접 설정해야 합니다.")

    dart_service = DartService()
    
    # 예시: 삼성전자
    corp_name = "삼성전자"
    corp_code = dart_service.get_corp_code(corp_name)
    
    if corp_code:
        print(f"'{corp_name}'의 기업 코드: {corp_code}")
        report_info = dart_service.get_latest_business_report_info(corp_code)
        if report_info:
            print(f"최신 사업보고서 정보: {report_info}")
            rcept_no = report_info.get("rcept_no")
            if rcept_no:
                # 텍스트 추출 함수를 호출
                document_content = dart_service.download_document_as_text(rcept_no)
                if document_content:
                    # 다운로드된 텍스트를 파일로 저장
                    file_name = f"{rcept_no}.txt"
                    with open(file_name, "w", encoding="utf-8") as f:
                        f.write(document_content)
                    print(f"'{file_name}' 파일이 저장되었습니다.")
    else:
        print(f"'{corp_name}'에 해당하는 기업 코드를 찾을 수 없습니다.")

