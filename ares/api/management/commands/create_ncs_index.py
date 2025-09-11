
# ares/api/management/commands/create_ncs_index.py
import os
from django.core.management.base import BaseCommand, CommandError
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchField, SearchFieldDataType, SearchableField,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
    SemanticSettings, SemanticConfiguration, SemanticField, SemanticPrioritizedFields
)

from ares.api.config import SEARCH_CONFIG

class Command(BaseCommand):
    help = "Creates or updates the Azure Cognitive Search index for NCS data."

    def handle(self, *args, **options):
        endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        key = os.getenv("AZURE_SEARCH_KEY")
        index_name = SEARCH_CONFIG["NCS_INDEX"]

        if not all([endpoint, key, index_name]):
            raise CommandError("Missing required environment variables: AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY")

        self.stdout.write(f"Connecting to Azure Search endpoint: {endpoint}")
        client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))

        fields = [
            SimpleField(name="doc_id", type=SearchFieldDataType.String, key=True, filterable=True, sortable=True),
            SimpleField(name="classification.major.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchableField(name="classification.major.name", type=SearchFieldDataType.String),
            SimpleField(name="classification.middle.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchableField(name="classification.middle.name", type=SearchFieldDataType.String),
            SimpleField(name="classification.minor.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchableField(name="classification.minor.name", type=SearchFieldDataType.String),
            SimpleField(name="classification.detail.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchableField(name="classification.detail.name", type=SearchFieldDataType.String),
            SimpleField(name="ability_unit.code", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchableField(name="ability_unit.name", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
            SimpleField(name="ability_unit.level", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="element.code", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="element.name", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
            SearchableField(name="criteria_text", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
            SearchableField(name="knowledge", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
            SearchableField(name="skills", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
            SearchableField(name="attitudes", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
            SearchableField(name="content_concat", type=SearchFieldDataType.String, analyzer_name="ko.microsoft"),
            SearchField(
                name=SEARCH_CONFIG["NCS_VECTOR_FIELD"],
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=1536, # This should ideally come from AI_CONFIG
                vector_search_profile_name="ncs-vs-profile",
            ),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        ]

        vs = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-default")],
            profiles=[VectorSearchProfile(name="ncs-vs-profile", algorithm_configuration_name="hnsw-default")],
        )

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

        try:
            self.stdout.write(f"Creating or updating index '{index_name}'...")
            client.create_or_update_index(index)
            self.stdout.write(self.style.SUCCESS(f"Successfully created or updated index: {index_name}"))
        except Exception as e:
            raise CommandError(f"Failed to create or update index: {e}")
