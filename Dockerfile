FROM mcr.microsoft.com/azure-functions/python:4-python3.12

ENV AzureWebJobsScriptRoot=/home/site/wwwroot/src/operations \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
    TIKTOKEN_CACHE_DIR=/var/tmp/tiktoken_cache

WORKDIR /home/site/wwwroot

COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt && \
    mkdir -p /var/tmp/tiktoken_cache && \
    python3 -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY . /home/site/wwwroot/

# Rename local.settings.json to avoid any conflicts with environment variables
RUN if [ -f /home/site/wwwroot/local.settings.json ]; then mv /home/site/wwwroot/local.settings.json /home/site/wwwroot/local.settings.json.bak; fi

CMD ["sh", "-c", "sed -i 's/\"workerIndexing\": \"true\"/\"workerIndexing\": \"false\"/' /azure-functions-host/workers/python/worker.config.json && python3 scripts/generate_function_metadata.py && python3 scripts/init_env.py && /azure-functions-host/Microsoft.Azure.WebJobs.Script.WebHost"]
