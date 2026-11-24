# syntax=docker/dockerfile:1.7
# Memories Rust server image for linux/amd64 and linux/arm64.
# Build from the server/ folder: docker build -t memories-rust server

# ONNX Runtime 1.31.0 (the version that ort 2.0.0-rc.15 targets), from the NuGet
# package Microsoft.ML.OnnxRuntime, pinned by hash. The GitHub release downloads
# return 404 since 2026-11-17. The linux-x64 library in this package has the same
# sha256 as the library of the GitHub archive. The CI steps use the same package.
FROM scratch AS ort-nupkg
ADD --checksum=sha256:7e35fdb4f5adb62f01e60798e2e5ed05108f5fbbe1cca03fbcc37903cf7c5e3b \
    https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime/1.31.0/microsoft.ml.onnxruntime.1.31.0.nupkg /ort.nupkg

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS ort
ARG TARGETARCH
RUN apt-get update && \
    apt-get install -y --no-install-recommends unzip && \
    rm -rf /var/lib/apt/lists/*
RUN --mount=from=ort-nupkg,source=/ort.nupkg,target=/tmp/ort.nupkg \
    case "$TARGETARCH" in \
      amd64) rid=linux-x64; sum=469849977cb60744ea160927e1a3a0643fd4c55c0aff743251c6d293402e334e ;; \
      arm64) rid=linux-arm64; sum=595c7f7a6775f5c7b3d567c3ed94c04f722092575abcc092560f5208a54daf99 ;; \
      *) echo "unsupported TARGETARCH $TARGETARCH" >&2; exit 1 ;; \
    esac && \
    mkdir -p /opt/onnxruntime/lib && \
    unzip -j /tmp/ort.nupkg "runtimes/$rid/native/libonnxruntime.so" -d /opt/onnxruntime/lib && \
    echo "$sum  /opt/onnxruntime/lib/libonnxruntime.so" | sha256sum -c - && \
    mv /opt/onnxruntime/lib/libonnxruntime.so /opt/onnxruntime/lib/libonnxruntime.so.1.31.0 && \
    ln -s libonnxruntime.so.1.31.0 /opt/onnxruntime/lib/libonnxruntime.so.1 && \
    ln -s libonnxruntime.so.1 /opt/onnxruntime/lib/libonnxruntime.so

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

COPY --from=ort /opt/onnxruntime /opt/onnxruntime
RUN ldd /opt/onnxruntime/lib/libonnxruntime.so > /tmp/ldd.txt && \
    ! grep -q "not found" /tmp/ldd.txt && rm /tmp/ldd.txt

COPY --from=build /memories-server /usr/local/bin/memories-server
# The web UI files, from the build context "webui" (docker build --build-context webui=webui server).
COPY --from=webui . /app/webui
# The compose files of older installs run "python -c ..." for the health check.
RUN ln -s memories-server /usr/local/bin/python && \
    ln -s memories-server /usr/local/bin/python3

# Same user and UID (999) as the Python image, so that an existing /data volume stays writable.
RUN groupadd -r -g 999 memories && \
    useradd -r -u 999 -g 999 -m -s /bin/false memories && \
    mkdir -p /data/backups /data/model-cache && \
    chown -R memories:memories /data

ENV DATA_DIR=/data \
    UI_DIR=/app/webui \
    MODEL_CACHE_DIR=/data/model-cache \
    ORT_DYLIB_PATH=/opt/onnxruntime/lib/libonnxruntime.so

USER memories
WORKDIR /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD ["memories-server", "healthcheck"]

ENTRYPOINT ["memories-server"]
CMD ["serve"]
