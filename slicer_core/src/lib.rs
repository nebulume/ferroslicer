/*!
 * slicer_core — Rust hot paths for MeshyGen slicer.
 *
 * Exposed to Python via PyO3. Provides:
 *   - slice_at_z()                   : Triangle-plane intersection (the main geometry bottleneck)
 *   - slice_all_layers()             : Batch slice every layer in one Rust call (parallelised with rayon)
 *   - generate_waves()               : Vectorised wave-pattern computation
 *   - parse_binary_stl()             : Zero-copy binary STL decoder
 *   - generate_spiral_with_waves()   : Full spiral generation + wave application (replaces Python spiral loop)
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
// Spiral generation helpers (not exported to Python)
// ─────────────────────────────────────────────────────────────────────────────

/// Area-weighted polygon centroid.
fn polygon_centroid(pts_x: &[f64], pts_y: &[f64]) -> (f64, f64) {
    let n = pts_x.len();
    if n == 0 {
        return (0.0, 0.0);
    }
    let mut cx = 0.0f64;
    let mut cy = 0.0f64;
    let mut area_acc = 0.0f64;
    for i in 0..n {
        let x0 = pts_x[i];
        let y0 = pts_y[i];
        let x1 = pts_x[(i + 1) % n];
        let y1 = pts_y[(i + 1) % n];
        let cross = x0 * y1 - x1 * y0;
        area_acc += cross;
        cx += (x0 + x1) * cross;
        cy += (y0 + y1) * cross;
    }
    if area_acc.abs() > 1e-9 {
        let area = area_acc * 0.5;
        cx /= 6.0 * area;
        cy /= 6.0 * area;
    } else {
        cx = pts_x.iter().sum::<f64>() / n as f64;
        cy = pts_y.iter().sum::<f64>() / n as f64;
    }
    (cx, cy)
}

/// Cast a ray from (cx, cy) in direction angle_rad and find the closest
/// intersection with the polygon boundary.  Falls back to nearest perimeter
/// point in the ray direction if no clean intersection is found.
fn get_position_at_angle(
    cx: f64,
    cy: f64,
    pts_x: &[f64],
    pts_y: &[f64],
    angle_rad: f64,
) -> (f64, f64) {
    let n = pts_x.len();
    if n == 0 {
        return (cx, cy);
    }

    let dx = angle_rad.cos();
    let dy = angle_rad.sin();

    // Compute radius bounds for intersection validation
    let mut min_radius = f64::INFINITY;
    let mut max_radius = 0.0f64;
    for i in 0..n {
        let rdx = pts_x[i] - cx;
        let rdy = pts_y[i] - cy;
        let dist = (rdx * rdx + rdy * rdy).sqrt();
        if dist < min_radius {
            min_radius = dist;
        }
        if dist > max_radius {
            max_radius = dist;
        }
    }
    let max_allow_t = max_radius * 1.3;

    let mut closest_t = f64::INFINITY;
    let mut closest_ix = f64::NAN;
    let mut closest_iy = f64::NAN;

    for i in 0..n {
        let x1 = pts_x[i];
        let y1 = pts_y[i];
        let x2 = pts_x[(i + 1) % n];
        let y2 = pts_y[(i + 1) % n];
        let vx = x2 - x1;
        let vy = y2 - y1;

        // Ray vs segment parametric solve
        let denom = dx * (-vy) - dy * (-vx);
        if denom.abs() < 1e-9 {
            continue;
        }
        let rx = x1 - cx;
        let ry = y1 - cy;
        let t = (rx * (-vy) - ry * (-vx)) / denom;
        let s = (dx * ry - dy * rx) / denom;

        if t >= 1e-6 && s >= 0.0 && s <= 1.0 && t <= max_allow_t {
            let ix = cx + t * dx;
            let iy = cy + t * dy;
            let dist_from_center = ((ix - cx).powi(2) + (iy - cy).powi(2)).sqrt();
            if dist_from_center >= min_radius * 0.8
                && dist_from_center <= max_radius * 1.2
                && t < closest_t
            {
                closest_t = t;
                closest_ix = ix;
                closest_iy = iy;
            }
        }
    }

    if !closest_ix.is_nan() {
        return (closest_ix, closest_iy);
    }

    // Fallback: closest perimeter point in ray direction
    let mut best_dist = f64::INFINITY;
    let mut best_x = pts_x[0];
    let mut best_y = pts_y[0];

    for i in 0..n {
        let x1 = pts_x[i];
        let y1 = pts_y[i];
        let x2 = pts_x[(i + 1) % n];
        let y2 = pts_y[(i + 1) % n];
        let vx = x2 - x1;
        let vy = y2 - y1;

        let seg_len_sq = vx * vx + vy * vy;
        let t_seg = if seg_len_sq > 1e-9 {
            let rx2 = cx - x1;
            let ry2 = cy - y1;
            ((rx2 * vx + ry2 * vy) / seg_len_sq).clamp(0.0, 1.0)
        } else {
            0.0
        };

        let px = x1 + t_seg * vx;
        let py = y1 + t_seg * vy;
        let rdx = px - cx;
        let rdy = py - cy;
        let dot = rdx * dx + rdy * dy;
        if dot >= -1e-6 {
            let dist = rdx * rdx + rdy * rdy;
            if dist < best_dist {
                best_dist = dist;
                best_x = px;
                best_y = py;
            }
        }
    }

    (best_x, best_y)
}

/// Interpolate perimeter position between two layers at a given angle.
fn interpolate_position_at_angle(
    angle_deg: f64,
    layer_idx_f: f64,
    layer_pts_x: &[Vec<f64>],
    layer_pts_y: &[Vec<f64>],
    centroids: &[(f64, f64)],
) -> (f64, f64) {
    let num_layers = layer_pts_x.len();
    if num_layers == 0 {
        return (0.0, 0.0);
    }
    let lower_idx = (layer_idx_f.floor() as usize).min(num_layers - 1);
    let upper_idx = (layer_idx_f.ceil() as usize).min(num_layers - 1);
    let t = if upper_idx != lower_idx {
        layer_idx_f - lower_idx as f64
    } else {
        0.0
    };

    let angle_rad = angle_deg * PI / 180.0;
    let (lcx, lcy) = centroids[lower_idx];
    let (lx, ly) = get_position_at_angle(
        lcx, lcy,
        &layer_pts_x[lower_idx],
        &layer_pts_y[lower_idx],
        angle_rad,
    );

    if t == 0.0 {
        return (lx, ly);
    }

    let (ucx, ucy) = centroids[upper_idx];
    let (ux, uy) = get_position_at_angle(
        ucx, ucy,
        &layer_pts_x[upper_idx],
        &layer_pts_y[upper_idx],
        angle_rad,
    );

    (lx + t * (ux - lx), ly + t * (uy - ly))
}

/// Base integrity amplitude factor (replicates BaseIntegrityManager logic).
/// mode: 0=fewer_gaps, 1=tighter_waves, 2=solid_then_mesh
/// transition: 0=linear, 1=exponential(quadratic), 2=step
fn base_integrity_factor(z: f64, base_height: f64, mode: u8, transition: u8) -> f64 {
    if z >= base_height {
        return 1.0;
    }
    let norm = match mode {
        2 => {
            // solid_then_mesh: solid for first half
            let solid_h = base_height / 2.0;
            if z < solid_h {
                return 0.0;
            }
            (z - solid_h) / solid_h
        }
        _ => z / base_height,
    };
    let norm = norm.clamp(0.0, 1.0);
    match transition {
        0 => norm,
        1 => norm * norm,
        2 => {
            let steps = (norm * 5.0) as i32;
            (steps as f64 / 5.0 * 0.2).min(1.0)
        }
        _ => norm,
    }
}

/// Wave value in [-1, 1].
/// pattern: 0=sine, 1=triangular, 2=sawtooth
fn spiral_wave_value(phase_deg: f64, pattern: u8) -> f64 {
    let phase = phase_deg.rem_euclid(360.0);
    match pattern {
        1 => {
            if phase < 180.0 {
                -1.0 + 2.0 * (phase / 180.0)
            } else {
                1.0 - 2.0 * ((phase - 180.0) / 180.0)
            }
        }
        2 => {
            let p180 = phase.rem_euclid(180.0);
            -1.0 + 2.0 * (p180 / 180.0)
        }
        _ => (phase * PI / 180.0).sin(),
    }
}

/// Generate a complete spiral path with wave pattern applied.
///
/// All the heavy lifting that was done in Python SpiralGenerator.generate_spiral_path()
/// + apply_wave_to_spiral() is done here in Rust, parallelised with Rayon.
///
/// Parameters
/// ----------
/// layer_zs           : Z height of each extracted layer
/// layer_pts_x/y      : Perimeter point coordinates per layer (list of lists)
/// layer_height       : Z rise per revolution (mm)
/// points_per_degree  : Initial spiral resolution (points per degree)
/// wave_amplitude     : Max outward wave offset (mm)
/// waves_per_rev      : Number of wave cycles per 360° revolution
/// wave_pattern       : "sine" | "triangular" | "sawtooth"
/// layer_alternation  : Alternate phase every N revolutions (0 = off)
/// phase_offset_pct   : Phase shift on alternating layers, 0-100 (percent of 360°)
/// seam_shift         : Extra fraction-of-wave shift added to cycle length
/// base_height        : Z below which amplitude is ramped (mm)
/// base_mode          : "fewer_gaps" | "tighter_waves" | "solid_then_mesh"
/// base_transition    : "linear" | "exponential" | "step"
/// smoothing_window   : Smoothing kernel half-width (points)
/// smoothing_threshold: Max displacement to apply smoothing (mm)
/// target_samples_pw  : Min samples per wave cycle (anti-alias guard)
/// wave_asymmetry     : Enable asymmetric wave shaping
/// wave_asym_intensity: Asymmetry intensity 0-100
///
/// Returns (xs, ys, zs, angles, revolutions) — flat f64 arrays.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn generate_spiral_with_waves(
    layer_zs: Vec<f64>,
    layer_pts_x: Vec<Vec<f64>>,
    layer_pts_y: Vec<Vec<f64>>,
    layer_height: f64,
    points_per_degree: f64,
    wave_amplitude: f64,
    waves_per_rev: f64,
    wave_pattern: &str,
    layer_alternation: i32,
    phase_offset_pct: f64,
    seam_shift: f64,
    seam_revolution_offset: f64,
    seam_transition_waves: f64,
    base_height: f64,
    base_mode: &str,
    base_transition: &str,
    smoothing_window: i32,
    smoothing_threshold: f64,
    target_samples_per_wave: i32,
    wave_asymmetry: bool,
    wave_asym_intensity: f64,
) -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>) {
    let num_layers = layer_zs.len();
    if num_layers == 0 {
        return (vec![], vec![], vec![], vec![], vec![]);
    }

    // ── encode enums ──────────────────────────────────────────────────────────
    let pat_code: u8 = match wave_pattern {
        "triangular" => 1,
        "sawtooth" => 2,
        _ => 0, // sine
    };
    let mode_code: u8 = match base_mode {
        "tighter_waves" => 1,
        "solid_then_mesh" => 2,
        _ => 0,
    };
    let trans_code: u8 = match base_transition {
        "linear" => 0,
        "step" => 2,
        _ => 1, // exponential (default)
    };

    // ── pre-compute centroids ─────────────────────────────────────────────────
    let centroids: Vec<(f64, f64)> = (0..num_layers)
        .map(|i| polygon_centroid(&layer_pts_x[i], &layer_pts_y[i]))
        .collect();

    // ── determine effective points_per_degree (anti-alias guard) ─────────────
    let min_z = layer_zs[0];
    let max_z = layer_zs[num_layers - 1];
    let total_z = max_z - min_z;
    let num_revolutions = if layer_height > 0.0 {
        total_z / layer_height
    } else {
        1.0
    };

    let effective_ppd = if waves_per_rev > 0.0 && target_samples_per_wave > 0 {
        let required_ppr = (waves_per_rev * target_samples_per_wave as f64).ceil() as usize;
        let min_ppr = (360.0 * points_per_degree).ceil() as usize;
        let ppr = required_ppr.max(min_ppr);
        ppr as f64 / 360.0
    } else {
        points_per_degree
    };

    let ppr = ((360.0 * effective_ppd).round() as usize).max(1);
    let total_points = ((ppr as f64 * num_revolutions).round() as usize).max(1);

    // ── PARALLEL: compute control variables and positions ────────────────────
    // Each point is independent: compute angle/revolution/z/layer_idx/position
    // in parallel across a thread pool.
    let deg_per_pt = 360.0 / ppr as f64;

    // We need layer_pts_x/y/centroids shared across threads.  They are
    // read-only so wrapping in Arc is fine; Rayon requires Send + Sync.
    use std::sync::Arc;
    let lx_arc = Arc::new(layer_pts_x);
    let ly_arc = Arc::new(layer_pts_y);
    let cen_arc = Arc::new(centroids);

    struct PtCtrl {
        angle: f64,
        revolution: f64,
        z: f64,
        layer_idx: f64,
    }

    // Step 1: sequential control-variable computation (very cheap)
    let controls: Vec<PtCtrl> = (0..total_points)
        .map(|idx| {
            let angle = (idx % ppr) as f64 * deg_per_pt;
            let revolution = idx as f64 / ppr as f64;
            let z = min_z + revolution * layer_height;
            let layer_idx = revolution.min((num_layers - 1) as f64);
            PtCtrl { angle, revolution, z, layer_idx }
        })
        .collect();

    // Step 2: parallel position computation (expensive: ray-polygon per point)
    let positions: Vec<(f64, f64)> = controls
        .par_iter()
        .map(|c| {
            interpolate_position_at_angle(
                c.angle,
                c.layer_idx,
                &lx_arc,
                &ly_arc,
                &cen_arc,
            )
        })
        .collect();

    // ── SEQUENTIAL: smoothing ─────────────────────────────────────────────────
    let n = total_points;
    let win = smoothing_window.max(1) as usize;
    let half_win = win / 2;

    // Pass 1: outlier rejection (if both neighbours jump > 2mm, interpolate)
    let mut px: Vec<f64> = positions.iter().map(|p| p.0).collect();
    let mut py: Vec<f64> = positions.iter().map(|p| p.1).collect();

    {
        let ox = px.clone();
        let oy = py.clone();
        for i in 0..n {
            if i == 0 {
                continue;
            }
            let prev = i - 1;
            let next = (i + 1) % n;
            let d_prev = {
                let ddx = ox[i] - ox[prev];
                let ddy = oy[i] - oy[prev];
                (ddx * ddx + ddy * ddy).sqrt()
            };
            let d_next = {
                let ddx = ox[next] - ox[i];
                let ddy = oy[next] - oy[i];
                (ddx * ddx + ddy * ddy).sqrt()
            };
            if d_prev > 2.0 && d_next > 2.0 {
                px[i] = (ox[prev] + ox[next]) / 2.0;
                py[i] = (oy[prev] + oy[next]) / 2.0;
            }
        }
    }

    // Pass 2: weighted average smoothing
    if win > 1 {
        let center_w = 0.6f64;
        let side_w = if win > 1 {
            (1.0 - center_w) / (win - 1) as f64
        } else {
            0.0
        };
        // Build weight array: [side_w, ..., center_w, ..., side_w]
        let weights: Vec<f64> = (0..=2 * half_win)
            .map(|k| if k == half_win { center_w } else { side_w })
            .collect();
        let w_sum: f64 = weights.iter().sum();

        let sx = px.clone();
        let sy = py.clone();
        for i in 0..n {
            let mut ax = 0.0f64;
            let mut ay = 0.0f64;
            for (k, &w) in weights.iter().enumerate() {
                let j = (i + k + n - half_win) % n;
                ax += sx[j] * w;
                ay += sy[j] * w;
            }
            ax /= w_sum;
            ay /= w_sum;
            let mv = ((ax - px[i]).powi(2) + (ay - py[i]).powi(2)).sqrt();
            if mv < smoothing_threshold {
                px[i] = ax;
                py[i] = ay;
            }
        }
    }

    // ── PARALLEL: wave application ───────────────────────────────────────────
    // Compute centroid for each point's layer (interpolated between layers)
    // Then apply radial wave offset.
    let cycle_len_revs = if layer_alternation > 0 && phase_offset_pct > 0.0 {
        let base = layer_alternation as f64;
        let extra = if seam_shift != 0.0 && waves_per_rev > 0.0 {
            seam_shift / waves_per_rev
        } else {
            0.0
        };
        base + extra
    } else {
        0.0
    };

    // Parallel wave application: each point is independent
    struct WaveInput {
        x: f64,
        y: f64,
        z: f64,
        angle: f64,
        revolution: f64,
        layer_idx: f64,
    }

    let wave_inputs: Vec<WaveInput> = (0..n)
        .map(|i| WaveInput {
            x: px[i],
            y: py[i],
            z: controls[i].z,
            angle: controls[i].angle,
            revolution: controls[i].revolution,
            layer_idx: controls[i].layer_idx,
        })
        .collect();

    // We need centroids accessible in the parallel closure
    // cen_arc still holds the Arc<Vec<(f64,f64)>>
    let cen_for_wave = Arc::clone(&cen_arc);

    let results: Vec<(f64, f64)> = wave_inputs
        .par_iter()
        .map(|wi| {
            if wave_amplitude <= 0.0 {
                return (wi.x, wi.y);
            }

            // Wave value with phase alternation and optional seam crossfade.
            // We blend wave VALUES (not phase constants) so the mesh pattern
            // fades to zero amplitude then rebuilds in the opposite phase —
            // a true crossfade rather than a phase slide.
            let base_phase = (wi.angle * waves_per_rev).rem_euclid(360.0);
            let wave_raw = if cycle_len_revs > 0.0 {
                let adjusted_rev = wi.revolution + seam_revolution_offset;
                let cycle = (adjusted_rev / cycle_len_revs) as i64;
                let cur_shift = (cycle % 2) as f64 * (phase_offset_pct / 100.0) * 360.0;
                let cur_wave = spiral_wave_value((base_phase + cur_shift).rem_euclid(360.0), pat_code);
                if seam_transition_waves > 0.0 && waves_per_rev > 0.0 {
                    let cycle_progress = (adjusted_rev / cycle_len_revs).rem_euclid(1.0);
                    let transition_frac = seam_transition_waves / (waves_per_rev * cycle_len_revs);
                    if transition_frac > 0.0 && cycle_progress > 1.0 - transition_frac {
                        let nxt_shift = ((cycle + 1) % 2) as f64 * (phase_offset_pct / 100.0) * 360.0;
                        let nxt_wave = spiral_wave_value((base_phase + nxt_shift).rem_euclid(360.0), pat_code);
                        let t = (cycle_progress - (1.0 - transition_frac)) / transition_frac;
                        let t_smooth = 0.5 - 0.5 * (t * std::f64::consts::PI).cos();
                        cur_wave * (1.0 - t_smooth) + nxt_wave * t_smooth
                    } else {
                        cur_wave
                    }
                } else {
                    cur_wave
                }
            } else {
                spiral_wave_value(base_phase, pat_code)
            };

            // Amplitude factor from base integrity
            let amp_factor = base_integrity_factor(wi.z, base_height, mode_code, trans_code);

            // Wave normalisation (outward-only)
            let wave_norm = if wave_asymmetry {
                let blend = wave_asym_intensity / 100.0;
                let sym = (wave_raw + 1.0) * 0.5;
                let asym = if wave_raw < 0.0 {
                    (wave_raw + 1.0) * 0.5 * (2.0 - blend)
                } else {
                    0.5 + (wave_raw * 0.5) / (2.0 - blend)
                };
                (sym * (1.0 - blend) + asym * blend).clamp(0.0, 1.0)
            } else {
                (wave_raw + 1.0) * 0.5
            };

            let offset_mag = wave_norm * wave_amplitude * amp_factor;

            // Interpolate centroid for this layer fraction
            let lower_idx = (wi.layer_idx.floor() as usize).min(num_layers - 1);
            let upper_idx = (wi.layer_idx.ceil() as usize).min(num_layers - 1);
            let t = if upper_idx != lower_idx {
                wi.layer_idx - lower_idx as f64
            } else {
                0.0
            };
            let (lcx, lcy) = cen_for_wave[lower_idx];
            let (ucx, ucy) = cen_for_wave[upper_idx];
            let cx = lcx + t * (ucx - lcx);
            let cy = lcy + t * (ucy - lcy);

            // Radial outward displacement
            let rdx = wi.x - cx;
            let rdy = wi.y - cy;
            let mag = (rdx * rdx + rdy * rdy).sqrt();
            if mag > 1e-9 {
                (wi.x + (rdx / mag) * offset_mag, wi.y + (rdy / mag) * offset_mag)
            } else {
                (wi.x, wi.y)
            }
        })
        .collect();

    // ── Collect outputs ───────────────────────────────────────────────────────
    let xs: Vec<f64> = results.iter().map(|r| r.0).collect();
    let ys: Vec<f64> = results.iter().map(|r| r.1).collect();
    let zs: Vec<f64> = controls.iter().map(|c| c.z).collect();
    let angles: Vec<f64> = controls.iter().map(|c| c.angle).collect();
    let revs: Vec<f64> = controls.iter().map(|c| c.revolution).collect();

    (xs, ys, zs, angles, revs)
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
    m.add_function(wrap_pyfunction!(generate_spiral_with_waves, m)?)?;
    Ok(())
}
