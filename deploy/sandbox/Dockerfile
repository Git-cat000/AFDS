FROM registry-1.docker.io/library/node@sha256:0e2646ab57e77b7a384a0cb59b6815b182c77e1f9c3ba36bd8435bd9c4e6680e AS node
FROM mcr.microsoft.com/dotnet/sdk@sha256:ddbaedfc8b774389d91a511af08b77296f2c7a8d15d86f6214d2a87166933625

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 bash \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY backend/app/sandbox_protocol.py /opt/afds/sandbox_protocol.py
COPY deploy/sandbox/entrypoint.py /opt/afds/entrypoint.py
LABEL io.afds.sandbox.policy="afds-container-v1"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POWERSHELL_TELEMETRY_OPTOUT=1 \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_EnableDiagnostics=0 \
    HOME=/work \
    TMPDIR=/work \
    XDG_CACHE_HOME=/work \
    AFDS_CONTEXT_FILE=/workspace/input.json
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["python3", "-I", "-B", "/opt/afds/entrypoint.py"]
