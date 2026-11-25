//! Matrix kernels of the encoder for AVX-512, AVX2 with FMA, NEON and plain Rust.
//!
//! All SIMD `unsafe` code of the crate is in this file. Each public function
//! is safe: it checks the slice sizes, then calls the kernel for the CPU.
#![allow(unsafe_code)]

use std::ops::{Deref, DerefMut};

/// Width of one panel of a [`Dense`] layer, in output columns.
pub const P: usize = 96;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Kind {
    #[cfg(target_arch = "x86_64")]
    Avx512,
    #[cfg(target_arch = "x86_64")]
    Avx2,
    #[cfg(target_arch = "aarch64")]
    Neon,
    Scalar,
}

/// A kernel family that this CPU can run. Only this module makes a value, so
/// a value is the proof that the CPU has the feature.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Simd(Kind);

impl std::fmt::Debug for Simd {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

impl Simd {
    /// Each family that this CPU can run. The fastest family is first.
    pub fn available() -> Vec<Simd> {
        let mut all = Vec::new();
        #[cfg(target_arch = "x86_64")]
        {
            if is_x86_feature_detected!("avx512f") {
                all.push(Simd(Kind::Avx512));
            }
            if is_x86_feature_detected!("avx2") && is_x86_feature_detected!("fma") {
                all.push(Simd(Kind::Avx2));
            }
        }
        // NEON is part of the base of each aarch64 target of Rust.
        #[cfg(target_arch = "aarch64")]
        all.push(Simd(Kind::Neon));
        all.push(Simd(Kind::Scalar));
        all
    }

    pub fn detect() -> Simd {
        Self::available()[0]
    }

    /// Floats in one vector. The packed weight order depends on it.
    fn lanes(self) -> usize {
        match self.0 {
            #[cfg(target_arch = "x86_64")]
            Kind::Avx512 => 16,
            #[cfg(target_arch = "x86_64")]
            Kind::Avx2 => 8,
            #[cfg(target_arch = "aarch64")]
            Kind::Neon => 4,
            Kind::Scalar => 8,
        }
    }
}

/// One cache line of floats.
#[repr(C, align(64))]
#[derive(Clone, Copy)]
struct Line([f32; 16]);

/// A buffer of floats that starts on a 64-byte boundary, so no vector load or
/// store crosses a cache line. With buffers that are not aligned, the AVX-512
/// kernel has less than half of its speed.
pub struct Buf {
    lines: Vec<Line>,
    len: usize,
}

impl Buf {
    pub fn zeroed(len: usize) -> Self {
        Self {
            lines: vec![Line([0.0; 16]); len.div_ceil(16)],
            len,
        }
    }
}

impl Deref for Buf {
    type Target = [f32];
    fn deref(&self) -> &[f32] {
        // Safety: `Line` is 16 floats with no padding, and `len` is not more
        // than 16 times the number of lines.
        unsafe { std::slice::from_raw_parts(self.lines.as_ptr().cast(), self.len) }
    }
}

impl DerefMut for Buf {
    fn deref_mut(&mut self) -> &mut [f32] {
        // Safety: as in `deref`; `&mut self` gives the only access.
        unsafe { std::slice::from_raw_parts_mut(self.lines.as_mut_ptr().cast(), self.len) }
    }
}

// The general kernel: y[r*ys + j] += sum over k of x[r*xs + k] * w[k*ws + j],
// for r < rows, k < inp, j < n.
// Safety of each function: the caller gives pointers that are valid for these
// index ranges, and the CPU has the feature.
macro_rules! acc_kernel {
    ($name:ident, $arch:ident, $feat:literal, $lanes:literal, $set1:ident, $load:ident, $store:ident, $fmadd:ident) => {
        #[target_feature(enable = $feat)]
        #[allow(clippy::too_many_arguments, unused_unsafe)]
        unsafe fn $name(
            x: *const f32,
            xs: usize,
            rows: usize,
            inp: usize,
            w: *const f32,
            ws: usize,
            n: usize,
            y: *mut f32,
            ys: usize,
        ) {
            use std::arch::$arch::*;
            unsafe {
                // 32 rows at a time keep the y rows of a block in the L1 cache.
                for r0 in (0..rows).step_by(32) {
                    let r1 = (r0 + 32).min(rows);
                    let mut k = 0;
                    while k + 8 <= inp {
                        for r in r0..r1 {
                            let xr = x.add(r * xs + k);
                            let b = [
                                $set1(*xr),
                                $set1(*xr.add(1)),
                                $set1(*xr.add(2)),
                                $set1(*xr.add(3)),
                                $set1(*xr.add(4)),
                                $set1(*xr.add(5)),
                                $set1(*xr.add(6)),
                                $set1(*xr.add(7)),
                            ];
                            let yr = y.add(r * ys);
                            let mut j = 0;
                            while j + $lanes <= n {
                                let mut a = $load(yr.add(j));
                                for i in 0..8 {
                                    a = $fmadd(b[i], $load(w.add((k + i) * ws + j)), a);
                                }
                                $store(yr.add(j), a);
                                j += $lanes;
                            }
                            while j < n {
                                let mut s = *yr.add(j);
                                for i in 0..8 {
                                    s += *xr.add(i) * *w.add((k + i) * ws + j);
                                }
                                *yr.add(j) = s;
                                j += 1;
                            }
                        }
                        k += 8;
                    }
                    while k < inp {
                        for r in r0..r1 {
                            let b = *x.add(r * xs + k);
                            for j in 0..n {
                                *y.add(r * ys + j) += b * *w.add(k * ws + j);
                            }
                        }
                        k += 1;
                    }
                }
            }
        }
    };
}

// One panel of a packed dense layer:
// t[r*P + j] += sum over k of x[r*inp + k] * W[j][k], for r < rows, j < P.
// `wp` is the panel in the order that `Dense::pack` writes, for `$lanes`.
// Safety of each function: x is [rows, inp], wp is [inp * P], t is [rows, P],
// inp is a multiple of 8, and the CPU has the feature.
macro_rules! packed_kernel {
    ($name:ident, $arch:ident, $feat:literal, $lanes:literal, $set1:ident, $load:ident, $store:ident, $fmadd:ident) => {
        #[target_feature(enable = $feat)]
        #[allow(unused_unsafe)]
        unsafe fn $name(x: *const f32, rows: usize, inp: usize, wp: *const f32, t: *mut f32) {
            use std::arch::$arch::*;
            unsafe {
                for r0 in (0..rows).step_by(32) {
                    let r1 = (r0 + 32).min(rows);
                    for kb in 0..inp / 8 {
                        let wk = wp.add(kb * 8 * P);
                        for r in r0..r1 {
                            let xr = x.add(r * inp + kb * 8);
                            let b = [
                                $set1(*xr),
                                $set1(*xr.add(1)),
                                $set1(*xr.add(2)),
                                $set1(*xr.add(3)),
                                $set1(*xr.add(4)),
                                $set1(*xr.add(5)),
                                $set1(*xr.add(6)),
                                $set1(*xr.add(7)),
                            ];
                            let tr = t.add(r * P);
                            // Six column vectors for each step: six FMA chains
                            // that do not wait for each other.
                            for jv in (0..P / $lanes).step_by(6) {
                                let (wj, tj) = (wk.add(jv * 8 * $lanes), tr.add(jv * $lanes));
                                let mut a = [
                                    $load(tj),
                                    $load(tj.add($lanes)),
                                    $load(tj.add(2 * $lanes)),
                                    $load(tj.add(3 * $lanes)),
                                    $load(tj.add(4 * $lanes)),
                                    $load(tj.add(5 * $lanes)),
                                ];
                                for i in 0..8 {
                                    for c in 0..6 {
                                        a[c] =
                                            $fmadd(b[i], $load(wj.add((c * 8 + i) * $lanes)), a[c]);
                                    }
                                }
                                for c in 0..6 {
                                    $store(tj.add(c * $lanes), a[c]);
                                }
                            }
                        }
                    }
                }
            }
        }
    };
}

#[cfg(target_arch = "x86_64")]
acc_kernel!(
    acc_avx512,
    x86_64,
    "avx512f",
    16,
    _mm512_set1_ps,
    _mm512_loadu_ps,
    _mm512_storeu_ps,
    _mm512_fmadd_ps
);
#[cfg(target_arch = "x86_64")]
acc_kernel!(
    acc_avx2,
    x86_64,
    "avx2,fma",
    8,
    _mm256_set1_ps,
    _mm256_loadu_ps,
    _mm256_storeu_ps,
    _mm256_fmadd_ps
);
#[cfg(target_arch = "x86_64")]
packed_kernel!(
    packed_avx512,
    x86_64,
    "avx512f",
    16,
    _mm512_set1_ps,
    _mm512_loadu_ps,
    _mm512_storeu_ps,
    _mm512_fmadd_ps
);
#[cfg(target_arch = "x86_64")]
packed_kernel!(
    packed_avx2,
    x86_64,
    "avx2,fma",
    8,
    _mm256_set1_ps,
    _mm256_loadu_ps,
    _mm256_storeu_ps,
    _mm256_fmadd_ps
);

/// `b * w + a`, with the argument order of the x86 intrinsic.
#[cfg(target_arch = "aarch64")]
#[target_feature(enable = "neon")]
#[inline]
fn neon_fmadd(
    b: std::arch::aarch64::float32x4_t,
    w: std::arch::aarch64::float32x4_t,
    a: std::arch::aarch64::float32x4_t,
) -> std::arch::aarch64::float32x4_t {
    std::arch::aarch64::vfmaq_f32(a, b, w)
}
#[cfg(target_arch = "aarch64")]
acc_kernel!(
    acc_neon,
    aarch64,
    "neon",
    4,
    vdupq_n_f32,
    vld1q_f32,
    vst1q_f32,
    neon_fmadd
);
#[cfg(target_arch = "aarch64")]
packed_kernel!(
    packed_neon,
    aarch64,
    "neon",
    4,
    vdupq_n_f32,
    vld1q_f32,
    vst1q_f32,
    neon_fmadd
);

#[allow(clippy::too_many_arguments)]
fn acc_scalar(
    x: &[f32],
    xs: usize,
    rows: usize,
    inp: usize,
    w: &[f32],
    ws: usize,
    n: usize,
    y: &mut [f32],
    ys: usize,
) {
    for r in 0..rows {
        let yr = &mut y[r * ys..][..n];
        for k in 0..inp {
            let b = x[r * xs + k];
            for (a, v) in yr.iter_mut().zip(&w[k * ws..][..n]) {
                *a += b * v;
            }
        }
    }
}

fn packed_scalar(x: &[f32], rows: usize, inp: usize, w: &[f32], t: &mut [f32]) {
    for kb in 0..inp / 8 {
        for r in 0..rows {
            for jv in 0..P / 8 {
                let tr = &mut t[r * P + jv * 8..][..8];
                for i in 0..8 {
                    let b = x[r * inp + kb * 8 + i];
                    let wv = &w[kb * 8 * P + jv * 64 + i * 8..][..8];
                    for (a, v) in tr.iter_mut().zip(wv) {
                        *a += b * v;
                    }
                }
            }
        }
    }
}

/// True if `count` rows with `stride` floats between row starts, each
/// `width` floats long, are in a slice of `len` floats.
fn fits(count: usize, stride: usize, width: usize, len: usize) -> bool {
    (count - 1)
        .checked_mul(stride)
        .and_then(|s| s.checked_add(width))
        .is_some_and(|end| end <= len)
}

/// `y[r*ys + j] += sum over k of x[r*xs + k] * w[k*ws + j]`, for `r < rows`,
/// `k < inp`, `j < n`. Panics if a slice is too short.
#[allow(clippy::too_many_arguments)]
pub fn acc(
    simd: Simd,
    x: &[f32],
    xs: usize,
    rows: usize,
    inp: usize,
    w: &[f32],
    ws: usize,
    n: usize,
    y: &mut [f32],
    ys: usize,
) {
    if rows == 0 || inp == 0 || n == 0 {
        return;
    }
    assert!(
        fits(rows, xs, inp, x.len()) && fits(inp, ws, n, w.len()) && fits(rows, ys, n, y.len()),
        "acc: a slice is too short"
    );
    match simd.0 {
        // Safety (each SIMD arm): the assert above gives the index ranges of
        // the kernel, and `simd` is the proof of the CPU feature.
        #[cfg(target_arch = "x86_64")]
        Kind::Avx512 => unsafe {
            acc_avx512(
                x.as_ptr(),
                xs,
                rows,
                inp,
                w.as_ptr(),
                ws,
                n,
                y.as_mut_ptr(),
                ys,
            )
        },
        #[cfg(target_arch = "x86_64")]
        Kind::Avx2 => unsafe {
            acc_avx2(
                x.as_ptr(),
                xs,
                rows,
                inp,
                w.as_ptr(),
                ws,
                n,
                y.as_mut_ptr(),
                ys,
            )
        },
        #[cfg(target_arch = "aarch64")]
        Kind::Neon => unsafe {
            acc_neon(
                x.as_ptr(),
                xs,
                rows,
                inp,
                w.as_ptr(),
                ws,
                n,
                y.as_mut_ptr(),
                ys,
            )
        },
        Kind::Scalar => acc_scalar(x, xs, rows, inp, w, ws, n, y, ys),
    }
}

/// A dense layer `y = x * W^T + b`. The weight is packed one time at load, in
/// the order in which the kernel reads it, so that no call packs it again
/// (the idea of the pre-packed weights of ONNX Runtime).
pub struct Dense {
    w: Buf,
    /// The bias, with zeros up to a full last panel.
    bias: Vec<f32>,
    inp: usize,
    out: usize,
    simd: Simd,
}

impl Dense {
    /// `raw` is the weight in the order of the model file, `[out, inp]`.
    /// Panics if `inp` is not a multiple of 8 or the sizes do not agree.
    ///
    /// Packed order: panel, block of 8 inputs, vector of columns, input, lane.
    /// The columns after `out` in the last panel are zero.
    pub fn pack(simd: Simd, raw: &[f32], bias: &[f32], inp: usize) -> Self {
        let out = bias.len();
        assert!(
            inp > 0 && inp.is_multiple_of(8) && raw.len() == out * inp,
            "pack: bad sizes"
        );
        let lanes = simd.lanes();
        let panels = out.div_ceil(P);
        let mut w = Buf::zeroed(panels * inp * P);
        let mut n = 0;
        for p in 0..panels {
            for kb in 0..inp / 8 {
                for jv in 0..P / lanes {
                    for i in 0..8 {
                        for c in 0..lanes {
                            let col = p * P + jv * lanes + c;
                            if col < out {
                                w[n] = raw[col * inp + kb * 8 + i];
                            }
                            n += 1;
                        }
                    }
                }
            }
        }
        let mut padded = vec![0f32; panels * P];
        padded[..out].copy_from_slice(bias);
        Self {
            w,
            bias: padded,
            inp,
            out,
            simd,
        }
    }

    pub fn out(&self) -> usize {
        self.out
    }

    pub fn panels(&self) -> usize {
        self.bias.len() / P
    }

    /// Output columns `[p*P, (p+1)*P)` for each row of `x`:
    /// `t[r*P + j] = b[p*P + j] + sum over k of x[r*inp + k] * W[p*P + j][k]`.
    /// `x` is `[rows, inp]` and `t` is `[rows, P]`. Panics if a size is wrong.
    pub fn panel(&self, p: usize, x: &[f32], rows: usize, t: &mut [f32]) {
        let inp = self.inp;
        assert!(
            p < self.panels() && x.len() == rows * inp && t.len() == rows * P,
            "panel: bad sizes"
        );
        for row in t.as_chunks_mut::<P>().0 {
            row.copy_from_slice(&self.bias[p * P..][..P]);
        }
        let w = &self.w[p * inp * P..][..inp * P];
        match self.simd.0 {
            // Safety (each SIMD arm): the assert and the slice above give
            // x [rows, inp], w [inp * P] and t [rows, P]; `pack` made `inp` a
            // multiple of 8 and used the lanes of `simd`; `simd` is the proof
            // of the CPU feature.
            #[cfg(target_arch = "x86_64")]
            Kind::Avx512 => unsafe {
                packed_avx512(x.as_ptr(), rows, inp, w.as_ptr(), t.as_mut_ptr())
            },
            #[cfg(target_arch = "x86_64")]
            Kind::Avx2 => unsafe { packed_avx2(x.as_ptr(), rows, inp, w.as_ptr(), t.as_mut_ptr()) },
            #[cfg(target_arch = "aarch64")]
            Kind::Neon => unsafe { packed_neon(x.as_ptr(), rows, inp, w.as_ptr(), t.as_mut_ptr()) },
            Kind::Scalar => packed_scalar(x, rows, inp, w, t),
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// Fixed pseudo-random floats in [-1, 1).
    pub(crate) fn noise(n: usize, seed: u64) -> Vec<f32> {
        let mut s = seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1;
        (0..n)
            .map(|_| {
                s ^= s << 13;
                s ^= s >> 7;
                s ^= s << 17;
                (s >> 40) as f32 / (1u64 << 23) as f32 - 1.0
            })
            .collect()
    }

    fn close(got: &[f32], want: &[f64], what: &str) {
        assert_eq!(got.len(), want.len(), "{what}");
        for (i, (g, w)) in got.iter().zip(want).enumerate() {
            assert!(
                (f64::from(*g) - w).abs() <= 1e-4 * (1.0 + w.abs()),
                "{what}: index {i}: got {g}, want {w}"
            );
        }
    }

    #[test]
    fn acc_agrees_with_plain_loops_on_each_cpu_path() {
        for simd in Simd::available() {
            for rows in [1, 2, 31, 32, 33, 65] {
                for inp in [1, 7, 8, 9, 32] {
                    for n in [1, 5, 16, 26, 33] {
                        let (xs, ws, ys) = (inp + 3, n + 2, n + 1);
                        let x = noise(rows * xs, 1);
                        let w = noise(inp * ws, 2);
                        let start = noise(rows * ys, 3);
                        let mut y = start.clone();
                        acc(simd, &x, xs, rows, inp, &w, ws, n, &mut y, ys);
                        let mut want: Vec<f64> = start.iter().map(|v| f64::from(*v)).collect();
                        for r in 0..rows {
                            for k in 0..inp {
                                for j in 0..n {
                                    want[r * ys + j] +=
                                        f64::from(x[r * xs + k]) * f64::from(w[k * ws + j]);
                                }
                            }
                        }
                        close(&y, &want, &format!("{simd:?} rows={rows} inp={inp} n={n}"));
                    }
                }
            }
        }
    }

    #[test]
    fn acc_does_nothing_for_an_empty_size_and_refuses_a_short_slice() {
        let simd = Simd::detect();
        let mut y = [1.0f32; 4];
        acc(simd, &[], 4, 0, 4, &[], 4, 4, &mut y, 4);
        assert_eq!(y, [1.0; 4]);
        let short = std::panic::catch_unwind(|| {
            let mut y = [0f32; 7];
            acc(
                Simd::detect(),
                &[0.0; 8],
                4,
                2,
                4,
                &[0.0; 16],
                4,
                4,
                &mut y,
                4,
            );
        });
        assert!(short.is_err());
    }

    #[test]
    fn dense_agrees_with_plain_loops_on_each_cpu_path() {
        for simd in Simd::available() {
            // 96 and 192 are full panels; 8, 104 and 200 have a part panel.
            let sizes: &[(usize, usize)] = if cfg!(miri) {
                // Miri runs each step about 1,000 times slower.
                &[(8, 24), (104, 8)]
            } else {
                &[(96, 8), (8, 24), (104, 104), (200, 104), (192, 16)]
            };
            for &(out, inp) in sizes {
                let raw = noise(out * inp, 4);
                let bias = noise(out, 5);
                let dense = Dense::pack(simd, &raw, &bias, inp);
                assert_eq!((dense.out(), dense.panels()), (out, out.div_ceil(P)));
                let row_counts: &[usize] = if cfg!(miri) {
                    &[0, 1, 33]
                } else {
                    &[0, 1, 31, 32, 33, 65]
                };
                for &rows in row_counts {
                    let x = noise(rows * inp, 6);
                    for p in 0..dense.panels() {
                        let mut t = Buf::zeroed(rows * P);
                        dense.panel(p, &x, rows, &mut t);
                        let mut want = vec![0f64; rows * P];
                        for r in 0..rows {
                            for j in 0..P.min(out - p * P) {
                                let col = p * P + j;
                                want[r * P + j] = f64::from(bias[col])
                                    + (0..inp)
                                        .map(|k| {
                                            f64::from(x[r * inp + k])
                                                * f64::from(raw[col * inp + k])
                                        })
                                        .sum::<f64>();
                            }
                        }
                        close(
                            &t,
                            &want,
                            &format!("{simd:?} out={out} inp={inp} rows={rows} p={p}"),
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn buf_is_aligned_and_has_the_asked_length() {
        for len in [0, 1, 16, 17, 100] {
            let mut b = Buf::zeroed(len);
            assert_eq!(b.len(), len);
            assert_eq!(b.as_ptr() as usize % 64, 0);
            b.iter_mut().for_each(|v| *v = 1.0);
            assert!(b.iter().all(|v| *v == 1.0));
        }
    }
}
