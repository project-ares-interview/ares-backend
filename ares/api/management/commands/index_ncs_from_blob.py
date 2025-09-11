# ares/api/management/commands/index_ncs_from_blob.py
from __future__ import annotations
import os, io, gzip, json, time, uuid, re, hashlib
from typing import Iterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient, ContainerClient

from ares.api.config import SEARCH_CONFIG, AI_CONFIG
from ares.api.utils.ai_utils import embed as embed_func

# =========================
# Command Class
# =========================
class Command(BaseCommand):
    help = "Indexes NCS data from Azure Blob Storage into Azure Cognitive Search."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="The number of documents to upload in a single batch."
        )
        parser.add_argument(
            "--container-name",
            type=str,
            default=os.getenv("NCS_CONTAINER", "ncs"),
            help="Name of the Azure Blob Storage container."
        )
        parser.add_argument(
            "--shards-prefix",
            type=str,
            default=os.getenv("NCS_SHARDS_PREFIX", "7ai-fianl-team4-ncs/jsonl/shards"),
            help="Prefix of the shard files within the container."
        )

    def handle(self, *args, **options):
        # 1. 환경 변수 및 설정 로드
        self.batch_size = options["batch_size"]
        self.container_name = options["container_name"]
        self.shards_prefix = options["shards_prefix"]

        self._assert_env()

        # 2. 클라이언트 초기화
        search_client = self._get_search_client()
        _, container_client, prefix = self._build_blob_clients()

        # 3. 인덱싱 실행
        self.stdout.write("Starting NCS data indexing...")
        total_docs = self._run_indexing(search_client, container_client, prefix)
        self.stdout.write(self.style.SUCCESS(f"✅ Indexing finished. Total documents processed: {total_docs}"))

    def _assert_env(self):
        missing = []
        env_vars = {
            "AZURE_SEARCH_ENDPOINT": os.getenv("AZURE_SEARCH_ENDPOINT"),
            "AZURE_SEARCH_KEY": os.getenv("AZURE_SEARCH_KEY"),
            "STORAGE_ACCOUNT_URL": os.getenv("STORAGE_ACCOUNT_URL"),
            "STORAGE_SAS or STORAGE_KEY": os.getenv("STORAGE_SAS") or os.getenv("STORAGE_KEY"),
            "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT"),
            "AZURE_OPENAI_API_KEY": os.getenv("AZURE_OPENAI_API_KEY"),
        }
        for key, value in env_vars.items():
            if not (value or "").strip():
                missing.append(key)
        if missing:
            raise CommandError("Missing required environment variables: " + ", ".join(missing))

    def _get_search_client(self) -> SearchClient:
        return SearchClient(
            endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
            index_name=SEARCH_CONFIG["NCS_INDEX"],
            credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_KEY"))
        )

    def _build_blob_clients(self) -> Tuple[str, ContainerClient, str]:
        account_url = os.getenv("STORAGE_ACCOUNT_URL", "")
        account_key = os.getenv("STORAGE_SAS") or os.getenv("STORAGE_KEY")
        
        root = account_url if account_url.startswith(("http://", "https://")) else f"https://{account_url}"
        parsed = urlparse(root)
        if not parsed.netloc or "blob.core.windows.net" not in parsed.netloc:
            raise CommandError("STORAGE_ACCOUNT_URL is not a valid Blob Storage account URL.")
        account_root = f"{parsed.scheme}://{parsed.netloc}"

        bsc = BlobServiceClient(account_url=account_root, credential=account_key)
        cc = bsc.get_container_client(self.container_name)
        prefix = self._normalize_prefix(self.shards_prefix)
        
        self.stdout.write(f"[Blob] Account Root: {account_root}, Container: {self.container_name}")
        self.stdout.write(f"[Blob] Shards Prefix: {prefix or '(root)'}")
        return account_root, cc, prefix

    def _normalize_prefix(self, pfx: str) -> str:
        if not pfx: return ""
        p = pfx.strip().lstrip("/")
        return p if p.endswith("/") else p + "/"

    def _run_indexing(self, sc: SearchClient, cc: ContainerClient, prefix: str) -> int:
        total = 0
        batch: List[Dict] = []
        shard_idx = 0

        try:
            for blob_name in self._iter_shard_blob_names(cc, prefix):
                shard_idx += 1
                self.stdout.write(f">> Processing shard [{shard_idx}]: {blob_name}")

                try:
                    props = cc.get_blob_client(blob_name).get_blob_properties()
                    updated_at = props.last_modified.isoformat() if getattr(props, "last_modified", None) else None
                except Exception:
                    updated_at = None

                for raw_doc in self._read_jsonl_stream(cc, blob_name):
                    try:
                        transformed_doc = self._transform_and_embed(raw_doc, updated_at)
                        batch.append(transformed_doc)
                    except Exception as e:
                        self.stderr.write(self.style.WARNING(f"  Transform/embed error (skipping doc): {e}"))
                        continue

                    if len(batch) >= self.batch_size:
                        self._upload_batch_with_retry(sc, batch)
                        total += len(batch)
                        self.stdout.write(f"  Uploaded: {total} documents")
                        batch = []

            if batch:
                self._upload_batch_with_retry(sc, batch)
                total += len(batch)
                self.stdout.write(f"  Uploaded (final batch): {total} documents")

            if shard_idx == 0:
                self.stderr.write(self.style.WARNING("No shard files found. Check container and prefix."))

            return total

        except ResourceNotFoundError:
            raise CommandError(f"Container not found: {self.container_name}")
        except Exception as e:
            raise CommandError(f"An unexpected error occurred during indexing: {e}")

    def _upload_batch_with_retry(self, sc: SearchClient, docs: List[Dict]):
        for attempt in range(1, 4):
            try:
                self._upload_batch(sc, docs)
                return
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"  Upload error: {e} (attempt {attempt}/3)"))
                time.sleep(1.5 * attempt)
        self.stderr.write(self.style.ERROR(f"Failed to upload batch after multiple retries."))

    def _upload_batch(self, sc: SearchClient, docs: List[Dict]):
        if not docs: return
        res = sc.upload_documents(documents=docs)
        fails = [r for r in res if not getattr(r, "succeeded", False)]
        if fails:
            for r in fails[:5]:
                self.stderr.write(self.style.WARNING(f"  Upload failed for key={getattr(r, "key", None)}, error={getattr(r, "error_message", None)}"))
            raise RuntimeError(f"Upload failed for {len(fails)} documents in the batch.")

    def _iter_shard_blob_names(self, cc: ContainerClient, prefix: str) -> Iterator[str]:
        # ... (logic is the same as original script)
        found = 0
        for b in cc.list_blobs(name_starts_with=prefix):
            name = b.name
            if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
                found += 1
                if found <= 8:
                    self.stdout.write(f"  - Found shard: {name}")
                elif found == 9:
                    self.stdout.write("  - ... (more shards found)")
                yield name
        if found == 0:
            self.stderr.write(self.style.WARNING("No shard files found. Check prefix/extension (.jsonl|.jsonl.gz)."))

    def _read_jsonl_stream(self, cc: ContainerClient, blob_name: str) -> Iterator[Dict]:
        # ... (logic is the same as original script)
        is_gz = blob_name.lower().endswith(".gz")
        downloader = cc.download_blob(blob_name)
        if is_gz:
            data = downloader.readall()
            with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
                for line in gz.read().decode("utf-8", errors="ignore").splitlines():
                    if s := line.strip(): yield json.loads(s)
        else:
            buf = ""
            for chunk in downloader.chunks():
                buf += chunk.decode("utf-8", errors="ignore")
                while (pos := buf.find("\n")) != -1:
                    line = buf[:pos].strip()
                    buf = buf[pos + 1:]
                    if line: yield json.loads(line)
            if buf.strip(): yield json.loads(buf.strip())

    def _transform_and_embed(self, doc: Dict, updated_at: Optional[str]) -> Dict:
        # ... (logic is mostly the same, but uses embed_func)
        cls = doc.get("classification", {}) or {}
        au = doc.get("ability_unit", {}) or {}
        el = doc.get("element", {}) or {}
        criteria = doc.get("criteria", []) or []
        ksas = doc.get("ksas", []) or []
        ksa = doc.get("ksa", {}) or {}

        criteria_text = " ".join([str(c.get("text","")).strip() for c in criteria if str(c.get("text","")).strip()])

        know_list, skill_list, att_list = [], [], []
        # ... (KSA logic is the same)
        if ksas:
            for k in ksas:
                typ = (k.get("type") or "").strip()
                val = (k.get("meaning") or "").strip()
                if not val: continue
                if "지식" in typ: know_list.append(val)
                elif "기술" in typ: skill_list.append(val)
                elif "태도" in typ: att_list.append(val)
        else:
            if ksa.get("meaning"): know_list.append(ksa.get("meaning"))

        knowledge = " ".join(know_list).strip()
        skills = " ".join(skill_list).strip()
        attitudes = " ".join(att_list).strip()

        pieces = [
            au.get("name",""), el.get("name",""), criteria_text, knowledge, skills, attitudes,
            self._safe_get(cls, "major", "name", default=""),
            self._safe_get(cls, "middle","name", default=""),
            self._safe_get(cls, "minor", "name", default=""),
            self._safe_get(cls, "detail","name", default=""),
        ]
        content_concat = " | ".join([p for p in pieces if p]).strip() or (au.get("name") or el.get("name") or "NCS").strip()

        # Use the refactored embed function
        content_vector = embed_func(content_concat)

        raw_id = doc.get("doc_id") or doc.get("id") or doc.get("uuid") or str(uuid.uuid4())
        did = self._sanitize_key(raw_id)

        flat = {
            "doc_id": did,
            "major_code":  self._safe_get(cls, "major", "code", default=""),
            "middle_code": self._safe_get(cls, "middle","code", default=""),
            "minor_code":  self._safe_get(cls, "minor", "code", default=""),
            "detail_code": self._safe_get(cls, "detail","code", default=""),
            "ability_code":  au.get("code",""),
            "ability_name":  au.get("name",""),
            "ability_level": str(au.get("level") or ""),
            "element_code":  el.get("code",""),
            "element_name":  el.get("name",""),
            "criteria_text": criteria_text,
            "knowledge":    knowledge,
            "skills":       skills,
            "attitudes":    attitudes,
            "content_concat": content_concat,
            SEARCH_CONFIG["NCS_VECTOR_FIELD"]:
            content_vector,
            "source":     doc.get("source","NCS_2025_v1"),
            "updated_at": updated_at or datetime.utcnow().isoformat(),
        }
        return {k: v for k, v in flat.items() if v is not None and v != ""}

    def _safe_get(self, d: Dict, *path, default: str = "") -> str:
        # ... (logic is the same)
        cur = d
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur if cur is not None else default

    def _sanitize_key(self, s: str, max_len: int = 512) -> str:
        # ... (logic is the same)
        if not s: return hashlib.sha1(b"ncs-empty").hexdigest()
        out = re.sub(r"[^A-Za-z0-9_\-=]+", "-", str(s))
        out = re.sub(r"-{2,}", "-", out).strip("-")
        if not out or len(out) > max_len:
            h = hashlib.sha1(str(s).encode("utf-8")).hexdigest()
            out = f"id-{h}"
        return out
