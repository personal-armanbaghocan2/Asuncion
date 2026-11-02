# Test appliance only. No server, model bytes, DB, deployment or activation.
# BASE_IMAGE must be the digest resolved and recorded by runner.py.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS native
ENV PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgomp1 tesseract-ocr tesseract-ocr-eng fonts-dejavu-core \
    && mkdir -p /opt/native-evidence \
    && cp /etc/apt/sources.list.d/debian.sources /opt/native-evidence/debian.sources \
    && dpkg-query -W > /opt/native-evidence/dpkg-build.txt \
    && python -c "import hashlib,json,pathlib; p=pathlib.Path('/var/lib/apt/lists'); pathlib.Path('/opt/native-evidence/apt-indexes.json').write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file()},sort_keys=True))" \
    && rm -rf /var/lib/apt/lists/*

# Network-enabled preparation; exact wheels only, two separate official indexes.
FROM native AS preparation
COPY candidate.json build_deps.py /preparation/
RUN python /preparation/build_deps.py

# No pip install, downloads or apt in the inference stage.
FROM native AS validation
COPY --from=preparation /opt/venv /opt/venv
COPY --from=preparation /opt/evidence /opt/evidence
COPY source/ /work/
ENV PATH=/opt/venv/bin:$PATH HOME=/tmp HF_HOME=/tmp/hf \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /work
USER 10001:10001
ENTRYPOINT ["python", "-B", "deploy/linux_validation/probe.py"]
