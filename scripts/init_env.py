import asyncio
import os
import glob
import logging
import sys
import time
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.queue.aio import QueueClient
from azure.core.exceptions import ResourceExistsError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("init_env")

async def wait_for_azurite(connection_string, timeout=30):
    """Wait for Azurite to be ready by attempting to list containers."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # If connection_string is UseDevelopmentStorage=true, it uses 127.0.0.1
            service_client = BlobServiceClient.from_connection_string(connection_string)
            async with service_client:
                async for _ in service_client.list_containers():
                    break
            logger.info("Azurite is ready!")
            return True
        except Exception as e:
            logger.info(f"Waiting for Azurite... (Error: {e})")
            await asyncio.sleep(2)
    return False

async def seed_storage(connection_string):
    service_client = BlobServiceClient.from_connection_string(connection_string)
    containers = ["graphify-config", "graphify-out"]
    async with service_client:
        for container_name in containers:
            container_client = service_client.get_container_client(container_name)
            try:
                await container_client.create_container()
                logger.info(f"Created container: {container_name}")
            except ResourceExistsError:
                logger.info(f"Container {container_name} already exists.")
            except Exception as e:
                logger.error(f"Error creating container {container_name}: {e}")

        config_client = service_client.get_container_client("graphify-config")
        schemas = ["ontology_schema.json"]
        for schema in schemas:
            schema_path = f"schemas/{schema}"
            if os.path.exists(schema_path):
                blob_client = config_client.get_blob_client(f"schemas/{schema}")
                with open(schema_path, "rb") as data:
                    await blob_client.upload_blob(data, overwrite=True)
                    logger.info(f"Uploaded schema: schemas/{schema}")

        prompt_files = glob.glob("prompts/*.md")
        for prompt_path in prompt_files:
            filename = os.path.basename(prompt_path)
            blob_client = config_client.get_blob_client(f"prompts/{filename}")
            with open(prompt_path, "rb") as data:
                await blob_client.upload_blob(data, overwrite=True)
                logger.info(f"Uploaded prompt: prompts/{filename}")

async def seed_queues(connection_string):
    queue_name = "pipeline-queue"
    queue_client = QueueClient.from_connection_string(connection_string, queue_name)
    async with queue_client:
        try:
            await queue_client.create_queue()
            logger.info(f"Created queue: {queue_name}")
        except ResourceExistsError:
            logger.info(f"Queue {queue_name} already exists.")
        except Exception as e:
            logger.error(f"Error creating queue {queue_name}: {e}")

async def main():
    conn_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    
    # Robust handling for Azurite/Local Development
    if not conn_str or "devstoreaccount1" in conn_str or conn_str.lower() == "usedevelopmentstorage=true":
        logger.info("Local development storage detected or no connection string provided. Using standard Azurite connection string.")
        conn_str = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
            "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
            "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
        )
    
    logger.info(f"Starting environment initialization with connection string: {conn_str.split(';')[0]}...")

    if not await wait_for_azurite(conn_str):
        logger.error("Azurite did not become ready in time. Exiting.")
        sys.exit(1)

    try:
        await seed_storage(conn_str)
        await seed_queues(conn_str)
        logger.info("Environment initialization complete.")
    except Exception as e:
        logger.error(f"Error during initialization: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
