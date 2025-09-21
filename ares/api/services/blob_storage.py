from azure.storage.blob import BlobServiceClient, ContentSettings
from io import BytesIO
import pandas as pd
import os


class BlobStorage:
    def __init__(self):
        self.conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        self.container_name = os.getenv("AZURE_BLOB_CONTAINER", "interview-data")

        if not self.conn_str:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING 환경 변수가 설정되지 않았습니다.")
        if not self.container_name:
            raise ValueError("AZURE_BLOB_CONTAINER 환경 변수가 설정되지 않았습니다.")

        self.service_client = BlobServiceClient.from_connection_string(self.conn_str)
        self.container_client = self.service_client.get_container_client(self.container_name)

    def read_excel(self, blob_name: str, engine: str | None = None) -> pd.DataFrame:
        """Blob에 있는 Excel 파일을 pandas DataFrame으로 변환"""
        blob_client = self.container_client.get_blob_client(blob=blob_name)
        data = blob_client.download_blob().readall()
        return pd.read_excel(BytesIO(data), engine=engine)

    def read_csv(self, blob_name: str, encoding: str = "utf-8") -> pd.DataFrame:
        """Blob에 있는 CSV 파일을 pandas DataFrame으로 변환"""
        blob_client = self.container_client.get_blob_client(blob=blob_name)
        data = blob_client.download_blob().readall()
        try:
            return pd.read_csv(BytesIO(data), encoding=encoding)
        except UnicodeDecodeError:
            return pd.read_csv(BytesIO(data), encoding="cp949")

    def list_blobs(self, prefix: str = "") -> list[str]:
        """컨테이너 내 파일 경로 확인용(디버그)"""
        return [b.name for b in self.container_client.list_blobs(name_starts_with=prefix)]

    def blob_exists(self, blob_name: str) -> bool:
        """Blob이 존재하는지 확인"""
        blob_client = self.container_client.get_blob_client(blob=blob_name)
        return blob_client.exists()

    def upload_blob(self, blob_name: str, data: bytes, content_type: str):
        """데이터를 Blob에 업로드"""
        blob_client = self.container_client.get_blob_client(blob=blob_name)
        content_settings = ContentSettings(content_type=content_type)
        blob_client.upload_blob(data, overwrite=True, content_settings=content_settings)

    def to_prompt_dict(self, df: pd.DataFrame) -> list:
        """프롬프트에 넣기 좋은 dict 구조로 변환"""
        # 컬럼명 앞뒤 공백 제거
        df.columns = df.columns.str.strip()

        # 필요한 열만 선택
        if all(col in df.columns for col in ["company_name", "detailed_description"]):
            df = df[["company_name", "detailed_description"]]
        else:
            raise ValueError(f"필요한 컬럼이 없습니다. 현재 컬럼들: {df.columns.tolist()}")

        # NaN 값은 빈 문자열로 변환
        df = df.fillna("")

        return df.to_dict(orient="records")
