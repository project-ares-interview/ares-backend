# ares/api/tools/create_ncs_index.py
import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchField, SearchFieldDataType, SearchableField,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
    SemanticSettings, SemanticConfiguration, SemanticField, SemanticPrioritizedFields
)

endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
key = os.getenv("AZURE_SEARCH_KEY")
index_name = os.getenv("NCS_INDEX", "ncs-index")

client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))

# === 필드 정의 (JSONL 구조와 일치) ===
fields = [
    SimpleField(name="doc_id", type=SearchFieldDataType.String, key=True, filterable=True, sortable=True),

    # classification.*
    SimpleField(name="classification.major.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="classification.major.name", type=SearchFieldDataType.String),
    SimpleField(name="classification.middle.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="classification.middle.name", type=SearchFieldDataType.String),
    SimpleField(name="classification.minor.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="classification.minor.name", type=SearchFieldDataType.String),
    SimpleField(name="classification.detail.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="classification.detail.name", type=SearchFieldDataType.String),

    # ability_unit.*
    SimpleField(name="ability_unit.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="ability_unit.name", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SimpleField(name="ability_unit.level", type=SearchFieldDataType.String, filterable=True),

    # element.*
    SimpleField(name="element.code", type=SearchFieldDataType.String, filterable=True),
    SearchableField(name="element.name", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SimpleField(name="element.level", type=SearchFieldDataType.String, filterable=True),

    # criteria_text (JSONL에선 criteria[].text, 인덱서 매핑에서 flatten 필요)
    SearchableField(name="criteria_text", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),

    SearchableField(name="knowledge", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SearchableField(name="skills", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
    SearchableField(name="attitudes", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),

    SearchableField(name="content_concat", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),

    # 벡터 필드: small → 1536차원
    SearchField(
        name="content_vector",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=1536,
        vector_search_profile_name="ncs-vs-profile",
    ),

    SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
]

# === VectorSearch 설정 ===
vs = VectorSearch(
    algorithms=[HnswAlgorithmConfiguration(name="hnsw-default")],
    profiles=[VectorSearchProfile(name="ncs-vs-profile", algorithm_configuration_name="hnsw-default")],
)

# === Semantic 설정 (선택) ===
semantic = SemanticSettings(
    configurations=[
        SemanticConfiguration(
            name="default",
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="ability_unit.name"),
                content_fields=[
                    SemanticField(field_name="criteria_text"),
                    SemanticField(field_name="knowledge"),
                    SemanticField(field_name="skills"),
                    SemanticField(field_name="attitudes"),
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
    semantic_settings=semantic,
)

client.create_or_update_index(index)
print(f"✅ created/updated index: {index_name} (dim=1536, semantic=on)")
