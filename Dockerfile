FROM ubuntu:22.04

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    git \
    python3 \
    python3-pip \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Ajouter le PPA SUMO manuellement sans gpg-agent
RUN curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x7604B28616B7E70EC0E6D840C32412BB1ADB414B" \
    | gpg --dearmor -o /etc/apt/trusted.gpg.d/sumo.gpg \
    && echo "deb https://ppa.launchpadcontent.net/sumo/stable/ubuntu jammy main" \
    > /etc/apt/sources.list.d/sumo.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    sumo \
    sumo-tools \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    traci \
    sumolib \
    bottleneck \
    fastmcp \
    pydantic \
    pandas \
    openpyxl

ENV SUMO_HOME=/usr/share/sumo
ENV LD_LIBRARY_PATH=/usr/local/lib:${LD_LIBRARY_PATH}
ENV PYTHONUNBUFFERED=1
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

COPY . /app
EXPOSE 8000
CMD ["/bin/bash"]