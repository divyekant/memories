# syntax=docker/dockerfile:1.7
# Memories Rust server image for linux/amd64 and linux/arm64.
# Build from the server/ folder: docker build -t memories-rust server

# ONNX Runtime 1.31.0 (the version that ort 2.0.0-rc.15 targets), pinned by hash.
# The G2 step of .github/workflows/ci.yml uses the same amd64 archive and hash.
FROM scratch AS ort-amd64
ADD --checksum=sha256:cc5c72baf5ae5c8238a6841f0897227be2d02826b9cf98eaf02fdefaeeb03a57 \
    https://github.com/microsoft/onnxruntime/releases/download/v1.31.0/onnxruntime-linux-x64-1.31.0.tgz /ort.tgz

FROM scratch AS ort-arm64
ADD --checksum=sha256:c5b8b3cca31f3d643a3b313b8f42e2d0f16a4fcefd02cda343d97bd5306afaab \
    https://github.com/microsoft/onnxruntime/releases/download/v1.31.0/onnxruntime-linux-aarch64-1.31.0.tgz /ort.tgz

# BuildKit sets TARGETARCH (amd64 or arm64) in the global scope.
FROM ort-${TARGETARCH} AS ort

FROM rust:1.100.0-slim-trixie@sha256:e9d95b74296f7126a83ae3d041aeaba1ff4c478f13abc5cc034fcf7c131c3d4a AS build
WORKDIR /src
# Use the toolchain of the base image. rust-toolchain.toml names the same version.
ENV RUSTUP_TOOLCHAIN=1.100.0
COPY . .
ARG TARGETARCH
RUN --mount=type=cache,id=cargo-registry-${TARGETARCH},target=/usr/local/cargo/registry \
    --mount=type=cache,id=cargo-target-${TARGETARCH},target=/src/target \
    cargo build --release --locked --bin memories-server && \
    cp target/release/memories-server /memories-server

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS runtime

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=from=ort,source=/ort.tgz,target=/tmp/ort.tgz \
    mkdir -p /opt/onnxruntime/lib && \
    tar -xzf /tmp/ort.tgz -C /opt/onnxruntime/lib --strip-components=2 --no-same-owner --wildcards '*/lib/libonnxruntime.so*' && \
    ldd /opt/onnxruntime/lib/libonnxruntime.so > /tmp/ldd.txt && \
    ! grep -q "not found" /tmp/ldd.txt && rm /tmp/ldd.txt

COPY --from=build /memories-server /usr/local/bin/memories-server
# The compose files of current installs run "python -c ..." for the health check.
RUN ln -s memories-server /usr/local/bin/python && \
    ln -s memories-server /usr/local/bin/python3

# Same user and UID (999) as the Python image, so that an existing /data volume stays writable.
RUN groupadd -r -g 999 memories && \
    useradd -r -u 999 -g 999 -m -s /bin/false memories && \
    mkdir -p /data/backups /data/model-cache && \
    chown -R memories:memories /data

ENV DATA_DIR=/data \
    MODEL_CACHE_DIR=/data/model-cache \
    ORT_DYLIB_PATH=/opt/onnxruntime/lib/libonnxruntime.so

USER memories
WORKDIR /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import sys,urllib.request; sys.exit(0 if 200 <= urllib.request.urlopen('http://localhost:8000/health', timeout=3).getcode() < 400 else 1)"

ENTRYPOINT ["memories-server"]
CMD ["serve"]
