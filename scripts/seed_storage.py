import asyncio
import os
import glob
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.queue.aio import QueueServiceClient

async def seed_storage():
    # Use the explicit standard Azurite connection string.
    # This is the most robust way to connect to Azurite inside a container
    # when network_mode: "service:azurite" is used (localhost refers to Azurite).
    # It bypasses any "Managed Identity" or "incorrect authentication" logic in the SDK.
    connection_string = (
        "DefaultEndpointsProtocol=http;"
        "AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
        "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
    )
    
    service_client = BlobServiceClient.from_connection_string(connection_string)
    queue_service_client = QueueServiceClient.from_connection_string(connection_string)
    
    # Containers to create
    containers = ["graphify-config", "graphify-out"]
    
    for container_name in containers:
        container_client = service_client.get_container_client(container_name)
        try:
            await container_client.create_container()
            print(f"Created container: {container_name}")
        except Exception as e:
            if "ContainerAlreadyExists" in str(e):
                print(f"Container {container_name} already exists.")
            else:
                print(f"Error creating container {container_name}: {e}")

    # 1. Upload schemas to graphify-config
    config_client = service_client.get_container_client("graphify-config")
    schemas = ["ontology_schema.json"]
    for schema in schemas:
        schema_path = f"schemas/{schema}"
        if os.path.exists(schema_path):
            blob_client = config_client.get_blob_client(f"schemas/{schema}")
            with open(schema_path, "rb") as data:
                await blob_client.upload_blob(data, overwrite=True)
                print(f"Uploaded schema: schemas/{schema}")

    # 2. Upload prompts to graphify-config
    prompt_files = glob.glob("prompts/*.md")
    for prompt_path in prompt_files:
        filename = os.path.basename(prompt_path)
        blob_client = config_client.get_blob_client(f"prompts/{filename}")
        with open(prompt_path, "rb") as data:
            await blob_client.upload_blob(data, overwrite=True)
            print(f"Uploaded prompt: prompts/{filename}")

    # 3. Create Queue
    print("Attempting to create pipeline-queue...")
    queue_client = queue_service_client.get_queue_client("pipeline-queue")
    try:
        await queue_client.create_queue()
        print("Created queue: pipeline-queue")
    except Exception as e:
        if "QueueAlreadyExists" in str(e):
            print("Queue pipeline-queue already exists.")
        else:
            print(f"Error creating queue: {e}")

    await service_client.close()
    await queue_service_client.close()

if __name__ == "__main__":
    asyncio.run(seed_storage())
