FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.30.10
ARG CRANE_VERSION=v0.20.7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl jq util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl

RUN curl -fsSL -o /usr/local/bin/kubectl-ko "https://raw.githubusercontent.com/kubeovn/kube-ovn/release-1.15/dist/images/kubectl-ko" \
    && chmod +x /usr/local/bin/kubectl-ko

RUN curl -fsSL "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz" \
      -o /tmp/go-containerregistry.tar.gz \
    && tar -xzf /tmp/go-containerregistry.tar.gz -C /usr/local/bin crane \
    && chmod +x /usr/local/bin/crane \
    && rm -f /tmp/go-containerregistry.tar.gz

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY draino /app/draino

RUN pip install --upgrade pip \
    && pip install .

RUN useradd --create-home --uid 10001 draino

USER draino

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["draino", "--web", "--host", "0.0.0.0", "--port", "8000", "--audit-log", "/tmp/draino-audit.log"]
