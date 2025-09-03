# ares/api/tools/index_ncs_from_blob.py
from __future__ import annotations
import os, io, gzip, json, time, uuid, re, hashlib
from typing import Iterator, Dict, List, Tuple, Optional
from urllib.parse import urlparse
from datetime import datetime

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient, ContainerClient

from openai import AzureOpenAI

# =========================
# 환경 변수 & 기본값
# =========================
SEARCH_ENDPOINT = (os.getenv("AZURE_SEARCH_ENDPOINT") or "").strip()
SEARCH_KEY      = (os.getenv("AZURE_SEARCH_KEY") or "").strip()
INDEX_NAME      = (os.getenv("NCS_INDEX") or "ncs-index").strip()

ACCOUNT_URL     = (os.getenv("STORAGE_ACCOUNT_URL") or "").strip()
ACCOUNT_KEY     = (os.getenv("STORAGE_SAS") or os.getenv("STORAGE_KEY") or "").strip()
CONTAINER_NAME  = (os.getenv("NCS_CONTAINER") or "ncs").strip()

# 주의: 컨테이너명을 포함하지 말 것. 컨테이너 내부의 경로만!
SHARDS_PREFIX   = (os.getenv("NCS_SHARDS_PREFIX") or "7ai-fianl-team4-ncs/jsonl/shards").strip()

AOAI_ENDPOINT   = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip()
AOAI_API_KEY    = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
AOAI_API_VER    = (os.getenv("AZURE_OPENAI_API_VERSION") or "2024-02-01").strip()
EMBED_MODEL     = (os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or "text-embedding-3-small").strip()

BATCH_SIZE      = int(os.getenv("NCS_INDEX_BATCH", "1000"))
MAX_EMBED_RETRY = 3
EXPECTED_DIM    = int(os.getenv("NCS_EMBED_DIM", "1536"))  # 모델 차원과 일치해야 함

# =========================
# 유틸: 환경/경로 정규화
# =========================
def _assert_env():
    missing = []
    for k, v in [
        ("AZURE_SEARCH_ENDPOINT", SEARCH_ENDPOINT),
        ("AZURE_SEARCH_KEY", SEARCH_KEY),
        ("NCS_INDEX", INDEX_NAME),
        ("STORAGE_ACCOUNT_URL", ACCOUNT_URL),
        ("STORAGE_SAS or STORAGE_KEY", ACCOUNT_KEY),
        ("AZURE_OPENAI_ENDPOINT", AOAI_ENDPOINT),
        ("AZURE_OPENAI_API_KEY", AOAI_API_KEY),
        ("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", EMBED_MODEL),
    ]:
        if not v:
            missing.append(k)
    if missing:
        raise RuntimeError("❌ 누락된 환경변수: " + ", ".join(missing))

def _normalize_prefix(pfx: str) -> str:
    if not pfx:
        return ""
    p = pfx.strip().lstrip("/")
    return p if p.endswith("/") else p + "/"

def _build_blob_clients() -> Tuple[str, ContainerClient, str]:
    # account_root 보정
    root = ACCOUNT_URL if ACCOUNT_URL.startswith(("http://", "https://")) else f"https://{ACCOUNT_URL}"
    parsed = urlparse(root)
    if not parsed.netloc or "blob.core.windows.net" not in parsed.netloc:
        raise ValueError("❌ STORAGE_ACCOUNT_URL이 올바른 Blob 계정 URL이 아닙니다.")
    account_root = f"{parsed.scheme}://{parsed.netloc}"

    bsc = BlobServiceClient(account_url=account_root, credential=ACCOUNT_KEY)
    cc  = bsc.get_container_client(CONTAINER_NAME)
    prefix = _normalize_prefix(SHARDS_PREFIX)
    print(f"[blob] account_root={account_root}  container={CONTAINER_NAME}")
    print(f"[blob] prefix={prefix or '(root)'}")
    return account_root, cc, prefix

# =========================
# 안전한 키(sanitize) 유틸
# =========================
SAFE_KEY_PATTERN = re.compile(r"[^A-Za-z0-9_\-=]+")

def sanitize_key(s: str, max_len: int = 512) -> str:
    """
    Azure Cognitive Search key 제약(A-Za-z0-9 _ - =) 충족하도록 정규화.
    - 허용문자 외는 '-'로 치환
    - 연속 '-' 축소 및 앞/뒤 '-' 제거
    - 비거나 너무 길면 sha1 해시로 대체
    """
    if not s:
        return hashlib.sha1(b"ncs-empty").hexdigest()

    out = SAFE_KEY_PATTERN.sub("-", str(s))
    out = re.sub(r"-{2,}", "-", out).strip("-")

    if not out or len(out) > max_len:
        h = hashlib.sha1(str(s).encode("utf-8")).hexdigest()
        out = f"id-{h}"

    return out

# =========================
# AOAI 임베딩
# =========================
class Embedder:
    def __init__(self):
        self.client = AzureOpenAI(
            api_key=AOAI_API_KEY,
            api_version=AOAI_API_VER,
            azure_endpoint=AOAI_ENDPOINT,
        )

    def embed(self, text: str) -> List[float]:
        s = (text or "").strip() or "NCS"
        s = s[:8000]
        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_EMBED_RETRY + 1):
            try:
                r = self.client.embeddings.create(model=EMBED_MODEL, input=s)
                vec = r.data[0].embedding
                if EXPECTED_DIM and len(vec) != EXPECTED_DIM:
                    raise ValueError(f"Embedding dimension mismatch: expected {EXPECTED_DIM}, got {len(vec)}")
                return vec
            except Exception as e:
                last_err = e
                wait = 1.2 * attempt
                print(f"  ⚠️ embed retry {attempt}/{MAX_EMBED_RETRY}: {e} (sleep {wait:.1f}s)")
                time.sleep(wait)
        # 마지막 폴백
        if last_err:
            print(f"  ❗embed fallback with short text due to: {last_err}")
        r = self.client.embeddings.create(model=EMBED_MODEL, input=(s[:256] or "NCS"))
        vec = r.data[0].embedding
        if EXPECTED_DIM and len(vec) != EXPECTED_DIM:
            raise ValueError(f"Embedding dimension mismatch on fallback: expected {EXPECTED_DIM}, got {len(vec)}")
        return vec

# =========================
# Blob → JSONL 라인 스트리밍
# =========================
def _is_gzip_head(cc: ContainerClient, blob_name: str) -> bool:
    try:
        head = cc.download_blob(blob_name, offset=0, length=2).readall()
        return head.startswith(b"\x1f\x8b")
    except Exception:
        return blob_name.lower().endswith(".gz")

def iter_shard_blob_names(cc: ContainerClient, prefix: str) -> Iterator[str]:
    found = 0
    for b in cc.list_blobs(name_starts_with=prefix):
        name = b.name
        if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
            found += 1
            if found <= 8:
                print(f"  - found: {name}")
            elif found == 9:
                print("  - ... (more)")
            yield name
    if found == 0:
        print("⚠️ shard 파일을 찾지 못했습니다. prefix/확장자(.jsonl|.jsonl.gz)를 확인하세요.")

def read_jsonl_stream(cc: ContainerClient, blob_name: str) -> Iterator[Dict]:
    is_gz = _is_gzip_head(cc, blob_name)
    downloader = cc.download_blob(blob_name)
    if is_gz:
        # 전체 읽기 (gz는 스트리밍 분해보다 전체 read 후 디코딩이 안전)
        data = downloader.readall()
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
            for line in gz.read().decode("utf-8", errors="ignore").splitlines():
                s = line.strip()
                if s:
                    yield json.loads(s)
    else:
        # 스트리밍 라인 파서
        buf = ""
        for chunk in downloader.chunks():
            buf += chunk.decode("utf-8", errors="ignore")
            while True:
                pos = buf.find("\n")
                if pos == -1:
                    break
                line = buf[:pos].strip()
                buf = buf[pos + 1:]
                if line:
                    yield json.loads(line)
        if buf.strip():
            yield json.loads(buf.strip())

# =========================
# Transform (평탄화)
# =========================
def _safe_get(d: Dict, *path, default: str = "") -> str:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default

def transform(doc: Dict, embedder: Embedder, updated_at: Optional[str]) -> Dict:
    cls = doc.get("classification", {}) or {}
    au  = doc.get("ability_unit", {}) or {}
    el  = doc.get("element", {}) or {}
    criteria = doc.get("criteria", []) or []
    ksas = doc.get("ksas", []) or []
    ksa  = doc.get("ksa", {}) or {}

    # 수행준거 합본
    criteria_text = " ".join([str(c.get("text","")).strip() for c in criteria if str(c.get("text","")).strip()])

    # KSA 수집
    know_list, skill_list, att_list = [], [], []
    if ksas:
        for k in ksas:
            typ = (k.get("type") or "").strip()
            val = (k.get("meaning") or "").strip()
            if not val:
                continue
            if "지식" in typ:
                know_list.append(val)
            elif "기술" in typ:
                skill_list.append(val)
            elif "태도" in typ:
                att_list.append(val)
    else:
        if ksa.get("meaning"):
            know_list.append(ksa.get("meaning"))

    knowledge = " ".join(know_list).strip()
    skills    = " ".join(skill_list).strip()
    attitudes = " ".join(att_list).strip()

    # content_concat
    pieces = [
        au.get("name",""), el.get("name",""), criteria_text, knowledge, skills, attitudes,
        _safe_get(cls, "major", "name", default=""),
        _safe_get(cls, "middle","name", default=""),
        _safe_get(cls, "minor", "name", default=""),
        _safe_get(cls, "detail","name", default=""),
    ]
    content_concat = " | ".join([p for p in pieces if p]).strip()
    if not content_concat:
        content_concat = (au.get("name") or el.get("name") or "NCS").strip()

    # 벡터
    content_vector = embedder.embed(content_concat)

    # doc_id 보장 + 정규화
    raw_id = doc.get("doc_id") or doc.get("id") or doc.get("uuid") or str(uuid.uuid4())
    did = sanitize_key(raw_id)

    # updated_at
    ua = updated_at or datetime.utcnow().isoformat()

    flat = {
        "doc_id": did,

        "major_code":  _safe_get(cls, "major", "code", default=""),
        "middle_code": _safe_get(cls, "middle","code", default=""),
        "minor_code":  _safe_get(cls, "minor", "code", default=""),
        "detail_code": _safe_get(cls, "detail","code", default=""),

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
        "content_vector": content_vector,

        "source":     doc.get("source","NCS_2025_v1"),
        "updated_at": ua,
    }

    # 빈 값 제거(선택)
    return {k: v for k, v in flat.items() if v not in (None, "")}

# =========================
# 업로드
# =========================
def upload_batch(sc: SearchClient, docs: List[Dict]) -> None:
    if not docs:
        return
    try:
        # SDK 버전에 따라 allow_unsafe_keys 지원이 없을 수 있음
        res = sc.upload_documents(documents=docs, allow_unsafe_keys=True)  # 미지원 시 TypeError
    except TypeError:
        res = sc.upload_documents(documents=docs)

    fails = [r for r in res if not getattr(r, "succeeded", False)]
    if fails:
        for r in fails[:5]:
            print(f"  ❌ upload failed key={getattr(r,'key',None)} err={getattr(r,'error_message',None)}")
        print(f"⚠️ 업로드 실패 {len(fails)}건. (총 {len(docs)} 중)")

# =========================
# 메인
# =========================
def main():
    _assert_env()

    sc = SearchClient(SEARCH_ENDPOINT, INDEX_NAME, AzureKeyCredential(SEARCH_KEY))
    _, cc, prefix = _build_blob_clients()
    embedder = Embedder()

    total = 0
    batch: List[Dict] = []
    shard_idx = 0

    try:
        for blob_name in iter_shard_blob_names(cc, prefix):
            shard_idx += 1
            print(f">> shard[{shard_idx}]: {blob_name}")

            # blob updated time
            try:
                props = cc.get_blob_client(blob_name).get_blob_properties()
                updated_at = props.last_modified.isoformat() if getattr(props, "last_modified", None) else None
            except Exception:
                updated_at = None

            for raw in read_jsonl_stream(cc, blob_name):
                try:
                    out = transform(raw, embedder, updated_at)
                except Exception as e:
                    print(f"  transform error(skip): {e}")
                    continue

                batch.append(out)
                if len(batch) >= BATCH_SIZE:
                    for attempt in range(1, 4):
                        try:
                            upload_batch(sc, batch)
                            total += len(batch)
                            print(f"  uploaded: {total}")
                            batch = []
                            break
                        except Exception as e:
                            print(f"  upload error: {e} (attempt {attempt}/3)")
                            time.sleep(1.5 * attempt)

        if batch:
            for attempt in range(1, 4):
                try:
                    upload_batch(sc, batch)
                    total += len(batch)
                    print(f"  uploaded(final): {total}")
                    batch = []
                    break
                except Exception as e:
                    print(f"  upload error(final): {e} (attempt {attempt}/3)")
                    time.sleep(1.5 * attempt)

        if shard_idx == 0:
            print("⚠️ 처리한 shard가 없습니다. prefix/컨테이너/확장자를 확인하세요.")

        print(f"✅ indexing done. total={total}")
    except ResourceNotFoundError:
        raise RuntimeError(f"❌ 컨테이너를 찾을 수 없습니다: {CONTAINER_NAME}")

if __name__ == "__main__":
    main()
