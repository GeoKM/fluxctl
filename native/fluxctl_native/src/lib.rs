use std::ptr;

#[repr(C)]
pub struct NativeBuffer {
    pub ptr: *mut u8,
    pub len: usize,
    pub cap: usize,
}

#[repr(C)]
pub struct NativeU32Buffer {
    pub ptr: *mut u32,
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

fn store_u32_buffer(values: Vec<u32>, out: *mut NativeU32Buffer) -> i32 {
    if out.is_null() {
        return -1;
    }

    let mut values = values;
    let buffer = NativeU32Buffer {
        ptr: values.as_mut_ptr(),
        len: values.len(),
        cap: values.capacity(),
    };
    std::mem::forget(values);

    unsafe {
        ptr::write(out, buffer);
    }
    0
}

fn bits_from_intervals(intervals: &[u32], cell_ns: f64, max_cells: usize) -> Vec<u8> {
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
    bits
}

fn pll_lock_score(intervals: &[u32], cell_ns: f64) -> f64 {
    if intervals.is_empty() {
        return 0.0;
    }

    let mut total_deviation = 0.0;
    let mut count = 0usize;
    for &interval in intervals {
        if interval == 0 {
            continue;
        }
        let cells = ((interval as f64) / cell_ns).round().max(1.0);
        let expected = cells * cell_ns;
        if expected > 0.0 {
            total_deviation += ((interval as f64) - expected).abs() / expected;
            count += 1;
        }
    }
    if count == 0 {
        return 0.0;
    }
    1.0 - (total_deviation / (count as f64)).min(1.0)
}

fn count_sync_word(bits: &[u8]) -> usize {
    const SYNC_WORD: u16 = 0x4489;
    if bits.len() < 16 {
        return 0;
    }

    let mut count = 0usize;
    let mut idx = 0usize;
    while idx + 16 <= bits.len() {
        let mut word = 0u16;
        for offset in 0..16 {
            word = (word << 1) | ((bits[idx + offset] & 1) as u16);
        }
        if word == SYNC_WORD {
            count += 1;
            idx += 16;
        } else {
            idx += 1;
        }
    }
    count
}

fn input_u8_slice<'a>(values: *const u8, len: usize) -> Option<&'a [u8]> {
    if values.is_null() {
        return None;
    }
    Some(unsafe { std::slice::from_raw_parts(values, len) })
}

fn input_slice<'a>(intervals: *const u32, len: usize) -> Option<&'a [u32]> {
    if intervals.is_null() {
        return None;
    }
    Some(unsafe { std::slice::from_raw_parts(intervals, len) })
}

fn crc16(data: &[u8]) -> u16 {
    let mut crc = 0xffffu16;
    for &byte in data {
        crc ^= (byte as u16) << 8;
        for _ in 0..8 {
            if crc & 0x8000 != 0 {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc <<= 1;
            }
        }
    }
    crc
}

fn decode_mfm_data_byte(bits: &[u8], offset: usize) -> Option<u8> {
    if offset + 16 > bits.len() {
        return None;
    }

    let mut value = 0u8;
    for idx in (offset + 1..offset + 16).step_by(2) {
        value = (value << 1) | (bits[idx] & 1);
    }
    Some(value)
}

fn has_sync_word(bits: &[u8], offset: usize) -> bool {
    const SYNC_WORD: u16 = 0x4489;
    if offset + 16 > bits.len() {
        return false;
    }

    let mut word = 0u16;
    for idx in 0..16 {
        word = (word << 1) | ((bits[offset + idx] & 1) as u16);
    }
    word == SYNC_WORD
}

fn append_mfm_sector_record(
    records: &mut Vec<u8>,
    c: u8,
    h: u8,
    r: u8,
    n: u8,
    crc_ok: bool,
    deleted: bool,
    data: &[u8],
) {
    let mut flags = 0u8;
    if crc_ok {
        flags |= 0x01;
    }
    if deleted {
        flags |= 0x02;
    }

    records.extend_from_slice(&[c, h, r, n, flags]);
    records.extend_from_slice(&(data.len() as u32).to_le_bytes());
    records.extend_from_slice(data);
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
pub extern "C" fn fluxctl_free_u32_buffer(ptr: *mut u32, len: usize, cap: usize) {
    if ptr.is_null() {
        return;
    }
    unsafe {
        drop(Vec::from_raw_parts(ptr, len, cap));
    }
}

#[no_mangle]
pub extern "C" fn fluxctl_parse_scp_flux_bytes(
    flux_bytes: *const u8,
    len: usize,
    timebase_ns: f64,
    out: *mut NativeU32Buffer,
) -> i32 {
    if flux_bytes.is_null() || timebase_ns <= 0.0 {
        return -1;
    }

    let data = unsafe { std::slice::from_raw_parts(flux_bytes, len) };
    let word_len = data.len() / 2;
    let mut intervals = Vec::with_capacity(word_len);
    let mut overflow: u64 = 0;

    for chunk in data[..word_len * 2].chunks_exact(2) {
        let tick = u16::from_be_bytes([chunk[0], chunk[1]]) as u64;
        if tick == 0 {
            overflow += 0x10000;
            continue;
        }

        let ticks_total = overflow + tick;
        let interval = ((ticks_total as f64) * timebase_ns).round();
        let interval = interval.clamp(0.0, u32::MAX as f64) as u32;
        intervals.push(interval);
        overflow = 0;
    }

    store_u32_buffer(intervals, out)
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

    let bits = bits_from_intervals(intervals, cell_ns, max_cells);
    store_buffer(bits, out)
}

#[no_mangle]
pub extern "C" fn fluxctl_mfm_decode_best(
    intervals: *const u32,
    len: usize,
    candidates: *const f64,
    candidate_len: usize,
    max_cells: usize,
    out: *mut NativeBuffer,
    out_pll_lock: *mut f64,
    out_sync_count: *mut usize,
) -> i32 {
    if max_cells == 0 || candidates.is_null() || out_pll_lock.is_null() || out_sync_count.is_null()
    {
        return -2;
    }
    let Some(intervals) = input_slice(intervals, len) else {
        return -1;
    };
    let candidates = unsafe { std::slice::from_raw_parts(candidates, candidate_len) };
    if candidates.is_empty() {
        return -2;
    }

    let mut best_bits = Vec::new();
    let mut best_sync = 0usize;
    let mut best_pll = -1.0f64;
    let mut found = false;

    for &cell_ns in candidates {
        if cell_ns <= 0.0 {
            continue;
        }
        let bits = bits_from_intervals(intervals, cell_ns, max_cells);
        let sync_count = count_sync_word(&bits);
        let pll_lock = pll_lock_score(intervals, cell_ns);
        if !found || sync_count > best_sync || (sync_count == best_sync && pll_lock > best_pll) {
            best_bits = bits;
            best_sync = sync_count;
            best_pll = pll_lock;
            found = true;
        }
    }

    if !found {
        return -2;
    }
    unsafe {
        *out_pll_lock = best_pll;
        *out_sync_count = best_sync;
    }
    store_buffer(best_bits, out)
}

#[no_mangle]
pub extern "C" fn fluxctl_mfm_reconstruct_track(
    bits: *const u8,
    len: usize,
    expected_sectors: usize,
    out: *mut NativeBuffer,
    out_weak_count: *mut usize,
) -> i32 {
    if out_weak_count.is_null() {
        return -2;
    }
    let Some(bits) = input_u8_slice(bits, len) else {
        return -1;
    };

    const ID_ADDRESS_MARK: u8 = 0xfe;
    const DATA_MARK: u8 = 0xfb;
    const DELETED_DATA_MARK: u8 = 0xf8;

    let mut records = Vec::new();
    let mut weak = 0usize;
    let mut sector_count = 0usize;
    let mut search_pos = 0usize;
    let mut last_header: Option<(u8, u8, u8, u8, bool)> = None;

    while search_pos + 64 <= bits.len() {
        if expected_sectors > 0 && sector_count >= expected_sectors {
            break;
        }

        let mut pos = search_pos;
        while pos + 16 <= bits.len() && !has_sync_word(bits, pos) {
            pos += 1;
        }
        if pos + 64 > bits.len() {
            break;
        }

        let mut sync_words = 0usize;
        while sync_words < 3 && has_sync_word(bits, pos + sync_words * 16) {
            sync_words += 1;
        }
        if sync_words < 3 {
            search_pos = pos + 1;
            continue;
        }
        sync_words = 3;

        let marker_offset = pos + sync_words * 16;
        let Some(marker) = decode_mfm_data_byte(bits, marker_offset) else {
            break;
        };

        if marker == ID_ADDRESS_MARK {
            let mut header_bytes = [0u8; 4];
            let mut complete = true;
            for (idx, value) in header_bytes.iter_mut().enumerate() {
                if let Some(decoded) = decode_mfm_data_byte(bits, pos + (sync_words + 1 + idx) * 16)
                {
                    *value = decoded;
                } else {
                    complete = false;
                    break;
                }
            }
            if !complete {
                break;
            }

            let Some(crc_hi) = decode_mfm_data_byte(bits, pos + (sync_words + 5) * 16) else {
                break;
            };
            let Some(crc_lo) = decode_mfm_data_byte(bits, pos + (sync_words + 6) * 16) else {
                break;
            };

            let [c, h, r, n] = header_bytes;
            let header_field = [0xa1, 0xa1, 0xa1, marker, c, h, r, n];
            let crc_read = u16::from_be_bytes([crc_hi, crc_lo]);
            last_header = Some((c, h, r, n, crc16(&header_field) == crc_read));
            search_pos = pos + (sync_words + 7) * 16;
            continue;
        }

        if (marker == DATA_MARK || marker == DELETED_DATA_MARK) && last_header.is_some() {
            let (c, h, r, n, header_crc_ok) = last_header.unwrap();
            let Some(data_len) = 128usize.checked_shl(n as u32) else {
                break;
            };
            let data_offset = pos + (sync_words + 1) * 16;
            let Some(data_bits_len) = data_len.checked_mul(16) else {
                break;
            };
            let Some(crc_offset) = data_offset.checked_add(data_bits_len) else {
                break;
            };
            let Some(required_len) = crc_offset.checked_add(32) else {
                break;
            };
            if required_len > bits.len() {
                break;
            }

            let mut data_bytes = Vec::with_capacity(data_len);
            for idx in 0..data_len {
                let Some(value) = decode_mfm_data_byte(bits, data_offset + idx * 16) else {
                    break;
                };
                data_bytes.push(value);
            }
            if data_bytes.len() < data_len {
                break;
            }

            let Some(crc_hi) = decode_mfm_data_byte(bits, crc_offset) else {
                break;
            };
            let Some(crc_lo) = decode_mfm_data_byte(bits, crc_offset + 16) else {
                break;
            };

            let mut data_field = Vec::with_capacity(data_len + 4);
            data_field.extend_from_slice(&[0xa1, 0xa1, 0xa1, marker]);
            data_field.extend_from_slice(&data_bytes);
            let crc_read = u16::from_be_bytes([crc_hi, crc_lo]);
            let crc_ok = crc16(&data_field) == crc_read && header_crc_ok;
            if !crc_ok {
                weak += 1;
            }

            append_mfm_sector_record(
                &mut records,
                c,
                h,
                r,
                n,
                crc_ok,
                marker == DELETED_DATA_MARK,
                &data_bytes,
            );
            sector_count += 1;
            last_header = None;
            search_pos = crc_offset + 32;
            continue;
        }

        search_pos = pos + 1;
    }

    unsafe {
        *out_weak_count = weak;
    }
    store_buffer(records, out)
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

fn is_valid_gcr_symbol(code: u8) -> bool {
    matches!(
        code,
        0b01010
            | 0b01011
            | 0b10010
            | 0b10011
            | 0b01110
            | 0b01111
            | 0b10110
            | 0b10111
            | 0b01001
            | 0b11001
            | 0b11010
            | 0b11011
            | 0b01101
            | 0b11101
            | 0b11110
            | 0b10101
    )
}

#[no_mangle]
pub extern "C" fn fluxctl_gcr_estimate_confidence(
    bits: *const u8,
    len: usize,
    out_confidence: *mut f64,
) -> i32 {
    if bits.is_null() || out_confidence.is_null() {
        return -1;
    }
    let bits = unsafe { std::slice::from_raw_parts(bits, len) };
    if bits.is_empty() {
        unsafe {
            *out_confidence = 0.0;
        }
        return 0;
    }

    let mut valid = 0usize;
    let mut total = 0usize;
    let mut idx = 0usize;
    while idx + 5 <= bits.len() {
        let mut code = 0u8;
        for offset in 0..5 {
            code = (code << 1) | (bits[idx + offset] & 1);
        }
        total += 1;
        if is_valid_gcr_symbol(code) {
            valid += 1;
        }
        idx += 5;
    }

    let ratio = (valid as f64) / (total.max(1) as f64);
    let confidence = if ratio <= 0.6 {
        0.1
    } else {
        ((ratio - 0.6) / 0.4).clamp(0.1, 1.0)
    };
    unsafe {
        *out_confidence = confidence;
    }
    0
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
