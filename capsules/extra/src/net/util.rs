// Licensed under the Apache License, Version 2.0 or the MIT License.
// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright Tock Contributors 2022.

//! Utility functions used in the 6LoWPAN implementation

/// Verifies that a prefix given in the form of a byte array slice is valid with
/// respect to its length in bits (prefix_len):
///
/// - The byte array slice must contain enough bytes to cover the prefix length
/// (no implicit zero-padding)
/// - The rest of the prefix array slice is zero-padded
pub fn verify_prefix_len(prefix: &[u8], prefix_len: u8) -> bool {
    let full_bytes = (prefix_len / 8) as usize;
    let remaining_bits = prefix_len % 8;
    // NO_PANIC_EDIT: explicit branch (replacing `usize::from(remaining_bits != 0)`)
    // so both Flux and LLVM can track bytes = full_bytes + 1 in the
    // remaining_bits != 0 branch, making prefix[full_bytes] provably in-bounds.
    let bytes = if remaining_bits != 0 {
        full_bytes + 1
    } else {
        full_bytes
    };

    if bytes > prefix.len() {
        return false;
    }

    // The bits between the prefix's end and the next byte boundary must be 0
    if remaining_bits != 0 {
        let last_byte_mask = 0xff >> remaining_bits;
        // NO_PANIC_EDIT: bytes = full_bytes + 1 (explicit branch above) and
        // bytes <= prefix.len() (guard above), so full_bytes < prefix.len().
        if prefix[full_bytes] & last_byte_mask != 0 {
            return false;
        }
    }

    // Ensure that the remaining bytes are also 0
    prefix[bytes..].iter().all(|&b| b == 0)
}

/// Verifies that the prefixes of the two buffers match, where the length of the
/// prefix is given in bits
pub fn matches_prefix(buf1: &[u8], buf2: &[u8], prefix_len: u8) -> bool {
    let full_bytes = (prefix_len / 8) as usize;
    let remaining_bits = prefix_len % 8;
    // NO_PANIC_EDIT: explicit branch so Flux can track bytes = full_bytes + 1
    // in the remaining_bits != 0 case, making buf1/buf2[full_bytes] provably safe.
    let bytes = if remaining_bits != 0 {
        full_bytes + 1
    } else {
        full_bytes
    };

    if bytes > buf1.len() || bytes > buf2.len() {
        return false;
    }

    // Ensure that the prefix bits in the last byte match
    if remaining_bits != 0 {
        let last_byte_mask = 0xff << (8 - remaining_bits);
        // NO_PANIC_EDIT: bytes = full_bytes + 1 (explicit branch above) and
        // bytes <= buf1.len() and bytes <= buf2.len() (guard above),
        // so full_bytes < buf1.len() and full_bytes < buf2.len().
        if (buf1[full_bytes] ^ buf2[full_bytes]) & last_byte_mask != 0 {
            return false;
        }
    }

    // Ensure that the prefix bytes before that match
    // FLUX-TODO addr=0xa496 line=72 flavor=slice_end
    flux_support::assert(full_bytes <= buf1.len() && full_bytes <= buf2.len());
    buf1[..full_bytes].iter().eq(buf2[..full_bytes].iter())
}

// When reading from a buffer in network order
#[flux_rs::sig(fn(buf: &[u8][@n]) -> u16 requires n >= 2)]
pub fn network_slice_to_u16(buf: &[u8]) -> u16 {
    ((buf[0] as u16) << 8) | (buf[1] as u16)
}

// When reading from a buffer in host order
#[flux_rs::sig(fn(buf: &[u8][@n]) -> u16 requires n >= 2)]
pub fn host_slice_to_u16(buf: &[u8]) -> u16 {
    ((buf[1] as u16) << 8) | (buf[0] as u16)
}

#[flux_rs::sig(fn(short: u16, slice: &mut [u8][@n]) requires n >= 2)]
pub fn u16_to_network_slice(short: u16, slice: &mut [u8]) {
    slice[0] = (short >> 8) as u8;
    slice[1] = (short & 0xff) as u8;
}
