use std::ptr;

#[repr(C)]
pub struct NativeBuffer {
    pub ptr: *mut u8,
    pub len: usize,
    pub cap: usize,
}

fn store_buffer(bits: Vec<u8>, out: *mut NativeBuffer) -> i32 {
    if out.is_null() {
        return -1;
    }

    let mut bits = bits;
    let buffer = NativeBuffer {
        ptr: bits.as_mut_ptr(),
        len: bits.len(),
        cap: bits.capacity(),
    };
    std::mem::forget(bits);

    unsafe {
        ptr::write(out, buffer);
    }
    0
}

fn input_slice<'a>(intervals: *const u32, len: usize) -> Option<&'a [u32]> {
    if intervals.is_null() {
        return None;
    }
    Some(unsafe { std::slice::from_raw_parts(intervals, len) })
}

#[no_mangle]
pub extern "C" fn fluxctl_free_buffer(ptr: *mut u8, len: usize, cap: usize) {
    if ptr.is_null() {
        return;
    }
    unsafe {
        drop(Vec::from_raw_parts(ptr, len, cap));
    }
}

#[no_mangle]
pub extern "C" fn fluxctl_mfm_intervals_to_bits(
    intervals: *const u32,
    len: usize,
    cell_ns: f64,
    max_cells: usize,
    out: *mut NativeBuffer,
) -> i32 {
    if cell_ns <= 0.0 || max_cells == 0 {
        return -2;
    }
    let Some(intervals) = input_slice(intervals, len) else {
        return -1;
    };

    let mut bits: Vec<u8> = Vec::with_capacity(intervals.len() * 2);
    for &interval in intervals {
        if interval == 0 {
            continue;
        }
        let rounded = ((interval as f64) / cell_ns).round() as isize;
        let cells = rounded.clamp(1, max_cells as isize) as usize;
        if cells > 1 {
            bits.extend(std::iter::repeat(0).take(cells - 1));
        }
        bits.push(1);
    }
    store_buffer(bits, out)
}

fn lowpass_merge(intervals: &[u32], threshold_ns: f64) -> Vec<f64> {
    let mut merged = Vec::with_capacity(intervals.len());
    let mut i = 0;
    while i < intervals.len() {
        let mut interval = intervals[i] as f64;
        if interval < threshold_ns && i + 1 < intervals.len() {
            interval += intervals[i + 1] as f64;
            if let Some(last) = merged.last_mut() {
                *last += interval;
            } else {
                merged.push(interval);
            }
            i += 2;
            continue;
        }
        merged.push(interval);
        i += 1;
    }
    merged
}

#[no_mangle]
pub extern "C" fn fluxctl_gcr_intervals_to_bits(
    intervals: *const u32,
    len: usize,
    cell_ns: f64,
    lowpass_ns: f64,
    out: *mut NativeBuffer,
) -> i32 {
    if cell_ns <= 0.0 {
        return -2;
    }
    let Some(intervals) = input_slice(intervals, len) else {
        return -1;
    };

    let merged = lowpass_merge(intervals, lowpass_ns);
    let clock_min = cell_ns * 0.9;
    let clock_max = cell_ns * 1.1;
    let period_adj = 0.05;
    let phase_adj = 0.60;
    let mut clock = cell_ns;
    let mut phase = 0.0;
    let mut bits: Vec<u8> = Vec::with_capacity(merged.len() * 2);

    for interval in merged {
        if interval <= 0.0 {
            continue;
        }
        phase += interval;
        let cells = ((phase + clock * 0.5) / clock).floor().max(1.0) as usize;
        phase -= (cells as f64) * clock;
        if cells > 1 {
            bits.extend(std::iter::repeat(0).take(cells - 1));
        }
        bits.push(1);
        let measured = interval / (cells as f64);
        let error = measured - clock;
        clock += error * period_adj;
        clock = clock.clamp(clock_min, clock_max);
        phase += error * phase_adj;
    }

    store_buffer(bits, out)
}
