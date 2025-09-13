import os
from django.core.management.base import BaseCommand
from unidecode import unidecode
from ares.api.services.rag.new_azure_rag_llamaindex import AzureBlobRAGSystem

class Command(BaseCommand):
    help = 'Indexes company reports from Azure Blob Storage into Azure AI Search using RAG system.'

    def add_arguments(self, parser):
        parser.add_argument('--company_name', type=str, help='The name of the company (e.g., SK하이닉스) to create an index for.', required=True)

    def handle(self, *args, **options):
        company_name = options['company_name']
        container_name = os.getenv('AZURE_BLOB_CONTAINER', 'interview-data') # 환경 변수에서 가져오기
        
        # Generate a safe index name from the company name
        safe_company_name_for_index = unidecode(company_name.lower()).replace(' ', '-')
        index_name = f"{safe_company_name_for_index}-report-index"

        self.stdout.write(self.style.SUCCESS(f'Starting RAG index synchronization for company: {company_name} (Index: {index_name})'))

        try:
            rag_system = AzureBlobRAGSystem(
                container_name=container_name,
                index_name=index_name
            )
            rag_system.sync_index(company_name_filter=company_name)
            self.stdout.write(self.style.SUCCESS(f'Successfully synchronized RAG index for {company_name}.'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error synchronizing RAG index for {company_name}: {e}'))
            # Optionally, re-raise the exception if you want the command to fail loudly
            # raise e
