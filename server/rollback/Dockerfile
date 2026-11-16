# syntax=docker/dockerfile:1.7
# The rollback image (task P7-1): a Python server image plus the
# memories-server binary. At each start, the image runs export-legacy, then
# the Python server. Build from the repository root:
#
#   docker build --target core -t memories-python .
#   docker build -f server/rollback/Dockerfile --build-arg BASE_IMAGE=memories-python -t memories-rollback .
ARG BASE_IMAGE

FROM rust:1.100.0-slim-trixie@sha256:e9d95b74296f7126a83ae3d041aeaba1ff4c478f13abc5cc034fcf7c131c3d4a AS build
WORKDIR /src
ENV RUSTUP_TOOLCHAIN=1.100.0
COPY server/ .
ARG TARGETARCH
RUN --mount=type=cache,id=cargo-registry-${TARGETARCH},target=/usr/local/cargo/registry \
    --mount=type=cache,id=cargo-target-${TARGETARCH},target=/src/target \
    cargo build --release --locked --bin memories-server && \
    cp target/release/memories-server /memories-server

FROM ${BASE_IMAGE}
COPY --from=build /memories-server /usr/local/bin/memories-server
COPY server/rollback/entrypoint.sh /usr/local/bin/memories-rollback-entrypoint
# The binary must run on the C library of the Python image.
RUN memories-server --version
ENTRYPOINT ["memories-rollback-entrypoint"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
