from azure.storage.blob import BlobServiceClient
from django.conf import settings
from io import BytesIO
import pandas as pd
import os


class BlobStorage:
    def __init__(self):
        self.conn_str = getattr(settings, "AZURE_STORAGE_CONN_STR", None)
        self.container_name = getattr(settings, "AZURE_STORAGE_CONTAINER_NAME", None)

        if not self.conn_str:
            raise ValueError("AZURE_STORAGE_CONN_STR 가 Django 설정에 없습니다.")
        if not self.container_name:
            raise ValueError("AZURE_STORAGE_CONTAINER_NAME 가 Django 설정에 없습니다.")

        self.service_client = BlobServiceClient.from_connection_string(self.conn_str)

    def read_excel(self, blob_name: str, engine: str | None = None) -> pd.DataFrame:
        """Blob에 있는 Excel 파일을 pandas DataFrame으로 변환"""
        blob_client = self.service_client.get_blob_client(
            container=self.container_name, blob=blob_name
        )
        data = blob_client.download_blob().readall()
        return pd.read_excel(BytesIO(data), engine=engine)

    def read_csv(self, blob_name: str, encoding: str = "utf-8") -> pd.DataFrame:
        """Blob에 있는 CSV 파일을 pandas DataFrame으로 변환"""
        blob_client = self.service_client.get_blob_client(
            container=self.container_name, blob=blob_name
        )
        data = blob_client.download_blob().readall()
        try:
            return pd.read_csv(BytesIO(data), encoding=encoding)
        except UnicodeDecodeError:
            return pd.read_csv(BytesIO(data), encoding="cp949")

    def list_blobs(self, prefix: str = "") -> list[str]:
        """컨테이너 내 파일 경로 확인용(디버그)"""
        container = self.service_client.get_container_client(self.container_name)
        return [b.name for b in container.list_blobs(name_starts_with=prefix)]
