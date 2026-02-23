/*!
 * slicer_core — Rust hot paths for MeshyGen slicer.
 *
 * Exposed to Python via PyO3. Provides:
 *   - slice_at_z()        : Triangle-plane intersection (the main geometry bottleneck)
 *   - slice_all_layers()  : Batch slice every layer in one Rust call (parallelised with rayon)
 *   - generate_waves()    : Vectorised wave-pattern computation
 *   - parse_binary_stl()  : Zero-copy binary STL decoder
 */

use pyo3::prelude::*;
use rayon::prelude::*;
use std::f64::consts::PI;

// ─────────────────────────────────────────────────────────────────────────────
// Internal geometry helpers (not exported to Python)
// ─────────────────────────────────────────────────────────────────────────────

/// Find intersection points of all triangle edges with the horizontal plane at `z`.
/// Returns a Vec of (x, y) pairs — unsorted.
fn intersect_edges_with_plane(
    v0: &[[f64; 3]],
    v1: &[[f64; 3]],
    v2: &[[f64; 3]],
    z: f64,
) -> Vec<(f64, f64)> {
    let n = v0.len();
    let mut pts: Vec<(f64, f64)> = Vec::with_capacity(n);

    // Helper closure: intersect one directed edge (a→b) with plane z
    let mut intersect_edge = |a: &[f64; 3], b: &[f64; 3]| {
        let az = a[2];
        let bz = b[2];
        // Edge spans z if one endpoint is ≤z and the other is ≥z
        if !((az <= z && bz >= z) || (bz <= z && az >= z)) {
            return;
        }
        let dz = bz - az;
        if dz.abs() < 1e-10 {
            return;
        }
        let t = (z - az) / dz;
        if t < -1e-6 || t > 1.0 + 1e-6 {
            return;
        }
        let ix = a[0] + t * (b[0] - a[0]);
        let iy = a[1] + t * (b[1] - a[1]);
        pts.push((ix, iy));
    };

    for i in 0..n {
        intersect_edge(&v0[i], &v1[i]);
        intersect_edge(&v1[i], &v2[i]);
        intersect_edge(&v2[i], &v0[i]);
    }

    pts
}

/// Deduplicate and sort points by angle around their centroid.
fn sort_and_dedup(mut pts: Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    if pts.len() < 2 {
        return pts;
    }

    // Sort for dedup (rounded to 0.01 mm)
    pts.sort_by(|a, b| {
        let ka = ((a.0 * 100.0).round() as i64, (a.1 * 100.0).round() as i64);
        let kb = ((b.0 * 100.0).round() as i64, (b.1 * 100.0).round() as i64);
        ka.cmp(&kb)
    });
    pts.dedup_by(|a, b| (a.0 - b.0).abs() < 0.01 && (a.1 - b.1).abs() < 0.01);

    if pts.len() < 2 {
        return pts;
    }

    // Sort by angle around centroid to form a closed polygon
    let cx = pts.iter().map(|p| p.0).sum::<f64>() / pts.len() as f64;
    let cy = pts.iter().map(|p| p.1).sum::<f64>() / pts.len() as f64;
    pts.sort_by(|a, b| {
        let ang_a = (a.1 - cy).atan2(a.0 - cx);
        let ang_b = (b.1 - cy).atan2(b.0 - cx);
        ang_a.partial_cmp(&ang_b).unwrap_or(std::cmp::Ordering::Equal)
    });

    pts
}

// ─────────────────────────────────────────────────────────────────────────────
// Python-exported functions
// ─────────────────────────────────────────────────────────────────────────────

/// Slice a triangle mesh with a horizontal plane at height `z`.
///
/// Args (flat f64 lists, one per vertex coordinate):
///   v0x, v0y, v0z  — first vertex of each triangle
///   v1x, v1y, v1z  — second vertex
///   v2x, v2y, v2z  — third vertex
///   z              — plane height
///
/// Returns (xs, ys): sorted perimeter point arrays.
#[pyfunction]
fn slice_at_z(
    v0x: Vec<f64>, v0y: Vec<f64>, v0z: Vec<f64>,
    v1x: Vec<f64>, v1y: Vec<f64>, v1z: Vec<f64>,
    v2x: Vec<f64>, v2y: Vec<f64>, v2z: Vec<f64>,
    z: f64,
) -> (Vec<f64>, Vec<f64>) {
    let n = v0x.len();
    let v0: Vec<[f64; 3]> = (0..n).map(|i| [v0x[i], v0y[i], v0z[i]]).collect();
    let v1: Vec<[f64; 3]> = (0..n).map(|i| [v1x[i], v1y[i], v1z[i]]).collect();
    let v2: Vec<[f64; 3]> = (0..n).map(|i| [v2x[i], v2y[i], v2z[i]]).collect();

    let pts = intersect_edges_with_plane(&v0, &v1, &v2, z);
    let pts = sort_and_dedup(pts);

    let xs: Vec<f64> = pts.iter().map(|p| p.0).collect();
    let ys: Vec<f64> = pts.iter().map(|p| p.1).collect();
    (xs, ys)
}

/// Slice ALL layers in one call — fully parallelised with Rayon.
///
/// z_levels: list of Z heights to slice at.
/// Returns: list of (xs, ys) per layer (empty vecs for layers with no geometry).
#[pyfunction]
fn slice_all_layers(
    v0x: Vec<f64>, v0y: Vec<f64>, v0z: Vec<f64>,
    v1x: Vec<f64>, v1y: Vec<f64>, v1z: Vec<f64>,
    v2x: Vec<f64>, v2y: Vec<f64>, v2z: Vec<f64>,
    z_levels: Vec<f64>,
) -> Vec<(Vec<f64>, Vec<f64>)> {
    let n = v0x.len();
    // Build triangle arrays once
    let v0: Vec<[f64; 3]> = (0..n).map(|i| [v0x[i], v0y[i], v0z[i]]).collect();
    let v1: Vec<[f64; 3]> = (0..n).map(|i| [v1x[i], v1y[i], v1z[i]]).collect();
    let v2: Vec<[f64; 3]> = (0..n).map(|i| [v2x[i], v2y[i], v2z[i]]).collect();

    // Pre-compute Z bounds for each triangle for fast culling
    let tri_min_z: Vec<f64> = (0..n)
        .map(|i| v0[i][2].min(v1[i][2]).min(v2[i][2]))
        .collect();
    let tri_max_z: Vec<f64> = (0..n)
        .map(|i| v0[i][2].max(v1[i][2]).max(v2[i][2]))
        .collect();

    z_levels
        .par_iter()
        .map(|&z| {
            // Only consider triangles whose Z range spans this layer (fast cull)
            let (sv0, sv1, sv2): (Vec<_>, Vec<_>, Vec<_>) = (0..n)
                .filter(|&i| tri_min_z[i] <= z && tri_max_z[i] >= z)
                .map(|i| (v0[i], v1[i], v2[i]))
                .unzip3_vec();

            let pts = intersect_edges_with_plane(&sv0, &sv1, &sv2, z);
            let pts = sort_and_dedup(pts);
            let xs: Vec<f64> = pts.iter().map(|p| p.0).collect();
            let ys: Vec<f64> = pts.iter().map(|p| p.1).collect();
            (xs, ys)
        })
        .collect()
}

/// Vectorised wave pattern application.
///
/// Returns (mod_x, mod_y): modified perimeter point coordinates.
#[pyfunction]
fn generate_waves(
    pts_x: Vec<f64>,
    pts_y: Vec<f64>,
    amplitude: f64,
    amplitude_factor: f64,
    spacing: f64,
    pattern: &str,
    start_phase: f64,
    phase_offset: f64,
    smoothness: i32,
) -> (Vec<f64>, Vec<f64>) {
    let n = pts_x.len();
    if n < 3 {
        return (pts_x, pts_y);
    }

    let center_x = pts_x.iter().sum::<f64>() / n as f64;
    let center_y = pts_y.iter().sum::<f64>() / n as f64;

    // Arc lengths along perimeter
    let mut arc = vec![0.0_f64; n];
    for i in 1..n {
        let dx = pts_x[i] - pts_x[i - 1];
        let dy = pts_y[i] - pts_y[i - 1];
        arc[i] = arc[i - 1] + (dx * dx + dy * dy).sqrt();
    }

    // Smoothness exponent (maps 1..10 → exp ~2.0..1.0)
    let smooth_exp = if smoothness < 10 {
        2.0 - (smoothness as f64 - 1.0) * (2.0 - 0.2) / 9.0
    } else {
        1.0
    };

    let mut mod_x = pts_x.clone();
    let mut mod_y = pts_y.clone();

    for i in 0..n {
        let phase_deg = ((arc[i] / spacing) * 360.0 + start_phase + phase_offset).rem_euclid(360.0);
        let phase_rad = phase_deg * PI / 180.0;

        let wave_raw = match pattern {
            "sine" => phase_rad.sin(),
            "triangular" => {
                let norm = phase_deg / 360.0;
                if norm < 0.25 {
                    norm * 4.0
                } else if norm < 0.75 {
                    2.0 - norm * 4.0
                } else {
                    norm * 4.0 - 4.0
                }
            }
            "sawtooth" => phase_deg / 360.0 * 2.0 - 1.0,
            _ => phase_rad.sin(),
        };

        let wave_adj = if smoothness < 10 {
            wave_raw.signum() * wave_raw.abs().powf(smooth_exp)
        } else {
            wave_raw
        };

        let wave_offset = (wave_adj + 1.0) * 0.5;
        let applied_amp = amplitude * amplitude_factor * wave_offset;

        let dx = pts_x[i] - center_x;
        let dy = pts_y[i] - center_y;
        let dist = (dx * dx + dy * dy).sqrt();
        if dist > 0.001 {
            mod_x[i] = pts_x[i] + (dx / dist) * applied_amp;
            mod_y[i] = pts_y[i] + (dy / dist) * applied_amp;
        }
    }

    (mod_x, mod_y)
}

/// Parse a binary STL file (bytes object) — returns flat f64 arrays.
///
/// Returns (normals_flat, v0_flat, v1_flat, v2_flat) where each is a
/// flattened list of [x, y, z, x, y, z, ...].
#[pyfunction]
fn parse_binary_stl(data: &[u8]) -> PyResult<(Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>)> {
    if data.len() < 84 {
        return Err(pyo3::exceptions::PyValueError::new_err("STL data too short"));
    }

    let n_tris = u32::from_le_bytes([data[80], data[81], data[82], data[83]]) as usize;
    let expected = 84 + n_tris * 50;
    if data.len() < expected {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("Binary STL truncated: expected {} bytes, got {}", expected, data.len())
        ));
    }

    let mut normals = Vec::with_capacity(n_tris * 3);
    let mut v0 = Vec::with_capacity(n_tris * 3);
    let mut v1 = Vec::with_capacity(n_tris * 3);
    let mut v2 = Vec::with_capacity(n_tris * 3);

    let read_f32 = |offset: usize| -> f64 {
        f32::from_le_bytes([data[offset], data[offset+1], data[offset+2], data[offset+3]]) as f64
    };

    for i in 0..n_tris {
        let base = 84 + i * 50;
        normals.push(read_f32(base));
        normals.push(read_f32(base + 4));
        normals.push(read_f32(base + 8));

        v0.push(read_f32(base + 12));
        v0.push(read_f32(base + 16));
        v0.push(read_f32(base + 20));

        v1.push(read_f32(base + 24));
        v1.push(read_f32(base + 28));
        v1.push(read_f32(base + 32));

        v2.push(read_f32(base + 36));
        v2.push(read_f32(base + 40));
        v2.push(read_f32(base + 44));
    }

    Ok((normals, v0, v1, v2))
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper trait to unzip an iterator of 3-tuples into three Vecs
// ─────────────────────────────────────────────────────────────────────────────

trait Unzip3Vec<A, B, C> {
    fn unzip3_vec(self) -> (Vec<A>, Vec<B>, Vec<C>);
}

impl<I, A, B, C> Unzip3Vec<A, B, C> for I
where
    I: Iterator<Item = (A, B, C)>,
{
    fn unzip3_vec(self) -> (Vec<A>, Vec<B>, Vec<C>) {
        let mut a = Vec::new();
        let mut b = Vec::new();
        let mut c = Vec::new();
        for (x, y, z) in self {
            a.push(x);
            b.push(y);
            c.push(z);
        }
        (a, b, c)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Module registration
// ─────────────────────────────────────────────────────────────────────────────

#[pymodule]
fn slicer_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(slice_at_z, m)?)?;
    m.add_function(wrap_pyfunction!(slice_all_layers, m)?)?;
    m.add_function(wrap_pyfunction!(generate_waves, m)?)?;
    m.add_function(wrap_pyfunction!(parse_binary_stl, m)?)?;
    Ok(())
}
