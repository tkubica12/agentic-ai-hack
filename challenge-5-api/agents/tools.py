import os
import json
from typing import Annotated
from semantic_kernel.functions import kernel_function

class CosmosDBPlugin:
    """
    A production-ready Cosmos DB plugin that connects to real Azure Cosmos DB.
    This plugin retrieves actual JSON documents from your database.
    """
    
    def __init__(self, endpoint: str = None, key: str = None, database_name: str = "MyDatabase", container_name: str = "MyContainer"):
        """
        Initialize the Cosmos DB plugin with connection details.
        For production, use environment variables or Azure Key Vault for credentials.
        """
        self.endpoint = endpoint or os.environ.get("COSMOS_ENDPOINT")
        self.key = key or os.environ.get("COSMOS_KEY") 
        self.database_name = "insurance_claims"
        self.container_name = "crash_reports"
    
    def _get_cosmos_client(self):
        """Create and return a Cosmos DB client."""
        if not self.endpoint or not self.key:
            raise Exception("Cosmos DB endpoint and key must be configured. Please set COSMOS_ENDPOINT and COSMOS_KEY environment variables.")
        
        try:
            from azure.cosmos import CosmosClient
            return CosmosClient(self.endpoint, self.key)
        except ImportError:
            raise ImportError("azure-cosmos package not installed. Run: pip install azure-cosmos")
        except Exception as e:
            raise Exception(f"Failed to create Cosmos DB client: {str(e)}")
    
    @kernel_function(description="Test Cosmos DB connection and list available claims")
    def test_connection(self) -> Annotated[str, "Connection test result and available claims"]:
        """Test the Cosmos DB connection and show what claims are available."""
        try:
            client = self._get_cosmos_client()
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.container_name)
            
            # Test with a simple query to get all claim IDs
            query = "SELECT c.claim_id, c.id FROM c"
            items = list(container.query_items(
                query=query,
                enable_cross_partition_query=True,
                max_item_count=10  # Limit to first 10 for testing
            ))
            
            if not items:
                return f"✅ Connection successful but no documents found in container '{self.container_name}'"
            
            claim_ids = [item.get('claim_id', 'N/A') for item in items]
            result = {
                "connection_status": "SUCCESS",
                "database": self.database_name,
                "container": self.container_name,
                "documents_found": len(items),
                "available_claim_ids": claim_ids
            }
            
            return json.dumps(result, indent=2)
            
        except Exception as e:
            return f"❌ Connection test failed: {str(e)}"
    
    @kernel_function(description="Retrieve a document by claim_id from Cosmos DB using cross-partition query")
    def get_document_by_claim_id(
        self, 
        claim_id: Annotated[str, "The claim_id to retrieve (not the partition key)"]
    ) -> Annotated[str, "JSON document from Cosmos DB"]:
        """Retrieve a document by its claim_id using a cross-partition query."""
        try:
            client = self._get_cosmos_client()
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.container_name)
            
            # Use SQL query to find document by claim_id across all partitions
            query = "SELECT * FROM c WHERE c.claim_id = @claim_id"
            parameters = [{"name": "@claim_id", "value": claim_id}]
            
            items = list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
                max_item_count=1  # We expect only one document with this claim_id
            ))
            
            if not items:
                # Try to find what claim IDs actually exist
                all_claims_query = "SELECT c.claim_id FROM c"
                all_items = list(container.query_items(
                    query=all_claims_query,
                    enable_cross_partition_query=True,
                    max_item_count=10
                ))
                available_ids = [item.get('claim_id', 'N/A') for item in all_items]
                
                return f"❌ No document found with claim_id '{claim_id}' in container '{self.container_name}'.\n\nAvailable claim IDs: {available_ids}\n\nPlease verify the claim ID exists in the database."
            
            # Return the first (and should be only) matching document
            document = items[0]
            return json.dumps(document, indent=2, ensure_ascii=False)
            
        except Exception as e:
            error_msg = str(e)
            if "endpoint and key must be configured" in error_msg:
                return f"❌ Cosmos DB not configured. Please set COSMOS_ENDPOINT and COSMOS_KEY environment variables. Error: {error_msg}"
            elif "Unauthorized" in error_msg or "401" in error_msg:
                return f"❌ Authentication failed. Please check your Cosmos DB credentials. Error: {error_msg}"
            elif "Forbidden" in error_msg or "403" in error_msg:
                return f"❌ Access denied. Please check your Cosmos DB permissions. Error: {error_msg}"
            elif "azure-cosmos package not installed" in error_msg:
                return f"❌ Missing dependency. Please run: pip install azure-cosmos"
            else:
                return f"❌ Error retrieving document by claim_id '{claim_id}': {error_msg}"
    
    @kernel_function(description="Retrieve a JSON document by partition key and document ID from Cosmos DB")
    def get_document_by_id(
        self, 
        document_id: Annotated[str, "The document ID to retrieve"],
        partition_key: Annotated[str, "The partition key value (optional, will use cross-partition query if not provided)"] = None
    ) -> Annotated[str, "JSON document from Cosmos DB"]:
        """Retrieve a specific document by its ID and optionally partition key from Cosmos DB."""
        try:
            client = self._get_cosmos_client()
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.container_name)
            
            if partition_key:
                # Direct read using partition key - most efficient
                item = container.read_item(item=document_id, partition_key=partition_key)
                return json.dumps(item, indent=2, ensure_ascii=False)
            else:
                # Cross-partition query when partition key is unknown
                query = "SELECT * FROM c WHERE c.id = @document_id"
                parameters = [{"name": "@document_id", "value": document_id}]
                
                items = list(container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                    max_item_count=1
                ))
                
                if not items:
                    return f"❌ Document with ID '{document_id}' not found in container '{self.container_name}'"
                
                return json.dumps(items[0], indent=2, ensure_ascii=False)
            
        except Exception as e:
            error_msg = str(e)
            if "NotFound" in error_msg or "404" in error_msg:
                return f"❌ Document with ID '{document_id}' not found in container '{self.container_name}'"
            elif "Unauthorized" in error_msg or "401" in error_msg:
                return f"❌ Authentication failed. Please check your Cosmos DB credentials."
            elif "Forbidden" in error_msg or "403" in error_msg:
                return f"❌ Access denied. Please check your Cosmos DB permissions."
            else:
                return f"❌ Error retrieving document: {error_msg}"
    
    @kernel_function(description="Query documents with a custom SQL query in Cosmos DB")
    def query_documents(
        self, 
        sql_query: Annotated[str, "SQL query to execute (e.g., 'SELECT * FROM c WHERE c.category = \"electronics\"')"]
    ) -> Annotated[str, "Query results as JSON"]:
        """Execute a custom SQL query against the Cosmos DB container."""
        try:
            client = self._get_cosmos_client()
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.container_name)
            
            # Execute the query
            items = list(container.query_items(
                query=sql_query,
                enable_cross_partition_query=True  # Enable if your query spans partitions
            ))
            
            if not items:
                return f"🔍 No documents found matching query: {sql_query}"
            
            # Return results as formatted JSON
            result = {
                "query": sql_query,
                "count": len(items),
                "results": items
            }
            
            return json.dumps(result, indent=2, ensure_ascii=False)
            
        except Exception as e:
            error_msg = str(e)
            if "Syntax error" in error_msg:
                return f"❌ SQL syntax error in query: {sql_query}\nError: {error_msg}"
            else:
                return f"❌ Error executing query: {error_msg}"
    
    @kernel_function(description="Get container information and statistics")
    def get_container_info(self) -> Annotated[str, "Container information and statistics"]:
        """Get information about the Cosmos DB container."""
        try:
            client = self._get_cosmos_client()
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.container_name)
            
            # Get container properties
            container_props = container.read()
            
            # Get approximate document count (this is an estimate)
            count_query = "SELECT VALUE COUNT(1) FROM c"
            count_items = list(container.query_items(
                query=count_query,
                enable_cross_partition_query=True
            ))
            document_count = count_items[0] if count_items else "Unknown"
            
            info = {
                "database": self.database_name,
                "container": self.container_name,
                "partition_key": container_props.get("partitionKey", {}).get("paths", ["Unknown"]),
                "approximate_document_count": document_count,
                "indexing_policy": container_props.get("indexingPolicy", {}).get("indexingMode", "Unknown")
            }
            
            return json.dumps(info, indent=2)
            
        except Exception as e:
            return f"❌ Error getting container info: {str(e)}"
    
    @kernel_function(description="List recent documents (up to 100) from Cosmos DB")
    def list_recent_documents(
        self, 
        limit: Annotated[int, "Maximum number of documents to return (default: 10, max: 100)"] = 10
    ) -> Annotated[str, "List of recent documents"]:
        """List recent documents from the container."""
        try:
            # Ensure limit is within bounds
            limit = max(1, min(limit, 100))
            
            client = self._get_cosmos_client()
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.container_name)
            
            # Query for documents (ordered by _ts if available)
            query = f"SELECT TOP {limit} * FROM c ORDER BY c._ts DESC"
            
            items = list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            if not items:
                return "📭 No documents found in the container"
            
            result = {
                "container": self.container_name,
                "count": len(items),
                "documents": items
            }
            
            return json.dumps(result, indent=2, ensure_ascii=False)
            
        except Exception as e:
            return f"❌ Error listing documents: {str(e)}"
    
    @kernel_function(description="Search documents by field value")
    def search_by_field(
        self, 
        field_name: Annotated[str, "The field name to search in (e.g., 'name', 'category', 'status')"],
        field_value: Annotated[str, "The value to search for"]
    ) -> Annotated[str, "Documents matching the search criteria"]:
        """Search for documents where a specific field matches a value."""
        try:
            client = self._get_cosmos_client()
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.container_name)
            
            # Use parameterized query for better security and performance
            query = f"SELECT * FROM c WHERE c.{field_name} = @field_value"
            parameters = [{"name": "@field_value", "value": field_value}]
            
            items = list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            
            if not items:
                return f"🔍 No documents found where {field_name} = '{field_value}'"
            
            result = {
                "search_criteria": f"{field_name} = '{field_value}'",
                "count": len(items),
                "documents": items
            }
            
            return json.dumps(result, indent=2, ensure_ascii=False)
            
        except Exception as e:
            return f"❌ Error searching documents: {str(e)}"

# Create an instance of the real Cosmos DB plugin
cosmos_plugin = CosmosDBPlugin()