# ONNX Runtime from NuGet

Date: 2026-11-24. Status: accepted by dk.

## Context

The Rust server loads ONNX Runtime 1.31.0 (`libonnxruntime.so`) at run time.
The image and CI downloaded it from the GitHub release of
`microsoft/onnxruntime`, pinned by sha256. Since 2026-11-17 these downloads
return HTTP 404. The image build and three CI jobs failed, so the v6.0.0
release could not build.

## Decision

Get ONNX Runtime 1.31.0 from the NuGet package `Microsoft.ML.OnnxRuntime`
(`https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime/1.31.0/`).

- The package is pinned by sha256
  (`7e35fdb4f5adb62f01e60798e2e5ed05108f5fbbe1cca03fbcc37903cf7c5e3b`).
- Each extracted library is pinned by sha256 too:
  - linux-x64: `469849977cb60744ea160927e1a3a0643fd4c55c0aff743251c6d293402e334e`
  - linux-arm64: `595c7f7a6775f5c7b3d567c3ed94c04f722092575abcc092560f5208a54daf99`

## Evidence

The linux-x64 library of the NuGet package has the same sha256 as
`/opt/onnxruntime/lib/libonnxruntime.so` in the image
`memories:rust-027f254`. That image used the GitHub archive and ran in
production from 2026-11-17 to 2026-11-24. The arm64 library has no earlier
copy to compare with.

## Consequences

- `server/Dockerfile` extracts the library in a separate stage and makes the
  `.so.1` and `.so.1.31.0` links that the GitHub archive had.
- `download.onnxruntime.ai` is not used. It had a TLS name mismatch.
- If the GitHub releases return, either source gives the same x64 bytes.
