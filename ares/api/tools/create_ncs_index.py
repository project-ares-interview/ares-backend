# ares/api/tools/create_ncs_index.py
import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchField, SearchFieldDataType, SearchableField,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
)

# 구버전 SDK 호환: Semantic 설정이 없으면 생략
try:
    from azure.search.documents.indexes.models import (
        SemanticConfiguration, SemanticSettings, PrioritizedFields, SemanticField
    )
    HAS_SEMANTIC = True
except ImportError:
    HAS_SEMANTIC = False

endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
key = os.getenv("AZURE_SEARCH_KEY")
index_name = os.getenv("NCS_INDEX", "ncs-index")

client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))

# === 필드 정의 ===
fields = [
    SimpleField(name="doc_id", type=SearchFieldDataType.String, key=True, filterable=True),

    SimpleField(name="major_code",  type=SearchFieldDataType.String, filterable=True, facetable=True),
    SimpleField(name="middle_code", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SimpleField(name="minor_code",  type=SearchFieldDataType.String, filterable=True, facetable=True),
    SimpleField(name="detail_code", type=SearchFieldDataType.String, filterable=True, facetable=True),

    SimpleField(name="ability_code", type=SearchFieldDataType.String, filterable=True),
    SearchableField(name="ability_name", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SimpleField(name="ability_level", type=SearchFieldDataType.String, filterable=True, facetable=True),

    SimpleField(name="element_code", type=SearchFieldDataType.String, filterable=True),
    SearchableField(name="element_name", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),

    SearchableField(name="criteria_text", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SearchableField(name="knowledge",     type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SearchableField(name="skills",        type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SearchableField(name="attitudes",     type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),

    SearchableField(name="content_concat", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),

    # 벡터 필드 (임베딩 차원 확인: small=1536, large=3072)
    SearchField(
        name="content_vector",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=1536,
        vector_search_profile_name="ncs-vs-profile",
    ),

    SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
    SimpleField(name="updated_at", type=SearchFieldDataType.String, filterable=True, sortable=True),
]

# === VectorSearch 설정 ===
vs = VectorSearch(
    algorithms=[HnswAlgorithmConfiguration(name="hnsw-default")],
    profiles=[VectorSearchProfile(name="ncs-vs-profile", algorithm_configuration_name="hnsw-default")],
)

kwargs = {}
if HAS_SEMANTIC:
    kwargs["semantic_settings"] = SemanticSettings(
        configurations=[
            SemanticConfiguration(
                name="ncs-semantic",
                prioritized_fields=PrioritizedFields(
                    title_field=SemanticField(field_name="ability_name"),
                    content_fields=[
                        SemanticField(field_name="criteria_text"),
                        SemanticField(field_name="knowledge"),
                        SemanticField(field_name="skills"),
                        SemanticField(field_name="content_concat"),
                    ],
                ),
            )
        ]
    )

index = SearchIndex(
    name=index_name,
    fields=fields,
    vector_search=vs,
    **kwargs
)

client.create_or_update_index(index)
print(f"✅ created/updated index: {index_name} (semantic={'on' if HAS_SEMANTIC else 'off'})")
